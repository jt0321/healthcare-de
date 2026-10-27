/*
    Field-level disagreement among records that were linked anyway.

    A match rate on its own is a reassuring number that hides the interesting
    problem. These are pairs the EMPI is confident describe one person, where
    the two systems still state different facts about them. Someone has to
    decide which is right, and until they do, every report that uses the field
    is reporting one system's answer.

    This is the model to look at when a downstream count differs from what the
    source system says.
*/

WITH linked AS (
    SELECT
        m.mpi_id,
        m.match_score,
        m.match_method,
        c.ehr_birth_date,
        c.hie_birth_date,
        c.ehr_last_name,
        c.hie_last_name,
        c.ehr_first_name,
        c.hie_first_name,
        c.ehr_gender,
        c.hie_gender,
        c.ehr_postal_code,
        c.hie_postal_code
    FROM {{ ref('dim_patient_master') }} m
    JOIN {{ ref('int_patient_match_candidates') }} c
      ON c.ehr_patient_id = m.ehr_patient_id
     AND c.hie_patient_id = m.hie_patient_id
    WHERE m.record_source = 'both'
)

SELECT
    mpi_id,
    match_score,
    match_method,

    ehr_birth_date,
    hie_birth_date,
    ehr_birth_date <> hie_birth_date                          AS birth_date_conflict,

    ehr_last_name,
    hie_last_name,
    ehr_last_name <> hie_last_name                            AS last_name_conflict,

    ehr_first_name,
    hie_first_name,
    ehr_first_name <> hie_first_name                          AS first_name_conflict,

    ehr_gender,
    hie_gender,
    ehr_gender <> hie_gender
        AND hie_gender <> 'unknown'
        AND ehr_gender <> 'unknown'                           AS gender_conflict,

    ehr_postal_code,
    hie_postal_code,
    ehr_postal_code IS NOT NULL
        AND hie_postal_code IS NOT NULL
        AND ehr_postal_code <> hie_postal_code                AS postal_code_conflict,

    -- A missing value is a different problem from a contradictory one: it
    -- needs a source to fill it, not an adjudication between two claims.
    hie_postal_code IS NULL                                   AS postal_code_missing_in_hie,
    hie_gender = 'unknown'                                    AS gender_missing_in_hie,

    CAST(ehr_birth_date <> hie_birth_date AS INTEGER)
      + CAST(ehr_last_name <> hie_last_name AS INTEGER)
      + CAST(ehr_first_name <> hie_first_name AS INTEGER)
      + CAST(ehr_postal_code IS NOT NULL
             AND hie_postal_code IS NOT NULL
             AND ehr_postal_code <> hie_postal_code AS INTEGER)
                                                              AS conflict_count
FROM linked
