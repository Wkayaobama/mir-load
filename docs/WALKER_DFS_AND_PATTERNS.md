# The walker: depth-first traversal, classification grammar, exclusion patterns, and the deal layer

Reference for `pipeline/library_files/drive_walker.py` (the traversal),
`pipeline/library_files/walker.py` (the classification), `context/cards/library.yaml`
(every pattern the two obey) and the silver models that consume the result. Written on the
side branch `walker-deal-depth3`; it describes the code as merged, not a plan.

## 1. Two stages, on purpose

| stage | function | what it knows | what it decides |
|---|---|---|---|
| 1 traversal | `dfs_entries` | the Drive API, one folder at a time | which nodes exist, in which order, with which metadata |
| 2 classification | `DriveTreeWalker.walk` | the path of a node and what its ancestors established | category, keys, inherited anchors, asset class |

Stage 1 never classifies; stage 2 never calls Drive. Stage 2 can therefore be replayed from
any manifest (the offline `runner index` path, the fixtures in `tests/`), and the rehearsal
runs stage 1 against a local Drive mock with the real client code.

## 2. Stage 1 — the depth-first traversal

A recursive **pre-order** walk from the scope root folder declared in the card
(`scope.roots[0].drive_id`).

For each folder:

1. One `files.list` call with `q = '<folder id>' in parents and trashed = false`, page size
   1000, `supportsAllDrives = true`, fields limited to what the index stores (id, name, mime,
   size, md5, times, owners, parents, webViewLink, shortcut target). A folder with more than
   1000 children costs one extra call per extra page.
2. Children are **sorted by name** before anything else happens. Two walks of the same Drive
   state produce the same CSV, row for row.
3. A child whose name matches a **scope exclusion** (`scope.exclude_segments`) is skipped before
   it is listed: its subtree is never visited, never counted, never in the CSV. This is how
   `70 Tradeshows` disappears.
4. Trashed children are skipped.
5. The child is yielded as a `ManifestEntry`, then, if it is a folder, recursed into.

Optional `max_depth` stops the descent; `preflight` uses depth 1 as the sharing probe (one
call, must return more than zero nodes).

**Multi-parent files.** Drive is a DAG. A file linked into two folders is listed by both
parents and is yielded twice, once per path, each row carrying `parents_count = 2`. The walker
does not pick a winner; the dbt gate rejects the state (`assert_no_multi_parent_nodes`) and the
operator fixes it in Drive.

**Cost.** One API call per folder visited plus pagination; time is the sum of those sequential
network calls, nothing else is measurable. Memory is the recursion depth (the tree depth) plus
one page of children. For the Miraex tree at the time of writing: about 64 folders, 410 nodes,
under a minute.

## 3. Stage 2 — the classification pass

A single pass over the entries, **sorted by path** so a parent is always processed before its
children. The work per node is constant:

1. Split the path: depth = number of segments; `rel_parent` = path minus the last segment.
2. Look up the parent's **inference record** in a dictionary keyed by the parent path. The
   record is what the ancestors established: `segment`, `company`, `company_key`, `deal`
   (engagement name), `year`, and, on this branch, `deal_key`.
3. Folders go through the first-match chain of §4 and store their own record for their
   children. Files are `document` leaves: they inherit the parent record unchanged and receive
   an `asset_class` from the card's rules (§5).
4. Keys are derived from the path only: `node_key` = the legacy segments joined with `|`,
   including the `path_prefix` (`30 Sales`) and the root name; `parent_key` = the same minus
   the last segment; `path_code` = ordinal or initial per segment (`3020Q…`);
   `legacy_library_id` = `path_code` plus an eight-character hash of the key.

Cost: one sort, then constant work per node; the dictionary holds one record per folder.

**The pattern in one sentence.** It is not a search. Every node is visited exactly once, top
down, and its category is decided at that moment from three inputs only: its depth, its own
name against the regexes, and what its ancestors already established. There is no look-ahead
and no second pass, so a folder cannot know anything about its descendants when it is
classified. §6 is built on that limit.

## 4. Folder classification grammar (first match wins)

Applied to a folder name at depth *d*, with the parent record *P*:

| # | rule | category | side effect on the record |
|---|---|---|---|
| 1 | `^(19\|20)\d{2}[\s_-]+rest` | `engagement_folder` | `deal` = name, `year`; if no company yet, `company` = rest and `company_key` = this folder |
| 2 | `^\d+\b` (leading number) | `taxonomy` | none |
| 3 | *d* ≤ `segment_depth` and no company yet | `segment` | `segment` = name |
| 4 | no company yet | `company_folder` | `company` = name, `company_key` = this folder |
| 5 | otherwise | `document` (a folder) | none |

Then, **independently of the category** (this branch):

| rule | effect |
|---|---|
| the parent's `company_key` is the parent's own key, i.e. the parent *is* the company anchor | `deal_key` = this folder (it is a **level-3 deal-anchor candidate**) |
| otherwise | `deal_key` inherited from the parent |

Both `company_key` and `deal_key` are emitted on every node beneath them as
`company_node_key` and `deal_node_key`. Segment rows carry neither.

Known property of rule 1, deliberately left as is on this branch: a year-prefixed folder at
company level (`2021_ELTA`) becomes an `engagement_folder` that is also its own company anchor,
and `silver_library_company` only takes `company_folder` rows, so its files are not in the
index. The singular test `assert_every_attachable_file_indexed` (severity WARN) makes that
visible; changing the company grammar is a separate decision.

## 5. File classification (asset class, first match wins)

From `asset_classification` in the card:

| class | rule | routing |
|---|---|---|
| `shortcut` | Drive shortcut mime | never attached, never a deal document |
| `deal_candidate` | extension `pdf` and name ~ `(?i)(^\|[^a-z0-9])(PO\|Billing)([^a-z0-9]\|$)` | note on the company (pass 1); note → deal (pass 2) |
| `parked_for_review` | extension `pdf` | note on the company; review queue; deferred deal document |
| `asset` | default | note on the company |

Image extensions are dropped before classification (`IMAGE_EXTS`).

## 6. The deal layer (side branch `walker-deal-depth3`)

**Why it is split.** "This folder holds a quote" is a property of a subtree. In a pre-order pass
the folder is classified before its descendants are seen, so the walker cannot decide it. The
walker therefore emits only the **structural** candidate and leaves the **heuristic** to the
silver model, where it can be iterated with `dbt run` instead of a re-walk.

**Structural half (walker).** `deal_node_key` = the first folder level under a company
folder, inherited by every node beneath it. Nothing else can be an anchor. Companies are not
affected in any way; `company_node_key` is computed exactly as before.

**Heuristic half (`silver_library_deal`, mirrored in `deal_anchors.py`).** A candidate
qualifies when

- its subtree contains at least `deal_inference.qualify.min_pdf_in_subtree` files with
  extension `pdf` (a quote exists), and
- its name does not match any of `deal_inference.exclude_name_patterns`, applied
  case-insensitively (the exhibition / tradeshow realm is never a deal).

In addition, a `deal_candidate` file sitting **directly under the company folder**, which has
no folder anchor above it, anchors itself (`anchor_kind = file`). That preserves the pass-2
behaviour that existed before this branch.

**Where each file lands.**

| file | `legacy_deal_id` | pass 2 |
|---|---|---|
| beneath a qualified folder, PO/Billing PDF | the folder's id | its note is associated to the deal |
| beneath a qualified folder, any other document | the folder's id | **deferred**: listed in `review/deal_documents.csv`, candidate for the notes API later |
| beneath a folder that does not qualify | NULL | none |
| directly under the company, PO/Billing PDF | its own id | one deal, its own note |
| directly under the company, anything else | NULL | none |

**Cardinality added to the card.** `Company → Deal` 1:N; `Deal → Company` N:1 (REJECT,
relationship test on `legacy_company_id`); `Library → Deal` N:0..1 (REJECT when set,
relationship test on `legacy_deal_id`). Structural and heuristic halves each have a singular
test: `assert_deal_anchors_are_level_3` and `assert_deal_anchor_names_not_excluded`.

**Where the patterns live and how to change them.**

| pattern | file | consumer |
|---|---|---|
| scope exclusions (`^70\b`, `tradeshow`, …) | card `scope.exclude_segments`; dbt var `excluded_segment_regex` | walker pruning; `assert_no_excluded_scope_nodes` |
| deal realm exclusions (`tradeshow`, `exhibition`, `exibition`, `expo`, `events`) | card `deal_inference.exclude_name_patterns`; dbt var `deal_exclude_regex` | `deal_anchors.py`; `silver_library_deal` |
| minimum PDFs beneath an anchor | card `deal_inference.qualify.min_pdf_in_subtree`; dbt var `deal_min_pdf` | same |
| PO/Billing recognition | card `asset_classification`; dbt var `deal_candidate_regex` | walker asset class; `assert_deal_candidates_are_pdf` |
| year prefix, ordinal prefix | `walker.py` (`_YEAR_RX`, `_ORDINAL_RX`) | folder grammar rules 1 and 2 |

`tests/test_card.py` asserts that the card and the dbt vars agree, so a change in one place
without the other fails the unit suite. Editing the two deal patterns needs `dbt run` and
`review`; it does not need a re-walk. Editing scope exclusions or the year/ordinal regexes
changes which nodes exist and needs a re-walk.

## 7. Worked example (the rehearsal tree)

```
Quantum/                                   segment
  Toshiba/                                 company_folder            company_key = Toshiba
    Toshiba PO 2026-001.pdf                deal_candidate, no folder anchor → FILE anchor (own deal)
    spec sheet.pdf                         parked, directly under the company → no deal
    2026 Quantum sensor RFQ/               level 3 → deal_key = itself; 2 PDFs beneath → QUALIFIES
      Quote QS-17.pdf                      parked → deferred deal document
      PO 2026-042.pdf                      deal_candidate → note associated to the RFQ deal
      SOW.docx                             asset → deferred deal document
      drawings/                            level 4, inherits the RFQ anchor
        layout.gds                         asset → deferred deal document
  Bluefors/
    Site survey/                           level 3, no PDF beneath → not a deal
      site photos.docx                     legacy_deal_id NULL
  Alice & Bob/
    2025 Photonics West Exhibition/        level 3, PDF beneath, name excluded → not a deal
      booth quote.pdf                      legacy_deal_id NULL
  Thorlabs/
    Submissions/                           level 3, Billing PDF beneath → QUALIFIES
      Billing-2026-03.pdf                  note associated to the Submissions deal
      lnoi_MZI_tests.gds                   deferred deal document
```

Pass 2 on that tree creates five deals: `Submissions`, `2026 Quantum sensor RFQ`, and the three
self-anchored PDFs (`PO_4711 Thorlabs.pdf`, `Toshiba PO 2026-001.pdf`, `IQM Billing Q2.PDF`).
Five note → deal associations follow, one per PO/Billing PDF; the quote, the SOW and the two
`.gds` files stay on the company only and appear in `review/deal_documents.csv`.

## 8. Properties to keep in mind

- **Path-derived keys.** Renaming any ancestor folder changes the keys of its whole subtree; the
  ledger then treats those files as new. The same holds for an opportunity folder: rename it and
  its deal gets a new `legacy_deal_id`.
- **Determinism.** Name-sorted traversal plus path-derived keys make the CSV reproducible for a
  given Drive state, which is what makes `bq-load --replace` and the dbt gate meaningful.
- **Positional load.** `bq load` maps CSV columns to the schema file by position after skipping
  the header; `tests/test_schema_drift.py` pins the writer's column order to the schema file so a
  reorder cannot load silently.
- **Two implementations of the deal heuristic.** Python (`deal_anchors.py`) feeds the offline
  artefacts and pass 2; SQL (`silver_library_deal`) is the record in BigQuery. The rehearsal
  compares them name by name.
