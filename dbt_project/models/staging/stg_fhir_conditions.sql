SELECT
    condition_id,
    patient_id,
    encounter_id,
    condition_code,
    -- prefer the human-readable text; fall back to the SNOMED display term
    coalesce(condition_text, condition_display)                     AS condition_description,
    condition_display                                               AS snomed_display,
    clinical_status,
    verification_status,
    onset_date,
    abatement_date,
    is_active
FROM iceberg.default.fhir_conditions
