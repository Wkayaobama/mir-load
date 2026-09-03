-- The 19-column icalps parity table (+ inference/drive extras), files only,
-- anchored to a company folder. legacy_company_id = legacy id of the
-- anchoring company folder row (Library → Company N:1). Files without an
-- anchor are routed to silver_library_orphans (WARN), never here (REJECT).
with h as (
    select * from {{ ref('stg_library_hierarchy') }}
),
companies as (
    select company_node_key, legacy_company_id, company_name
    from {{ ref('silver_library_company') }}
)
select
    h.legacy_library_id,
    c.legacy_company_id,
    cast(null as string)        as legacy_contact_id,
    cast(null as string)        as legacy_deal_id,
    cast(null as string)        as legacy_case_id,
    h.legacy_file_path,
    h.node_name                 as legacy_file_name,
    h.link                      as legacy_file_link,
    concat('Drive indexed: ', h.node_name) as libr_note,
    'file'                      as libr_type,
    h.libr_category,
    'indexed'                   as libr_status,
    cast(null as int64)         as libr_created_by,
    cast(null as int64)         as libr_updated_by,
    h.drive_created_at          as libr_created_at,
    h.drive_modified_at         as libr_updated_at,
    h.owner_email               as mirx_owner_email,
    h.owner_name                as mirx_owner_fullname,
    h.walked_at                 as loaded_at,
    -- extras
    h.node_key,
    h.parent_key,
    h.company_node_key,
    c.company_name,
    h.asset_class,
    h.inferred_segment,
    h.path_code,
    h.depth,
    h.drive_id                  as drive_file_id,
    h.drive_md5,
    h.drive_size,
    h.drive_mimetype,
    h.extension,
    h.parents_count
from h
join companies c using (company_node_key)
where not h.is_dir
  and h.asset_class != 'shortcut'
