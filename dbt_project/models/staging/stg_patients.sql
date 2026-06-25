SELECT
    patient_id,
    birth_date,
    full_name,
    gender,
    race,
    date_diff('year', birth_date, current_date)                     AS age,
    CASE
        WHEN date_diff('year', birth_date, current_date) < 18  THEN '0-17'
        WHEN date_diff('year', birth_date, current_date) < 35  THEN '18-34'
        WHEN date_diff('year', birth_date, current_date) < 50  THEN '35-49'
        WHEN date_diff('year', birth_date, current_date) < 65  THEN '50-64'
        ELSE '65+'
    END                                                             AS age_group
FROM iceberg.default.patients
