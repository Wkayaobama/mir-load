-- One row per company folder in scope: the object that becomes a HubSpot
-- company. hs_company_id comes from the runner ledger (companies_resolved,
-- loaded by `runner ledger-export`); NULL until step 6 has run.
with companies as (
    select
        node_key            as company_node_key,
        node_name           as company_name,
        legacy_library_id   as legacy_company_id,
        parent_key          as segment_node_key,
        inferred_segment    as segment,
        drive_id,
        link,
        drive_created_at,
        drive_modified_at,
        owner_email
    from {{ ref('stg_library_hierarchy') }}
    where libr_category = 'company_folder'
),
assets as (
    select
        company_node_key,
        {{ countif('not is_dir') }}                            as asset_count,
        {{ countif("asset_class = 'deal_candidate'") }}       as deal_candidate_count,
        {{ countif("asset_class = 'parked_for_review'") }}    as parked_count,
        {{ countif("asset_class = 'shortcut'") }}             as shortcut_count
    from {{ ref('stg_library_hierarchy') }}
    where company_node_key is not null
    group by company_node_key
),
resolved as (
    select company_node_key, hs_company_id, status as resolution_status
    from {{ source('mrload_ledger', 'companies_resolved') }}
)
select
    c.*,
    coalesce(a.asset_count, 0)          as asset_count,
    coalesce(a.deal_candidate_count, 0) as deal_candidate_count,
    coalesce(a.parked_count, 0)         as parked_count,
    coalesce(a.shortcut_count, 0)       as shortcut_count,
    r.hs_company_id,
    r.resolution_status
from companies c
left join assets a using (company_node_key)
left join resolved r using (company_node_key)
