-- Inferred deals (side branch walker-deal-depth3). One row per QUALIFIED anchor:
--   folder : a level-3 node (deal_node_key = its own node_key) whose subtree holds >= deal_min_pdf
--            PDFs and whose name is outside deal_exclude_regex (exhibition / tradeshow realm);
--   file   : a PO/Billing PDF (asset_class deal_candidate) sitting directly under the company —
--            no folder anchor above it — anchors itself (pass-2 behaviour before this branch).
-- The structural key comes from the walker; the heuristic is THIS predicate, driven by the card
-- through dbt vars, so it is iterated with `dbt run`, never with a re-walk.
-- hs_deal_id / deal_status come from the ledger (deals_created, keyed by the anchor's library id).
{% set rx = "'" ~ var('deal_exclude_regex') ~ "'" %}
with h as (
    select * from {{ ref('stg_library_hierarchy') }}
),
folder_candidates as (
    select
        node_key            as deal_node_key,
        legacy_library_id   as legacy_deal_id,
        node_name           as deal_name,
        'folder'            as anchor_kind,
        company_node_key,
        depth,
        drive_id,
        link,
        drive_created_at,
        drive_modified_at,
        owner_email
    from h
    where is_dir and deal_node_key = node_key and company_node_key is not null
),
beneath as (
    select
        deal_node_key,
        {{ countif('not is_dir') }}                                as asset_count,
        {{ countif("not is_dir and extension = 'pdf'") }}          as pdf_count,
        {{ countif("asset_class = 'deal_candidate'") }}            as deal_candidate_count,
        {{ countif("asset_class = 'parked_for_review'") }}         as parked_count
    from h
    where deal_node_key is not null
    group by deal_node_key
),
folders as (
    select c.*,
           coalesce(b.asset_count, 0)          as asset_count,
           coalesce(b.pdf_count, 0)            as pdf_count,
           coalesce(b.deal_candidate_count, 0) as deal_candidate_count,
           coalesce(b.parked_count, 0)         as parked_count
    from folder_candidates c
    left join beneath b using (deal_node_key)
    where coalesce(b.pdf_count, 0) >= {{ var('deal_min_pdf') }}
      and not {{ regexp_contains('c.deal_name', rx) }}
),
files as (
    select
        node_key            as deal_node_key,
        legacy_library_id   as legacy_deal_id,
        node_name           as deal_name,
        'file'              as anchor_kind,
        company_node_key,
        depth,
        drive_id,
        link,
        drive_created_at,
        drive_modified_at,
        owner_email,
        1 as asset_count, 1 as pdf_count, 1 as deal_candidate_count, 0 as parked_count
    from h
    where not is_dir and asset_class = 'deal_candidate' and deal_node_key is null and company_node_key is not null
),
anchors as (
    select * from folders
    union all
    select * from files
),
companies as (
    select company_node_key, legacy_company_id, company_name, segment
    from {{ ref('silver_library_company') }}
),
resolved as (
    select legacy_library_id as legacy_deal_id, hs_deal_id, dealname as hs_dealname, status as deal_status
    from {{ source('mrload_ledger', 'deals_created') }}
)
select
    a.deal_node_key,
    a.legacy_deal_id,
    a.deal_name,
    a.anchor_kind,
    a.company_node_key,
    co.legacy_company_id,
    co.company_name,
    co.segment,
    a.depth,
    a.drive_id,
    a.link,
    a.drive_created_at,
    a.drive_modified_at,
    a.owner_email,
    a.asset_count,
    a.pdf_count,
    a.deal_candidate_count,
    a.parked_count,
    r.hs_deal_id,
    r.hs_dealname,
    r.deal_status
from anchors a
join companies co using (company_node_key)
left join resolved r using (legacy_deal_id)
