{% macro generate_schema_name(custom_schema_name, node) -%}
    {#- Use the custom schema name directly without prefix (ic-load convention) -#}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
