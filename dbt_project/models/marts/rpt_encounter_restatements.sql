/*
    How much of the encounter table has been corrected since it first loaded.

    Every row the MERGE updates increments its revision counter, so revision
    is a running count of how many times the source has changed its mind about
    that encounter. Voided encounters keep their last known values and carry
    the flag, because a reversal is itself a fact to report -- deleting the
    row would leave the original figure unexplained.

    The money columns matter most: repricing is the common correction, and
    "total claim cost, as restated" is a different number from "total claim
    cost, as first reported." A pipeline that cannot tell them apart will
    quietly disagree with finance.
*/

WITH encounters AS (
    SELECT * FROM {{ ref('stg_encounters') }}
)

SELECT
    revision,
    is_voided,
    count(*)                                            AS encounters,
    count(DISTINCT patient_id)                          AS patients,
    round(sum(total_claim_cost), 2)                     AS total_claim_cost,
    round(avg(total_claim_cost), 2)                     AS avg_claim_cost,
    min(source_updated_at)                              AS earliest_source_update,
    max(source_updated_at)                              AS latest_source_update,
    round(100.0 * count(*) / sum(count(*)) OVER (), 2)  AS pct_of_encounters
FROM encounters
GROUP BY revision, is_voided
ORDER BY revision, is_voided
