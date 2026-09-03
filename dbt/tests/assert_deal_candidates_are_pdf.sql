-- Card asset_classification: deal_candidate requires extension == pdf AND
-- name ~ PO|Billing. Anything else classified as a candidate is a walker bug.
select legacy_library_id, legacy_file_name, extension
from {{ ref('silver_library_deal_candidates') }}
where extension != 'pdf'
   or not regexp_contains(legacy_file_name, var('deal_candidate_regex'))
