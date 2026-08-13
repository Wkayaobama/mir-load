# mr-load — Inferred Library Datastructure

Methodical study of the Miraex Drive hierarchy (live enumeration, 2026-08-13)
and the derivation rules the walker implements. Companion to
`pipeline/library_files/` and to `docs/PLAN_ASSESSMENT.md`.

## 1. Observed dialects

Two structural dialects coexist in the trees to be indexed:

### Segment dialect — `<taxonomy>/<segment>/<company>/...`

Observed under `20 Opportunities and customer data` (Drive id
`1If3SX0GD6FJypMy23na6HizFD6gciw_0`) → `Quantum`
(`1wNn20ijHfDJjUbdssQUdMYWhtEUGpwLq`), whose children are company-named
folders: Thorlabs, Toshiba, Alice & Bob, DSO, MEMQ, Novalink, Bluefors,
Pixel Photonics, Quantinuum, IQM, IonQ, CERN, NVidia, Cisco, Fujitsu, …
plus occasional loose files ("251226 Miraex Lead Opportunities.xlsx").

A company folder is itself a library record (the legacy example row for
Thorlabs points `legacy_file_link` at the *folder* URL), and every file
beneath it inherits the company candidate.

### Engagement dialect — `<year>[_ ]<counterparty/program>`

Observed under the deals root (`1VM3c4ly7s3XUVY0xe5Dy_XaaGsOaKGKA`):
`2026_Thales`, `2026 Light Trace`, `2025 IBM`, `2025_QPHOX`, `2024_Nokia`,
`2023_CSEM MPW`, `2023 Luxtelligence`, `2026 flagship armasuisse
innosuisse`, … Year prefix + remainder make the *deal* candidate; the
de-yeared remainder is the *company* candidate.

## 2. Path-code / id derivation

The legacy index encodes taxonomy ordinals into ids — example row:
path `30 Sales/20 opportunities and customer data /quantum` →
`legacy_library_id = 3020Q`. Rule, replicated in `walker.segment_code`:

- segment starts with digits → contribute the digits (`30 Sales` → `30`)
- otherwise → first alphanumeric, uppercased (`quantum` → `Q`)
- concatenate along the parent path (`3020Q`)

The bare code is **not unique across siblings** (Thorlabs and Toshiba
would both sit under `3020Q`), so the default id scheme is
`pathcode-hash`: `3020Q-<sha1(full_path)[:8]>`. `--id-scheme pathcode`
reproduces the bare legacy code; `--id-scheme hash` mirrors ic-load's
synthetic `fs:<sha1[:12]>` ids (`dr:` prefix here).

## 3. Classification grammar (walker)

For a folder at depth *d* (1-based, within the walked root), first match
wins:

1. `^((19|20)\d{2})[\s_-]+rest` → **engagement_folder**; deal = full name,
   year = the prefix, company = rest (unless inherited).
2. `^\d+\s` → **taxonomy** (numbered structural folder).
3. *d* ≤ `--segment-depth` (default 1) and no company inherited →
   **segment** (e.g. Quantum).
4. No company inherited yet → **company_folder**; company = folder name.
5. Otherwise → **document** folder.

Files are always **document** leaves; both inherit segment/company/deal
candidates from the nearest classified ancestors. Classification lands in
`libr_category`, node kind in `libr_type` (`folder`/`file`).

## 4. Column contract

First 19 columns are drop-in parity with the icalps silver table
(`stg_library_normalised` lineage), owner-prefix configurable
(`--owner-prefix icalps` for byte-parity, default `mirx`):

```
legacy_library_id  legacy_company_id  legacy_contact_id  legacy_deal_id
legacy_case_id     legacy_file_path   legacy_file_name   legacy_file_link
libr_note  libr_type  libr_category  libr_status
libr_created_by  libr_updated_by  libr_created_at  libr_updated_at
mirx_owner_email  mirx_owner_fullname  loaded_at
```

The `legacy_*_id` FK columns are emitted **empty by design** — the CRM
destination is virtual at index time. The walker's candidates travel in
trailing columns the associativity layer joins on to fill them:

```
inferred_segment  inferred_company_name  inferred_deal_name  inferred_year
path_code  depth  drive_file_id  drive_md5  drive_size  drive_mimetype
```

Deliberate divergence from ic-load, stated openly: the silver
**at-least-one-FK filter is not applied** here (it would drop every row
before association). `--require-inference` approximates it by dropping
rows with neither company nor deal candidate.

## 5. Upstream associativity (HubSpot)

The intended join path, mirroring ic-load's `overrides.SandboxOverrideMap`
resolution stage:

1. `bq load` the silver CSV into `mrload.library_index`
   (`pipeline/library_files/sql/library_index.schema.json`).
2. Resolve `inferred_company_name` → HubSpot company id (name-normalised
   match against the synced companies table; ic-load's
   `text_normalization` conventions apply).
3. Resolve `inferred_deal_name`/`inferred_year` → HubSpot deal id.
4. Write resolutions back into `legacy_company_id`/`legacy_deal_id`, at
   which point the table satisfies the original icalps silver contract and
   the ic-load uploader pattern (two-phase upload + note-attach with
   idempotency ledger) applies unchanged.

## 6. Bronze provenance

Bronze = `rclone lsjson -R --hash drive:` over the same remote the sync
copies, so index and clone cannot drift. Fields not present in lsjson
(`createdTime`, owner) stay NULL / config-supplied until a Drive API
enrichment pass; `legacy_file_link` is reconstructed from the Drive id in
the same URL forms the legacy index stored.
