-- Review queue: every other PDF, parked for operator review before any deal
-- processing. Still attached as a note on the company in pass 1.
select *
from {{ ref('silver_library_index') }}
where asset_class = 'parked_for_review'
