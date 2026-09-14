-- Deal layer, heuristic half: no inferred deal belongs to the exhibition / tradeshow realm.
-- True by construction of silver_library_deal; re-checked so a var edit cannot regress it silently.
{% set rx = "'" ~ var('deal_exclude_regex') ~ "'" %}
select deal_node_key, deal_name
from {{ ref('silver_library_deal') }}
where {{ regexp_contains('deal_name', rx) }}
