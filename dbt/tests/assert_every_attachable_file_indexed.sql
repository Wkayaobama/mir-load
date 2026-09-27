{{ config(severity='warn') }}
-- Silent-drop guard: every file with a company anchor (and not a shortcut) must appear in
-- silver_library_index. A folder that anchors files without being a company_folder row (e.g. a
-- year-prefixed folder at company level such as "2021_ELTA") drops its subtree here — reported
-- as WARN on this branch (company classification is out of scope), so it is visible, not silent.
select h.node_key, h.node_name, h.company_node_key
from {{ ref('stg_library_hierarchy') }} h
left join {{ ref('silver_library_index') }} i using (node_key)
where not h.is_dir and h.company_node_key is not null and h.asset_class != 'shortcut'
  and i.node_key is null
