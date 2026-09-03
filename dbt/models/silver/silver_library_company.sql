-- One row per company folder in scope: the object that becomes a HubSpot
-- company. hs_company_id is NULL here; the runner's ledger holds the
-- resolution (companies_resolved) and is joined back at attach time.
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
        countif(not is_dir)                                   as asset_count,
        countif(asset_class = 'deal_candidate')               as deal_candidate_count,
        countif(asset_class = 'parked_for_review')            as parked_count,
        countif(asset_class = 'shortcut')                     as shortcut_count
    from {{ ref('stg_library_hierarchy') }}
    where company_node_key is not null
    group by company_node_key
)
select
    c.*,
    coalesce(a.asset_count, 0)          as asset_count,
    coalesce(a.deal_candidate_count, 0) as deal_candidate_count,
    coalesce(a.parked_count, 0)         as parked_count,
    coalesce(a.shortcut_count, 0)       as shortcut_count,
    cast(null as string)                as hs_company_id
from companies c
left join assets a using (company_node_key)
