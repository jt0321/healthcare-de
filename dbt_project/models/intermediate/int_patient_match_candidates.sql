/*
    Score every plausible (EHR patient, HIE patient) pair.

    There is no shared identifier between the two feeds and no national
    patient identifier to fall back on, so identity has to be inferred from
    the demographics. This is the standard two-stage approach:

      1. Blocking. Comparing all 1,123 x ~1,130 pairs is cheap here but is
         quadratic, and at a real population size it is the thing that makes
         naive matching impossible. Three cheap blocking keys each propose a
         small set of candidates; their union is what gets scored. A pair that
         agrees on nothing in any block is never considered -- which is also
         the cost of blocking: it can lose a true match that disagrees badly
         on every blocking key.

      2. Scoring. Agreement on each attribute contributes weight in
         proportion to how much it narrows the population, which is the
         intuition behind Fellegi-Sunter weights. Date of birth and surname
         carry the most; administrative sex carries little on its own but is
         strong evidence *against* a match when the two sides disagree, so it
         scores negative. A field missing on one side scores zero rather than
         negative: an outside feed that omits the ZIP tells us nothing about
         whether this is the same person.

    The weights below are hand-set and total 100 for an exact agreement on
    everything. A production EMPI derives them from measured field
    reliability on its own population and re-estimates them as the data
    drifts; hard-coding them is the shortcut this project takes.
*/

WITH ehr AS (
    SELECT * FROM {{ ref('int_identity_ehr') }}
),

hie AS (
    SELECT * FROM {{ ref('int_identity_hie') }}
),

-- Three blocking passes, unioned to distinct pairs.
blocked AS (
    -- Exact date of birth: the single most selective attribute available.
    SELECT e.ehr_patient_id, h.hie_patient_id
    FROM ehr e
    JOIN hie h ON e.birth_date = h.birth_date

    UNION

    -- Surname that sounds alike, born the same year: catches typos,
    -- hyphenation, and transposed day/month in the date of birth.
    SELECT e.ehr_patient_id, h.hie_patient_id
    FROM ehr e
    JOIN hie h
      ON {{ safe_soundex('e.last_name_root') }} = {{ safe_soundex('h.last_name_root') }}
     AND e.birth_year = h.birth_year

    UNION

    -- Given name that sounds alike in the same ZIP: catches a surname that
    -- changed outright between the two systems.
    SELECT e.ehr_patient_id, h.hie_patient_id
    FROM ehr e
    JOIN hie h
      ON {{ safe_soundex('e.first_name_norm') }} = {{ safe_soundex('h.first_name_norm') }}
     AND e.postal_code = h.postal_code
),

compared AS (
    SELECT
        b.ehr_patient_id,
        b.hie_patient_id,

        e.birth_date                                    AS ehr_birth_date,
        h.birth_date                                    AS hie_birth_date,
        e.last_name_norm                                AS ehr_last_name,
        h.last_name_norm                                AS hie_last_name,
        e.first_name_norm                               AS ehr_first_name,
        h.first_name_norm                               AS hie_first_name,
        e.gender                                        AS ehr_gender,
        h.gender                                        AS hie_gender,
        e.postal_code                                   AS ehr_postal_code,
        h.postal_code                                   AS hie_postal_code,
        e.full_name                                     AS ehr_full_name,
        h.full_name                                     AS hie_full_name,

        -- Date of birth: 30 for an exact match, partial credit when two of
        -- the three components agree. A transposed month and day still
        -- agrees on the year and on the multiset of the other two, which is
        -- why the transposition case is scored explicitly.
        CASE
            WHEN e.birth_date = h.birth_date                                    THEN 30
            WHEN e.birth_year = h.birth_year
             AND e.birth_month = h.birth_day
             AND e.birth_day = h.birth_month                                    THEN 22
            WHEN e.birth_year = h.birth_year AND e.birth_month = h.birth_month  THEN 18
            WHEN e.birth_month = h.birth_month AND e.birth_day = h.birth_day    THEN 14
            WHEN e.birth_year = h.birth_year                                    THEN 8
            ELSE 0
        END                                             AS dob_score,

        -- Surname: exact, then the hyphen-stripped root, then phonetic, then
        -- edit distance. Each fallback is worth less than the one above it.
        CASE
            WHEN e.last_name_norm = h.last_name_norm                            THEN 25
            WHEN e.last_name_root = h.last_name_root                            THEN 22
            WHEN {{ safe_soundex('e.last_name_root') }}
               = {{ safe_soundex('h.last_name_root') }}                         THEN 16
            WHEN levenshtein_distance(e.last_name_root, h.last_name_root) <= 2  THEN 14
            ELSE 0
        END                                             AS last_name_score,

        -- Given name: same ladder, plus credit for an initial-only record,
        -- which is common when a feed carries a truncated registration.
        CASE
            WHEN e.first_name_norm = h.first_name_norm                          THEN 20
            WHEN length(h.first_name_norm) = 1
             AND substr(e.first_name_norm, 1, 1) = h.first_name_norm            THEN 12
            WHEN {{ safe_soundex('e.first_name_norm') }}
               = {{ safe_soundex('h.first_name_norm') }}                        THEN 12
            WHEN levenshtein_distance(e.first_name_norm, h.first_name_norm) <= 2 THEN 11
            ELSE 0
        END                                             AS first_name_score,

        -- Administrative sex: weak confirmation, strong contradiction.
        CASE
            WHEN h.gender = 'unknown' OR e.gender = 'unknown'                   THEN 0
            WHEN e.gender = h.gender                                            THEN 10
            ELSE -15
        END                                             AS gender_score,

        -- Address: a shared ZIP is meaningful in a statewide population; a
        -- different ZIP is weak evidence against, since people move.
        CASE
            WHEN e.postal_code IS NULL OR h.postal_code IS NULL                 THEN 0
            WHEN e.postal_code = h.postal_code                                  THEN 15
            ELSE -5
        END                                             AS postal_code_score

    FROM blocked b
    JOIN ehr e ON e.ehr_patient_id = b.ehr_patient_id
    JOIN hie h ON h.hie_patient_id = b.hie_patient_id
),

scored AS (
    SELECT
        *,
        dob_score + last_name_score + first_name_score + gender_score + postal_code_score
            AS match_score
    FROM compared
)

SELECT
    *,
    /*
        Two thresholds, three outcomes -- the shape every EMPI has. The
        middle band is not a failure of the algorithm; it is the band where
        automatic linkage would cause harm, so it goes to a human. Merging two
        patients who are not the same person puts one person's diagnoses in
        another's chart, which is why the upper threshold is set
        conservatively and the review queue is expected to be non-empty.
    */
    CASE
        WHEN match_score >= 85 THEN 'auto'
        WHEN match_score >= 60 THEN 'review'
        ELSE 'reject'
    END                                                 AS match_tier,
    CASE
        WHEN dob_score = 30 AND last_name_score = 25 AND first_name_score = 20
            THEN 'deterministic'
        ELSE 'probabilistic'
    END                                                 AS match_method
FROM scored
