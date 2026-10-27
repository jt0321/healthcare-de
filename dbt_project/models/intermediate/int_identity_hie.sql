/*
    Identity attributes from the HIE feed, standardized the same way.

    FHIR carries given names as an array, which the staging loader joins with
    a space ("Graham Hank"); the EHR extract supplies only the legal first
    name. Comparing the first token of each is the closest honest comparison
    available -- the middle name is not evidence of a different person.
*/

SELECT
    patient_id                                      AS hie_patient_id,
    birth_date,
    year(birth_date)                                AS birth_year,
    month(birth_date)                               AS birth_month,
    day(birth_date)                                 AS birth_day,
    {{ normalize_name('family_name') }}             AS last_name_norm,
    {{ name_root('family_name', '-') }}             AS last_name_root,
    {{ name_root('given_name', ' ') }}              AS first_name_norm,
    gender,
    {{ clean_postal_code('postal_code') }}          AS postal_code,
    nullif(trim(state), '')                         AS state,
    full_name,
    race,
    ethnicity
FROM {{ ref('stg_fhir_patients') }}
