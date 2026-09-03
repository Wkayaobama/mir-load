-- Pass-2 queue: PO / Billing PDFs that must ALSO be indexed as deals.
-- Deliberately a separate model so deal indexing never runs inside the DFS.
-- hs_deal_id / deal_status come from the ledger (deals_created) after pass 2.
select
    i.*,
    d.hs_deal_id,
    d.dealname,
    d.status as deal_status
from {{ ref('silver_library_index') }} i
left join {{ source('mrload_ledger', 'deals_created') }} d using (legacy_library_id)
where i.asset_class = 'deal_candidate'
