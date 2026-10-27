/*
    The master patient index: one row per person the warehouse believes exists.

    Two things happen here.

    Linkage. Accepted mutual-best matches above the auto threshold become a
    single master identity holding both source keys. Everything else stays
    separate: an EHR patient with no accepted match, an HIE record with no
    accepted match, and anything that landed in the review band all get their
    own master identity, flagged with why. A record awaiting review is a
    distinct person as far as every downstream model is concerned -- the
    alternative is to pre-empt the reviewer's decision.

    Survivorship. When both sides describe the same person and disagree, one
    value has to be chosen per field. The rule here is source precedence: the
    EHR is the system of record for everything it populates, and the HIE feed
    fills only what the EHR left null. Real MDM uses per-field rules -- most
    recently updated for address, most complete for name, a designated
    authority for date of birth -- and records which source won for each
    attribute. The conflicts the rule papers over are not discarded: they are
    reported in rpt_identity_conflicts.

    mpi_id is derived by hash from the anchoring source key so it is stable
    across rebuilds. A real MPI issues a surrogate from a sequence and never
    recomputes it, precisely so that a change in matching logic cannot
    silently renumber every person.
*/

WITH accepted AS (
    SELECT *
    FROM {{ ref('int_patient_match_best') }}
    WHERE is_mutual_best
      AND match_tier = 'auto'
),

review_queue AS (
    SELECT *
    FROM {{ ref('int_patient_match_best') }}
    WHERE is_mutual_best
      AND match_tier = 'review'
),

ehr AS (
    SELECT * FROM {{ ref('int_identity_ehr') }}
),

hie AS (
    SELECT * FROM {{ ref('int_identity_hie') }}
),

-- Every EHR patient, with its accepted HIE counterpart where one exists.
from_ehr AS (
    SELECT
        e.ehr_patient_id,
        a.hie_patient_id,
        a.match_score,
        a.match_method,
        CASE
            WHEN a.hie_patient_id IS NOT NULL                 THEN 'both'
            WHEN r.hie_patient_id IS NOT NULL                 THEN 'ehr_only_pending_review'
            ELSE 'ehr_only'
        END                                                   AS record_source,
        r.hie_patient_id                                      AS pending_review_hie_id,
        r.match_score                                         AS pending_review_score
    FROM ehr e
    LEFT JOIN accepted     a ON a.ehr_patient_id = e.ehr_patient_id
    LEFT JOIN review_queue r ON r.ehr_patient_id = e.ehr_patient_id
                            AND a.ehr_patient_id IS NULL
),

-- HIE records that no accepted link claimed: either genuinely unknown to the
-- local EHR, or a duplicate that lost a mutual-best contest.
from_hie AS (
    SELECT
        CAST(NULL AS VARCHAR)                                 AS ehr_patient_id,
        h.hie_patient_id,
        CAST(NULL AS INTEGER)                                 AS match_score,
        CAST(NULL AS VARCHAR)                                 AS match_method,
        CASE
            WHEN d.hie_patient_id IS NOT NULL THEN 'hie_only_duplicate_suspect'
            ELSE 'hie_only'
        END                                                   AS record_source,
        CAST(NULL AS VARCHAR)                                 AS pending_review_hie_id,
        CAST(NULL AS INTEGER)                                 AS pending_review_score
    FROM hie h
    LEFT JOIN (
        SELECT DISTINCT hie_patient_id
        FROM {{ ref('int_patient_match_best') }}
        WHERE is_duplicate_suspect
    ) d ON d.hie_patient_id = h.hie_patient_id
    WHERE h.hie_patient_id NOT IN (SELECT hie_patient_id FROM accepted)
),

linked AS (
    SELECT * FROM from_ehr
    UNION ALL
    SELECT * FROM from_hie
)

SELECT
    'MPI-' || upper(substr(
        to_hex(md5(to_utf8(coalesce(l.ehr_patient_id, l.hie_patient_id)))), 1, 12
    ))                                                        AS mpi_id,
    l.ehr_patient_id,
    l.hie_patient_id,
    l.record_source,
    l.match_score,
    l.match_method,
    l.pending_review_hie_id,
    l.pending_review_score,

    -- Survivorship: EHR first, HIE as fallback.
    coalesce(e.birth_date,  h.birth_date)                     AS birth_date,
    coalesce(e.full_name,   h.full_name)                      AS full_name,
    coalesce(
        nullif(e.gender, 'unknown'),
        nullif(h.gender, 'unknown'),
        'unknown'
    )                                                         AS gender,
    coalesce(e.race,        h.race)                           AS race,
    coalesce(e.ethnicity,   h.ethnicity)                      AS ethnicity,
    coalesce(e.postal_code, h.postal_code)                    AS postal_code,
    coalesce(e.state,       h.state)                          AS state,

    -- Which feeds contributed to this identity at all.
    l.ehr_patient_id IS NOT NULL                              AS has_ehr_record,
    l.hie_patient_id IS NOT NULL                              AS has_hie_record,

    date_diff('year', coalesce(e.birth_date, h.birth_date), current_date) AS age,
    CASE
        WHEN date_diff('year', coalesce(e.birth_date, h.birth_date), current_date) < 18 THEN '0-17'
        WHEN date_diff('year', coalesce(e.birth_date, h.birth_date), current_date) < 35 THEN '18-34'
        WHEN date_diff('year', coalesce(e.birth_date, h.birth_date), current_date) < 50 THEN '35-49'
        WHEN date_diff('year', coalesce(e.birth_date, h.birth_date), current_date) < 65 THEN '50-64'
        ELSE '65+'
    END                                                       AS age_group
FROM linked l
LEFT JOIN ehr e ON e.ehr_patient_id = l.ehr_patient_id
LEFT JOIN hie h ON h.hie_patient_id = l.hie_patient_id
