# mr-load — Plan Assessment & Runbook

Assessment date: 2026-08-13. Every claim below was verified against primary documentation
(rclone.org, cloud.google.com, docs.digitalocean.com) or tested live in the working
environment on this date. Nothing here is from memory alone.

## 0. Ground truth established in the working environment

| Fact | Status |
|---|---|
| Google Drive access (MCP, read) | **LIVE** — library root located: `2023 Luxtelligence`, folder id `14Z0pozhG4TARz-8YspU8YATW4UND_Llr` (subfolders `01 LN4BB-PDK01`, `02 Sample GDS`, `Submissions`, `Luceda PDK`, `LXTLN4002_11 - Miraex`; mixed `.gds`/`.pdf`/`.docx`/`.zip` binaries + native Google Docs/Slides/Sheets) |
| rclone | **WORKS** — v1.75.0 downloaded and executed over the HTTPS proxy |
| python3 + pip | **WORKS** (pypi reachable directly) |
| Outbound raw TCP **port 22** | **BLOCKED** — egress is HTTPS-CONNECT-proxy only. Classic `ssh`/`scp`/paramiko to any cloud VM is dead *from this container* (fine from a local IDE) |
| gcloud / doctl / ansible / ssh binaries | not preinstalled; all installable over HTTPS |
| Git repo | `wkayaobama/mir-load`, branch `claude/mr-load-library-system-dphpbj` |

## 1. Per-component likelihood of success

| # | Plan component | Likelihood | Verdict & evidence |
|---|---|---|---|
| 1 | rclone Drive flags: `--fast-list` (50 parent filters/batch, ~20x, Google-bug workaround) | **HIGH** | Confirmed verbatim in rclone Drive docs; the bug workaround is `--drive-fast-list-bug-fix`, default ON |
| 2 | `--checksum` Drive↔GCS via MD5 both sides | **HIGH** (binaries) / **N/A** (native gdocs) | Drive: md5/sha1/sha256; GCS: md5 native. **Native Google Docs/Sheets have no size (-1) or hash** — they transfer fine but can never be checksum-verified. Use `--drive-skip-gdocs` for the verifiable pass |
| 3 | `--track-renames` → server-side ops | **HIGH** | Confirmed. GCS supports server-side Copy (not Move) — requirement met; falls back with an ERROR log if not |
| 4 | `--drive-export-formats csv,docx`, Sheets→CSV = first tab only | **HIGH mechanism / LOSSY choice** | Mechanism confirmed. First-tab-only is documented by **Google's** export-formats reference (not rclone's docs). Multi-tab spreadsheets silently lose tabs 2+ — decision required (see §3.2) |
| 5 | `[drive]` SA key + `drive.readonly` + `root_folder_id` / `team_drive`; share-to-SA is the silent-failure step | **HIGH** | All confirmed in rclone docs, including the documented failure mode when the folder isn't shared with the SA email |
| 6 | `[gcs]` `env_auth` + `bucket_policy_only` + `directory_markers` | **HIGH** | All three confirmed verbatim. `env_auth` = keyless on a GCE VM with attached SA |
| 7 | Cloud Shell as ansible-style control host | **HIGH (bootstrap only) / LOW (unattended)** | gcloud preauthenticated, 5 GB persistent $HOME — good interactive seat. But "intended for interactive use only": 40–60 min inactivity kill, 12 h session cap, 50 h/week quota, $HOME deleted after 120 days idle. Never the scheduler |
| 8 | Barebone GCE VM + cron running the identical core | **HIGH** | `--metadata-from-file startup-script=` runs as root at boot, installs + schedules everything, **zero SSH ever needed**. Pure HTTPS API call to create |
| 9 | paramiko/SSH channel (stdin/stdout/stderr bridge) | **HIGH from local IDE / DEAD from this container** | Port 22 blocked here. Verified HTTPS-native substitute: **IAP TCP forwarding** (`gcloud compute ssh --tunnel-through-iap`) — SSH inside a WebSocket to `tunnel.cloudproxy.app:443`, works through CONNECT proxies; set `core/custom_ca_certs_file=/root/.ccr/ca-bundle.crt` |
| 10 | DigitalOcean droplet as host | **MEDIUM-LOW** | Creation = pure HTTPS API ✓. But DO has **no IAP/SSM equivalent** — no API command execution, web console is UI-only. Only bootstrap channel is cloud-init `user_data` (≤64 KiB, immutable, one-shot). And on DO there is no keyless GCP auth: the SA JSON key must be baked in — the exact pattern the plan tries to avoid. See decision §3.1 |
| 11 | "scp to finalize the transfer towards BigQuery" | **ZERO as stated** | BigQuery is an API service with no filesystem; scp/SSH play no role, confirmed against Google's batch-loading docs. Correct paths exist and are trivial — see §2, phase 5 |
| 12 | Replicate folder structure locally, path-as-hierarchical-index + FK to entity | **HIGH** | `rclone lsjson -R --hash` emits the entire tree (path, file id, md5, size, mimetype) in one call — the index and the clone come from the same tool, so the manifest is consistent with the copied bytes. Caveat: Drive permits duplicate names in one folder → run `rclone dedupe` first, and key the index on `file_id`, with path as attribute |

Additional findings from the verification pass (not in the original plan, worth knowing):

- **rclone's shared OAuth client_id is being retired during 2026** — creating your own client_id is now mandatory-ish, and gives you your own quota (default ~10 tx/s per client_id).
- Drive rate-limits rclone to roughly **2 files/second** regardless of bandwidth — sizing input for the cron interval.
- With `sync` + `--fast-list` on shared drives, recently-uploaded files can be missing from listings for up to ~1 h (Google-side caching) → spurious re-copies; harmless with `--checksum` but expect log noise.
- BigQuery can query **Drive directly** via external tables (CSV, JSON, Avro, Google Sheets; Drive share-URL as the URI, wildcards NOT supported, needs the `https://www.googleapis.com/auth/drive` scope, e.g. `gcloud auth login --enable-gdrive-access`) — an optional shortcut for tabular assets that skips the GCS hop.

## 2. Runbook — sequential commands per phase

### Phase 0 — Local IDE dependencies (your machine, not the CCR container)

```bash
python3 -m pip install paramiko google-auth google-api-python-client google-cloud-storage
# paramiko is only useful from your machine / Cloud Shell: this container cannot open TCP/22
```

### Phase 1 — GCP bootstrap (interactive seat: Cloud Shell, gcloud preauthenticated)

```bash
gcloud config set project <PROJECT_ID>

gcloud services enable drive.googleapis.com storage.googleapis.com \
  bigquery.googleapis.com compute.googleapis.com iap.googleapis.com

# Runner identity
gcloud iam service-accounts create mr-load-runner --display-name="mr-load runner"

gcloud projects add-iam-policy-binding <PROJECT_ID> \
  --member="serviceAccount:mr-load-runner@<PROJECT_ID>.iam.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"
gcloud projects add-iam-policy-binding <PROJECT_ID> \
  --member="serviceAccount:mr-load-runner@<PROJECT_ID>.iam.gserviceaccount.com" \
  --role="roles/bigquery.dataEditor"
gcloud projects add-iam-policy-binding <PROJECT_ID> \
  --member="serviceAccount:mr-load-runner@<PROJECT_ID>.iam.gserviceaccount.com" \
  --role="roles/bigquery.jobUser"

# JSON key — needed for the DRIVE side only (GCS/BQ are keyless on the VM via env_auth).
# Domain-wide delegation is NOT available on a personal gmail account, so share-to-SA
# + key file IS the primary Drive path here, not the fallback.
gcloud iam service-accounts keys create mr-load-sa.json \
  --iam-account=mr-load-runner@<PROJECT_ID>.iam.gserviceaccount.com

# Target bucket, UBLA on (this is why bucket_policy_only is required in rclone.conf)
gcloud storage buckets create gs://mr-load-library \
  --location=<REGION> --uniform-bucket-level-access
```

**Manual step — the silent-failure step, exactly as the plan predicted:** in the Drive UI,
share the library root folder (`2023 Luxtelligence`) with
`mr-load-runner@<PROJECT_ID>.iam.gserviceaccount.com` (Viewer). Without this, rclone sees
an empty remote and errors nothing.

### Phase 2 — `rclone.conf` (declarative; byte-identical on every chassis)

```ini
[drive]
type = drive
scope = drive.readonly
service_account_file = /opt/mr-load/mr-load-sa.json
root_folder_id = 14Z0pozhG4TARz-8YspU8YATW4UND_Llr
# team_drive = <ID>   # only if the library moves to a Shared Drive

[gcs]
type = google cloud storage
env_auth = true
bucket_policy_only = true
directory_markers = true
```

### Phase 3 — `sync.sh` (the identical core)

```bash
#!/usr/bin/env bash
set -euo pipefail
export RCLONE_CONFIG=/opt/mr-load/rclone.conf

# 0. one-time before first sync: Drive allows duplicate names; path-index needs uniqueness
#    rclone dedupe --dedupe-mode newest drive:

# 1. THE HIERARCHICAL INDEX — full tree with path, drive file id, md5, size, mimetype
rclone lsjson -R --hash drive: > /opt/mr-load/manifest.json

# 2. THE CLONE  (flags verified 2026-08-13 against rclone.org)
#    WARNING on csv: Sheets->CSV exports the FIRST TAB ONLY (Google-documented).
#    Lossless alternative: --drive-export-formats xlsx,docx  (convert downstream).
rclone sync drive: gcs:mr-load-library/library \
  --fast-list \
  --checksum \
  --track-renames \
  --drive-export-formats csv,docx \
  --transfers 8 --checkers 16 \
  --log-file /var/log/mr-load-sync.log --log-level INFO

# 3. VERIFY — binaries only; native gdocs have no size/hash and cannot be verified
rclone check drive: gcs:mr-load-library/library \
  --checksum --drive-skip-gdocs --one-way \
  --log-file /var/log/mr-load-check.log || true

# 4. publish index next to the data
rclone copyto /opt/mr-load/manifest.json gcs:mr-load-library/index/manifest.json
```

Note: rclone manages its own transfer concurrency (`--transfers`/`--checkers`) — the plan's
instinct that no extra threading layer is needed is correct (it's rclone doing this, not rsync;
rsync never touches Drive/GCS in this architecture).

### Phase 4 — `deploy.sh` (provision the ephemeral runner; NO SSH required)

```bash
#!/usr/bin/env bash
set -euo pipefail
PROJECT=<PROJECT_ID>; ZONE=<ZONE>

# stage payload where the startup script can fetch it (keeps startup-script tiny)
gcloud storage cp rclone.conf sync.sh mr-load-sa.json gs://mr-load-library/bootstrap/

gcloud compute instances create mr-load-runner-vm \
  --project="$PROJECT" --zone="$ZONE" \
  --machine-type=e2-small \
  --service-account="mr-load-runner@${PROJECT}.iam.gserviceaccount.com" \
  --scopes=cloud-platform \
  --metadata-from-file startup-script=startup.sh
```

`startup.sh` (runs as root at boot — this replaces the entire ansible playbook for one host):

```bash
#!/usr/bin/env bash
set -euo pipefail
mkdir -p /opt/mr-load
curl -fsSL https://rclone.org/install.sh | bash          # installs rclone
gcloud storage cp gs://mr-load-library/bootstrap/rclone.conf   /opt/mr-load/
gcloud storage cp gs://mr-load-library/bootstrap/mr-load-sa.json /opt/mr-load/
gcloud storage cp gs://mr-load-library/bootstrap/sync.sh       /opt/mr-load/
chmod 700 /opt/mr-load/sync.sh
echo '0 */6 * * * root /opt/mr-load/sync.sh' > /etc/cron.d/mr-load
/opt/mr-load/sync.sh   # first run at boot
```

Optional live shell into the VM **from HTTPS-only environments** (verified viable — SSH inside
a WebSocket on 443, no port 22 involved):

```bash
gcloud compute firewall-rules create allow-iap-ssh \
  --direction=INGRESS --action=ALLOW --rules=tcp:22 --source-ranges=35.235.240.0/20
gcloud config set core/custom_ca_certs_file /root/.ccr/ca-bundle.crt   # CCR container only
gcloud compute ssh mr-load-runner-vm --zone=<ZONE> --tunnel-through-iap
```

### Phase 5 — Finalize toward BigQuery (replaces the scp step — scp has no role here)

```bash
bq mk --dataset <PROJECT_ID>:mrload

# 5a. the hierarchical index itself (build library_index.csv from manifest.json
#     with the python wrapper: file_id, path, parent_path, depth, name, md5, size,
#     mimetype, entity_fk — entity_fk resolved from the top-level path segment)
bq load --autodetect --source_format=CSV \
  mrload.library_index gs://mr-load-library/index/library_index.csv

# 5b. tabular assets — one load per logical table (heterogeneous CSVs can't share one table).
#     Wildcard rule: a SINGLE asterisk only, and it matches across folder boundaries:
bq load --autodetect --source_format=CSV \
  mrload.<table> 'gs://mr-load-library/library/<prefix>/*.csv'

# 5c. optional GCS-skipping shortcut for live Sheets (no wildcards, one file per URI):
gcloud auth login --enable-gdrive-access   # Drive scope for the querying credential
bq mk --external_table_definition=@GOOGLE_SHEETS=<drive-share-url> mrload.<sheet_table>
```

### Phase 6 — DigitalOcean droplet (as specified; kept pending decision §3.1)

```bash
# creation is pure HTTPS API — works from anywhere
curl -sS -X POST https://api.digitalocean.com/v2/droplets \
  -H "Authorization: Bearer $DO_TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"mr-load-do","region":"fra1","size":"s-1vcpu-1gb","image":"ubuntu-24-04-x64",
       "user_data":"#cloud-config\nruncmd:\n  - curl -fsSL https://rclone.org/install.sh | bash\n  - ..."}'
```

Verified constraints: `user_data` ≤ 64 KiB, immutable after creation, and **DO has no
API/HTTPS command channel into a running droplet** (no IAP/SSM equivalent; web console is
UI-only). All automation must be front-loaded into cloud-init, or the droplet must poll
outward over HTTPS. The Drive SA key must also be embedded (no keyless GCP auth off-GCP).

## 3. Decisions required from the operator (not taken unilaterally)

1. **Chassis:** consolidate on GCE-only, or keep the DO droplet as second chassis?
   Recommendation: GCE-only — keyless GCS/BQ auth, IAP shell reachable from HTTPS-only
   environments, startup-script ≤256 KB vs cloud-init ≤64 KiB, one cloud fewer. The rclone.conf
   stays byte-identical either way, so the second chassis can be added later at zero design cost.
2. **Sheets export:** keep `csv` (first tab only — silent loss on multi-tab spreadsheets) or
   switch to `xlsx` (lossless, convert per-tab downstream)?
3. **Index destination:** confirm the index lands as `mrload.library_index` in BigQuery
   (mirroring the icalps pattern), and provide the entity-FK mapping rule
   (top-level folder name → entity id).
4. **Library root:** confirm `2023 Luxtelligence` (`14Z0pozhG4TARz-8YspU8YATW4UND_Llr`) is the
   root to index, vs its parent folder.
