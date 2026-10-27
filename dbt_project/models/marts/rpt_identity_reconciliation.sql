/*
    The one table to hand someone who asks "did the two feeds line up?"

    Long format -- one row per metric -- so the set of metrics can grow
    without a schema change and so the whole thing renders as a single table
    in a dashboard. Counts are deliberately reported alongside the rates: a
    98% match rate on 1,100 patients still leaves twenty-odd people whose
    records are split across two identities, and the count is the number
    somebody has to work through.
*/

WITH master AS (
    SELECT * FROM {{ ref('dim_patient_master') }}
),

conflicts AS (
    SELECT * FROM {{ ref('rpt_identity_conflicts') }}
),

candidates AS (
    SELECT * FROM {{ ref('int_patient_match_candidates') }}
),

totals AS (
    SELECT
        (SELECT count(*) FROM {{ ref('int_identity_ehr') }})           AS ehr_patients,
        (SELECT count(*) FROM {{ ref('int_identity_hie') }})           AS hie_records,
        count(*)                                                       AS master_identities,
        count_if(record_source = 'both')                               AS linked,
        count_if(record_source = 'ehr_only')                           AS ehr_only,
        count_if(record_source = 'ehr_only_pending_review')            AS pending_review,
        count_if(record_source = 'hie_only')                           AS hie_only,
        count_if(record_source = 'hie_only_duplicate_suspect')         AS duplicate_suspects,
        coalesce(avg(match_score), 0)                                  AS mean_match_score
    FROM master
),

conflict_totals AS (
    SELECT
        count(*)                                  AS pairs,
        count_if(birth_date_conflict)             AS birth_date_conflicts,
        count_if(last_name_conflict)              AS last_name_conflicts,
        count_if(first_name_conflict)             AS first_name_conflicts,
        count_if(gender_conflict)                 AS gender_conflicts,
        count_if(postal_code_conflict)            AS postal_code_conflicts,
        count_if(postal_code_missing_in_hie)      AS postal_code_missing,
        count_if(conflict_count = 0)              AS clean_pairs
    FROM conflicts
),

metrics AS (
    SELECT 10 AS sort_order, 'volume' AS category,
           'EHR patients (CSV extract)' AS metric,
           CAST(ehr_patients AS DOUBLE) AS value, 'count' AS unit FROM totals
    UNION ALL SELECT 20, 'volume', 'HIE patient records (FHIR feed)',
           CAST(hie_records AS DOUBLE), 'count' FROM totals
    UNION ALL SELECT 30, 'volume', 'Master identities after linkage',
           CAST(master_identities AS DOUBLE), 'count' FROM totals

    UNION ALL SELECT 40, 'linkage', 'Auto-linked across both feeds',
           CAST(linked AS DOUBLE), 'count' FROM totals
    UNION ALL SELECT 50, 'linkage', 'Link rate against EHR population',
           CAST(round(100.0 * linked / nullif(ehr_patients, 0), 2) AS DOUBLE), 'percent' FROM totals
    UNION ALL SELECT 60, 'linkage', 'Held for manual review',
           CAST(pending_review AS DOUBLE), 'count' FROM totals
    UNION ALL SELECT 70, 'linkage', 'EHR-only (no HIE record found)',
           CAST(ehr_only AS DOUBLE), 'count' FROM totals
    UNION ALL SELECT 80, 'linkage', 'HIE-only (unknown to the EHR)',
           CAST(hie_only AS DOUBLE), 'count' FROM totals
    UNION ALL SELECT 90, 'linkage', 'Suspected duplicates within the HIE feed',
           CAST(duplicate_suspects AS DOUBLE), 'count' FROM totals
    UNION ALL SELECT 100, 'linkage', 'Mean score of accepted links',
           CAST(round(mean_match_score, 1) AS DOUBLE), 'score' FROM totals

    UNION ALL SELECT 110, 'conflicts', 'Linked pairs agreeing on every field',
           CAST(clean_pairs AS DOUBLE), 'count' FROM conflict_totals
    UNION ALL SELECT 120, 'conflicts', 'Linked pairs with at least one conflict',
           CAST(pairs - clean_pairs AS DOUBLE), 'count' FROM conflict_totals
    UNION ALL SELECT 130, 'conflicts', 'Conflicting date of birth',
           CAST(birth_date_conflicts AS DOUBLE), 'count' FROM conflict_totals
    UNION ALL SELECT 140, 'conflicts', 'Conflicting surname',
           CAST(last_name_conflicts AS DOUBLE), 'count' FROM conflict_totals
    UNION ALL SELECT 150, 'conflicts', 'Conflicting given name',
           CAST(first_name_conflicts AS DOUBLE), 'count' FROM conflict_totals
    UNION ALL SELECT 160, 'conflicts', 'Conflicting administrative sex',
           CAST(gender_conflicts AS DOUBLE), 'count' FROM conflict_totals
    UNION ALL SELECT 170, 'conflicts', 'Conflicting postal code',
           CAST(postal_code_conflicts AS DOUBLE), 'count' FROM conflict_totals
    UNION ALL SELECT 180, 'conflicts', 'Postal code absent from the HIE feed',
           CAST(postal_code_missing AS DOUBLE), 'count' FROM conflict_totals

    UNION ALL SELECT 190, 'scoring', 'Candidate pairs evaluated after blocking',
           CAST((SELECT count(*) FROM candidates) AS DOUBLE), 'count'
    UNION ALL SELECT 200, 'scoring', 'Candidates rejected below threshold',
           CAST((SELECT count_if(match_tier = 'reject') FROM candidates) AS DOUBLE), 'count'
)

SELECT category, metric, value, unit
FROM metrics
ORDER BY sort_order
