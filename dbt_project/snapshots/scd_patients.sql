{#
    Slowly changing dimension, type 2, over the EHR patient demographics.

    The Iceberg patients table holds current state only -- the MERGE
    overwrites a row when a correction supersedes it. That is the right shape
    for "who is this patient now" and the wrong shape for every question that
    reaches into the past: which address was on file when the claim was
    submitted, what the chart said on the date of service, whether a report
    run last quarter would reproduce today.

    dbt's snapshot does the versioning. The `timestamp` strategy is used
    rather than `check` because the source carries a real last-changed time
    from the sending system; comparing column values would also detect the
    change, but would date it to when this pipeline noticed rather than to
    when the source made it.

    The snapshot is captured once per pipeline run, so it records state at
    build time. A row corrected twice between two runs contributes one
    version, not two -- the usual limitation of snapshotting a mutable source,
    and the reason the staging layer keeps every delivered version of a row.
#}

{% snapshot scd_patients %}

{{
    config(
        target_schema = 'default',
        unique_key = 'patient_id',
        strategy = 'timestamp',
        updated_at = 'source_updated_at',
        hard_deletes = 'invalidate',
    )
}}

SELECT
    patient_id,
    birth_date,
    first_name,
    last_name,
    full_name,
    gender,
    race,
    ethnicity,
    city,
    state,
    postal_code,
    is_voided,
    revision,
    source_updated_at
FROM iceberg.default.patients

{% endsnapshot %}
