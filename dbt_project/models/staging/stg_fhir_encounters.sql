SELECT
    encounter_id,
    patient_id,
    encounter_start,
    encounter_stop,
    encounter_class_code,
    encounter_type_code,
    encounter_type_display,
    reason_code,
    reason_display,
    status,
    date_diff('minute', encounter_start, encounter_stop)            AS encounter_duration_minutes
FROM iceberg.default.fhir_encounters
