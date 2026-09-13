-- Scope guard: tradeshows (ordinal 70) and friends are out of the present
-- pipeline. The walker prunes them; this re-checks bronze so nothing leaks.
{% set rx = "'" ~ var('excluded_segment_regex') ~ "'" %}
select node_key, rel_path
from {{ ref('stg_library_hierarchy') }}
where {{ segments_match('rel_path', rx) }}
