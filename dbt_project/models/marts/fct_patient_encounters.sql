{{
    config(
        materialized = 'incremental',
        unique_key = 'encounter_id',
        incremental_strategy = 'merge',
        on_schema_change = 'append_new_columns',
    )
}}

/*
    Encounter grain, resolved to a master patient identity.

    Incremental by merge, keyed on encounter_id. The filter below selects only
    rows whose source system has touched them since the last build, so a
    correction batch rewrites the few thousand encounters it restated instead
    of the whole table -- and, because the strategy is merge rather than
    append, a repriced encounter updates in place rather than appearing twice.

    Two things to know about this model:

    The incremental filter keys on source_updated_at, the source system's own
    last-change time, not on the ingest time. Those differ by however long the
    correction took to arrive, which is the entire point of handling late
    arrival: a claim restated in March and delivered in June has a March
    timestamp and must still be picked up by the June build.

    Because the filter is on the encounter, a change to a *patient's* master
    identity does not by itself pull the encounter back into the build. The
    mpi_id here can therefore go stale after a re-link until the next full
    refresh (`dbt run --full-refresh`). Resolving that properly means either
    tracking identity changes as their own trigger or keeping mpi_id out of
    the fact and joining it at query time; this project takes the simpler
    route and documents the staleness rather than hiding it.
*/

WITH encounters AS (
    SELECT * FROM {{ ref('stg_encounters') }}

    {% if is_incremental() %}
    WHERE source_updated_at > (
        SELECT coalesce(max(source_updated_at), TIMESTAMP '1970-01-01 00:00:00')
        FROM {{ this }}
    )
    {% endif %}
),

master AS (
    SELECT * FROM {{ ref('dim_patient_master') }}
    WHERE ehr_patient_id IS NOT NULL
),

patients AS (
    SELECT * FROM {{ ref('stg_patients') }}
)

SELECT
    e.encounter_id,
    e.patient_id,
    m.mpi_id,
    m.hie_patient_id,
    m.record_source                     AS patient_record_source,
    m.match_score                       AS patient_match_score,
    p.full_name,
    p.gender,
    p.race,
    p.age,
    p.age_group,
    e.encounter_start,
    e.encounter_stop,
    e.encounter_class,
    e.encounter_code,
    e.encounter_description,
    e.reason_code,
    e.reason_description,
    e.payer_id,
    e.total_claim_cost,
    e.encounter_duration_minutes,
    date_trunc('month', e.encounter_start)  AS encounter_month,

    -- Restatement metadata carried through to the mart, so a consumer can
    -- tell a figure that has been corrected from one that has not.
    e.is_voided,
    e.is_restated,
    e.revision,
    e.source_updated_at
FROM encounters e
LEFT JOIN patients p ON e.patient_id = p.patient_id
LEFT JOIN master   m ON e.patient_id = m.ehr_patient_id
