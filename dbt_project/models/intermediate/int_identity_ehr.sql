/*
    Identity attributes from the local EHR, standardized for comparison.

    Pulling standardization into its own model keeps the matching logic
    readable and makes the standardized form inspectable on its own -- when a
    match is wrong, the first question is always whether the inputs were
    normalized the way you thought.
*/

SELECT
    patient_id                                      AS ehr_patient_id,
    birth_date,
    year(birth_date)                                AS birth_year,
    month(birth_date)                               AS birth_month,
    day(birth_date)                                 AS birth_day,
    {{ normalize_name('last_name') }}               AS last_name_norm,
    {{ name_root('last_name', '-') }}               AS last_name_root,
    {{ normalize_name('first_name') }}              AS first_name_norm,
    gender,
    {{ clean_postal_code('postal_code') }}          AS postal_code,
    nullif(trim(state), '')                         AS state,
    full_name,
    race,
    ethnicity
FROM {{ ref('stg_patients') }}
WHERE NOT is_voided
