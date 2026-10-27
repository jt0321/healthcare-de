/*
    Reduce the scored candidates to at most one link per patient, per side.

    Taking each EHR patient's highest-scoring candidate is not enough: two
    HIE records can both point at the same person, and accepting both would
    attach one person's records to two master identities (or, worse, pull a
    second person's data in). Requiring the choice to be *mutual* -- each is
    the other's best available candidate -- yields a one-to-one assignment.

    The records that lose a mutual-best contest are kept, not dropped. An HIE
    record whose only strong candidate was already claimed is the signature of
    a duplicate inside the sending system, and that is a finding worth
    reporting rather than a row to discard.
*/

WITH ranked AS (
    SELECT
        *,
        row_number() OVER (
            PARTITION BY ehr_patient_id
            ORDER BY match_score DESC, hie_patient_id
        )                                               AS rank_for_ehr,
        row_number() OVER (
            PARTITION BY hie_patient_id
            ORDER BY match_score DESC, ehr_patient_id
        )                                               AS rank_for_hie
    FROM {{ ref('int_patient_match_candidates') }}
    WHERE match_tier IN ('auto', 'review')
)

SELECT
    *,
    rank_for_ehr = 1 AND rank_for_hie = 1               AS is_mutual_best,
    /*
        Scored highly and is the best candidate for its own id, but lost the
        contest for the EHR patient to a better-scoring record. Two records in
        the sending system pointing at one local patient is the signature of a
        duplicate inside that system.

        Note which side each rank is tested on. A twin pair carries two
        distinct HIE ids, so each twin is rank 1 within its own hie partition;
        what separates the loser is its rank against the shared EHR patient.
    */
    rank_for_ehr > 1 AND rank_for_hie = 1               AS is_duplicate_suspect
FROM ranked
