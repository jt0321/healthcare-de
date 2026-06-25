SELECT
    patient_id,
    encounter_id,
    condition_start,
    condition_stop,
    condition_code,
    condition_description,
    condition_stop IS NULL                                          AS is_active
FROM iceberg.default.conditions
