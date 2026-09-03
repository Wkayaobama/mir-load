# mr-load dbt — silver layer over the walked Drive hierarchy

Adapter: `dbt-bigquery`. Source: `mrload_raw.library_hierarchy` (walker output
loaded by `python -m pipeline.library_files.runner bq-load`).

```
dbt deps
dbt run          # stg_library_hierarchy → silver_library_company / _index / _deal_candidates / _parked / _orphans
dbt test         # the cardinality contract of context/cards/library.yaml
```

**Constraint reality on BigQuery** (docs.getdbt.com/reference/resource-properties/constraints):
`not_null` is enforced; `primary_key` / `foreign_key` are definable but NOT
enforced (optimizer hints); `unique` / `check` unsupported. Enforcement
therefore lives in the data tests (`unique`, `relationships`, `accepted_values`,
`dbt_utils.expression_is_true`) and the singular tests in `tests/`, exactly as
in ic-load.

| Card rule | Where it is enforced |
|---|---|
| node_key unique / not null | `stg_library_hierarchy` column tests |
| parent_key → node_key | `relationships` test |
| Drive node → parent N:1 | `assert_no_multi_parent_nodes` + `parents_count = 1` |
| Library → Company N:1 (REJECT) | `silver_library_index.legacy_company_id` not_null + relationships; orphans routed to `silver_library_orphans` |
| Segment → Company 1:N, unique names | `assert_company_name_unique_within_segment` |
| deal_candidate = pdf ∧ PO/Billing | `assert_deal_candidates_are_pdf` |
| Tradeshows out of scope | `assert_no_excluded_scope_nodes` |
| Orphan assets (warn) | `assert_asset_has_company_anchor` (severity warn) |
