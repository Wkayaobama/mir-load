-- Segment → Company is 1:N with unique company names per segment. Two
-- folders "Toshiba" under Quantum would create two HubSpot companies.
select segment_node_key, lower(trim(company_name)) as company_name_norm, count(*) as n
from {{ ref('silver_library_company') }}
group by 1, 2
having count(*) > 1
