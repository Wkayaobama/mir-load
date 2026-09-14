-- Deal layer, structural half: every FOLDER anchor is the first folder level under its
-- company anchor (its parent's company_node_key is the parent itself). Files anchor themselves
-- only when they sit directly under the company (no deal_node_key inherited).
with h as (select * from {{ ref('stg_library_hierarchy') }})
select d.deal_node_key, d.anchor_kind, d.deal_name
from {{ ref('silver_library_deal') }} d
left join h self on self.node_key = d.deal_node_key
left join h parent on parent.node_key = self.parent_key
where (d.anchor_kind = 'folder' and not (parent.company_node_key = parent.node_key))
   or (d.anchor_kind = 'file' and (self.deal_node_key is not null or self.asset_class != 'deal_candidate'))
