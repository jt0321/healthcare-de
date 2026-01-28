WITH flattened_patients AS (
    {{ unnest_fhir('raw_json_data', 'Patient') }}
)

SELECT
    resource_id as patient_id,
    resource_content->>'birthDate' as birth_date,
    resource_content->'name'->0->>'family' as last_name,
    resource_content->'name'->0->>'given'->>0 as first_name,
    resource_content->>'gender' as gender
FROM flattened_patients