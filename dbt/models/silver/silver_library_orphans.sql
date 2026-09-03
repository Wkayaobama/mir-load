-- Files in scope with no company anchor (e.g. a spreadsheet dropped directly
-- under a segment folder). Reported, never attached — Library → Company is N:1.
select
    node_key, node_name, parent_key, depth, legacy_file_path, link,
    asset_class, inferred_segment, drive_id, drive_mimetype
from {{ ref('stg_library_hierarchy') }}
where not is_dir
  and company_node_key is null
