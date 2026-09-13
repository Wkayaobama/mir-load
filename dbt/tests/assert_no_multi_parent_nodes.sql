-- Card cardinality: "Drive node → parent" must be N:1. Drive is a DAG, so a
-- file linked into two folders would map to two companies. REJECT.
select node_key, node_name, parents_count
from {{ ref('stg_library_hierarchy') }}
where parents_count > 1
