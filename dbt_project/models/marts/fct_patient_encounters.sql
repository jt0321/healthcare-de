WITH encounters AS (
    SELECT * FROM {{ ref('stg_encounters') }}
),

patients AS (
    SELECT * FROM {{ ref('stg_patients') }}
)

SELECT
    e.encounter_id,
    e.patient_id,
    p.full_name,
    p.gender,
    p.race,
    p.age,
    p.age_group,
    e.encounter_start,
    e.encounter_stop,
    e.encounter_class,
    e.encounter_code,
    e.encounter_description,
    e.reason_code,
    e.reason_description,
    e.payer_id,
    e.total_claim_cost,
    e.encounter_duration_minutes,
    date_trunc('month', e.encounter_start) as encounter_month
FROM encounters e
LEFT JOIN patients p ON e.patient_id = p.patient_id
