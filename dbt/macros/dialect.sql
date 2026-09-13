{#- Dialect shims so the same models run on BigQuery (production) and DuckDB
    (the e2e rehearsal, ic-load's own dbt pattern). Dispatch order is set in
    dbt_project.yml: mr_load_library → dbt. -#}

{% macro countif(expr) -%}
  {{ return(adapter.dispatch('countif', 'mr_load_library')(expr)) }}
{%- endmacro %}
{% macro default__countif(expr) -%} count_if({{ expr }}) {%- endmacro %}
{% macro bigquery__countif(expr) -%} countif({{ expr }}) {%- endmacro %}

{% macro regexp_contains(col, rx) -%}
  {{ return(adapter.dispatch('regexp_contains', 'mr_load_library')(col, rx)) }}
{%- endmacro %}
{% macro default__regexp_contains(col, rx) -%} regexp_matches({{ col }}, {{ rx }}) {%- endmacro %}
{% macro bigquery__regexp_contains(col, rx) -%} regexp_contains({{ col }}, {{ rx }}) {%- endmacro %}

{#- true when ANY '/'-separated segment of col matches rx -#}
{% macro segments_match(col, rx) -%}
  {{ return(adapter.dispatch('segments_match', 'mr_load_library')(col, rx)) }}
{%- endmacro %}
{% macro default__segments_match(col, rx) -%}
  len(list_filter(string_split({{ col }}, '/'), s -> regexp_matches(s, {{ rx }}))) > 0
{%- endmacro %}
{% macro bigquery__segments_match(col, rx) -%}
  exists (select 1 from unnest(split({{ col }}, '/')) as seg where regexp_contains(seg, {{ rx }}))
{%- endmacro %}
