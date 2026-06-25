SELECT
    patient_id,
    encounter_id,
    condition_start,
    condition_stop,
    condition_code,
    condition_description,
    condition_stop IS NULL as is_active
FROM iceberg_scan('s3://healthcare/iceberg/default/conditions', allow_moved_paths=true)
