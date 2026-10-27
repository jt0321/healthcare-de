-- The local EHR's nightly patient extract, after corrections have been merged.
-- Voided rows are kept but flagged: a retracted registration is part of the
-- record, and downstream models decide whether to count it.
SELECT
    patient_id,
    birth_date,
    first_name,
    last_name,
    full_name,
    {{ normalize_gender('gender') }}                                AS gender,
    race,
    ethnicity,
    city,
    state,
    postal_code,
    is_voided,
    source_updated_at,
    revision,
    revision > 1                                                    AS is_restated,
    date_diff('year', birth_date, current_date)                     AS age,
    CASE
        WHEN date_diff('year', birth_date, current_date) < 18  THEN '0-17'
        WHEN date_diff('year', birth_date, current_date) < 35  THEN '18-34'
        WHEN date_diff('year', birth_date, current_date) < 50  THEN '35-49'
        WHEN date_diff('year', birth_date, current_date) < 65  THEN '50-64'
        ELSE '65+'
    END                                                             AS age_group
FROM iceberg.default.patients
