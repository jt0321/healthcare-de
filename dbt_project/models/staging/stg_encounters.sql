SELECT
    encounter_id,
    patient_id,
    encounter_start,
    encounter_stop,
    encounter_class,
    encounter_code,
    encounter_description,
    reason_code,
    reason_description,
    payer_id,
    total_claim_cost,
    date_diff('minute', encounter_start, encounter_stop) as encounter_duration_minutes
FROM iceberg_scan('s3://healthcare/iceberg/default/encounters', allow_moved_paths=true)
