-- The HIE feed's patient roster. Identifiers are the sending system's own
-- (HIEPT-*) and share nothing with the EHR extract above; the feed is also
-- missing fields and carries its own data-entry errors. See dags/hie_feed.py.
SELECT
    patient_id,
    birth_date,
    {{ normalize_gender('gender') }}                                AS gender,
    family_name,
    given_name,
    family_name || ', ' || given_name                               AS full_name,
    postal_code,
    state,
    race,
    ethnicity,
    date_diff('year', birth_date, current_date)                     AS age,
    CASE
        WHEN date_diff('year', birth_date, current_date) < 18  THEN '0-17'
        WHEN date_diff('year', birth_date, current_date) < 35  THEN '18-34'
        WHEN date_diff('year', birth_date, current_date) < 50  THEN '35-49'
        WHEN date_diff('year', birth_date, current_date) < 65  THEN '50-64'
        ELSE '65+'
    END                                                             AS age_group
FROM iceberg.default.fhir_patients
