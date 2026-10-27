-- EHR encounters, carrying the restatement metadata the MERGE maintains:
-- source_updated_at is the source system's own last-change time, revision
-- counts how many times the row has been corrected since it first landed.
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
    is_voided,
    source_updated_at,
    revision,
    revision > 1                                                    AS is_restated,
    ingested_at,
    date_diff('minute', encounter_start, encounter_stop)            AS encounter_duration_minutes
FROM iceberg.default.encounters
