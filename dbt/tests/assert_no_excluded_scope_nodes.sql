-- Scope guard: tradeshows (ordinal 70) and friends are out of the present
-- pipeline. The walker prunes them; this re-checks bronze so nothing leaks.
select node_key, rel_path
from {{ ref('stg_library_hierarchy') }}
where exists (
    select 1
    from unnest(split(rel_path, '/')) as seg
    where regexp_contains(seg, var('excluded_segment_regex'))
)
