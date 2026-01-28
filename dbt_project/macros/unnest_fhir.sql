{% macro unnest_fhir(raw_json_column, resource_type) %}
    WITH base AS (
        SELECT 
            -- Extract the 'entry' array from the FHIR bundle
            unnest(json_extract({{ raw_json_column }}, '$.entry')) AS entry
    )
    SELECT
        -- Navigate the nested structure to get the actual resource data
        entry->'resource'->>'id' as resource_id,
        entry->'resource' as resource_content
    FROM base
    WHERE entry->'resource'->>'resourceType' = '{{ resource_type }}'
{% endmacro %}