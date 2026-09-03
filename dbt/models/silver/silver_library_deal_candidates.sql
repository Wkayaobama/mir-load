-- Pass-2 queue: PO / Billing PDFs that must ALSO be indexed as deals.
-- Deliberately a separate model so deal indexing never runs inside the DFS.
select *
from {{ ref('silver_library_index') }}
where asset_class = 'deal_candidate'
