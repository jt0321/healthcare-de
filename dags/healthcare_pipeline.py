import glob
import json
import os
import boto3
import pandas as pd
from datetime import datetime, timedelta, timezone

from airflow import DAG
from airflow.models import Variable
from airflow.operators.python import PythonOperator
from airflow.sensors.python import PythonSensor
from airflow.providers.docker.operators.docker import DockerOperator
from airflow.providers.trino.operators.trino import TrinoOperator

import hie_feed

default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

TRINO_CONN_ID = 'trino_default'
CSV_BASE = '/opt/airflow/data/csv'
DELTA_BASE = '/opt/airflow/data/delta'
FHIR_BASE = '/opt/airflow/data/fhir'
STAGING_BUCKET = 'healthcare'
STAGING_PREFIX = 'staging'

MINIO_CLIENT = dict(
    endpoint_url='http://minio:9000',
    aws_access_key_id='admin',
    aws_secret_access_key='password123',
)


WATERMARK_VAR = 'synthea_last_ingest_mtime'

SOURCE_FILES = [
    f'{CSV_BASE}/patients.csv',
    f'{CSV_BASE}/encounters.csv',
    f'{CSV_BASE}/conditions.csv',
]


def _delta_files(table_name: str) -> list[str]:
    """Correction batches for one table, oldest first (see scripts/simulate_amendments.py)."""
    return sorted(glob.glob(f'{DELTA_BASE}/{table_name}_*.csv'))


def _watched_files() -> list[str]:
    """Everything whose arrival should wake the pipeline: base extracts and corrections."""
    return SOURCE_FILES + sorted(glob.glob(f'{DELTA_BASE}/*.csv'))


def check_new_synthea_data():
    last_mtime = float(Variable.get(WATERMARK_VAR, default_var=0))
    return any(
        os.path.exists(p) and os.path.getmtime(p) > last_mtime
        for p in _watched_files()
    )


def update_watermark():
    mtimes = [os.path.getmtime(p) for p in _watched_files() if os.path.exists(p)]
    if mtimes:
        Variable.set(WATERMARK_VAR, max(mtimes))


def _s3_client():
    return boto3.client('s3', **MINIO_CLIENT)


def _read_source_csv(path: str) -> pd.DataFrame:
    """
    Read a source extract without letting pandas guess at types.

    Everything comes back as text. Healthcare extracts are full of columns
    that look numeric and are not: a Massachusetts ZIP code inferred as an
    integer loses its leading zero, so 02494 becomes 2494 and stops matching
    the same ZIP delivered as a string by another feed. SNOMED and SNOMED-CT
    codes, NPIs, and account numbers all have the same problem. The staging
    layer is typed as VARCHAR for exactly this reason -- casting belongs in
    the ingest SQL, where it is declared rather than inferred.
    """
    return pd.read_csv(path, dtype=str, low_memory=False)


def _coerce_numeric(df: pd.DataFrame, columns: tuple) -> pd.DataFrame:
    """Cast back the few staged columns that really are numeric."""
    for column in columns:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors='coerce')
    return df


def _upload_parquet(df: pd.DataFrame, table_name: str):
    parquet_path = f'/tmp/{table_name}.parquet'
    df.to_parquet(parquet_path, index=False)
    _s3_client().upload_file(
        parquet_path,
        STAGING_BUCKET,
        f'{STAGING_PREFIX}/{table_name}/{table_name}.parquet',
    )
    print(f"Staged {len(df)} rows to s3://{STAGING_BUCKET}/{STAGING_PREFIX}/{table_name}/")


def _stage_csv_to_parquet(table_name: str, column_map: dict, numeric: tuple = ()):
    """Read a Synthea CSV, rename columns, upload as Parquet to MinIO staging area."""
    df = _read_source_csv(f'{CSV_BASE}/{table_name}.csv')
    df = df.rename(columns=column_map)[list(column_map.values())]
    _upload_parquet(_coerce_numeric(df, numeric), table_name)


def _stage_with_corrections(table_name: str, column_map: dict, numeric: tuple = ()):
    """
    Stage a base extract together with every correction batch that has landed.

    All versions of a row are staged, not just the newest. Deciding which one
    wins is the ingest layer's job -- it has the target table to compare
    against, and keeping the losers visible in staging means a restatement can
    be audited after the fact rather than vanishing at read time.

    The base extract carries no update timestamp of its own, so the file's
    mtime stands in for "when the source produced this". Every correction
    batch carries a real one.
    """
    base_path = f'{CSV_BASE}/{table_name}.csv'
    base = _read_source_csv(base_path)
    base['CHANGE_TYPE'] = 'insert'
    base['SOURCE_UPDATED_AT'] = (
        datetime.fromtimestamp(os.path.getmtime(base_path), tz=timezone.utc)
        .isoformat(timespec='seconds')
        .replace('+00:00', 'Z')
    )

    batches = [base]
    for path in _delta_files(table_name):
        batches.append(_read_source_csv(path))
    print(f"{table_name}: base extract + {len(batches) - 1} correction batch(es)")

    full_map = {**column_map, 'CHANGE_TYPE': 'change_type', 'SOURCE_UPDATED_AT': 'source_updated_at'}
    df = pd.concat(batches, ignore_index=True)
    df = df.rename(columns=full_map)[list(full_map.values())]
    _upload_parquet(_coerce_numeric(df, numeric), table_name)


def stage_patients():
    _stage_with_corrections('patients', {
        'Id': 'patient_id',
        'BIRTHDATE': 'birth_date',
        'GENDER': 'gender',
        'RACE': 'race',
        'ETHNICITY': 'ethnicity',
        'FIRST': 'first_name',
        'LAST': 'last_name',
        'CITY': 'city',
        'STATE': 'state',
        'ZIP': 'postal_code',
    })


def stage_encounters():
    _stage_with_corrections('encounters', {
        'Id': 'encounter_id',
        'PATIENT': 'patient_id',
        'START': 'encounter_start',
        'STOP': 'encounter_stop',
        'ENCOUNTERCLASS': 'encounter_class',
        'CODE': 'encounter_code',
        'DESCRIPTION': 'encounter_description',
        'REASONCODE': 'reason_code',
        'REASONDESCRIPTION': 'reason_description',
        'PAYER': 'payer_id',
        'TOTAL_CLAIM_COST': 'total_claim_cost',
    }, numeric=('total_claim_cost',))


def _us_core_race(extensions: list) -> str | None:
    """Extract race text from US Core race extension."""
    for ext in extensions or []:
        if ext.get('url') == 'http://hl7.org/fhir/us/core/StructureDefinition/us-core-race':
            for sub in ext.get('extension', []):
                if sub.get('url') == 'text':
                    return sub.get('valueString')
    return None


def _us_core_ethnicity(extensions: list) -> str | None:
    """Extract ethnicity text from US Core ethnicity extension."""
    for ext in extensions or []:
        if ext.get('url') == 'http://hl7.org/fhir/us/core/StructureDefinition/us-core-ethnicity':
            for sub in ext.get('extension', []):
                if sub.get('url') == 'text':
                    return sub.get('valueString')
    return None


def stage_fhir_patients():
    """
    Flatten Patient resources from Synthea FHIR R4 bundles into an HIE feed.

    The FHIR path is treated as a feed from an outside health information
    exchange rather than a second view of the local EHR: hie_feed rewrites
    every identifier into the sending system's own id space and perturbs the
    demographics, so there is no shared key between this table and the CSV
    path. Resolving the two onto one person is the EMPI's job -- see the dbt
    models under models/intermediate/ and dim_patient_master.
    """
    records = []
    roles = {'normal': 0, 'duplicate': 0, 'orphan': 0, 'absent': 0}

    for path in glob.glob(f'{FHIR_BASE}/*.json'):
        with open(path) as f:
            bundle = json.load(f)

        for entry in bundle.get('entry', []):
            resource = entry.get('resource', {})
            if resource.get('resourceType') != 'Patient':
                continue

            uuid = resource.get('id')
            role = hie_feed.feed_role(uuid)
            roles[role] += 1
            if role == 'absent':
                continue

            name = next(
                (n for n in resource.get('name', []) if n.get('use') == 'official'),
                resource.get('name', [{}])[0],
            )
            address = resource.get('address', [{}])[0]
            extensions = resource.get('extension', [])

            record = {
                'patient_id':   hie_feed.hie_id('HIEPT', uuid),
                'birth_date':   resource.get('birthDate'),
                'gender':       resource.get('gender'),
                'family_name':  name.get('family'),
                'given_name':   ' '.join(name.get('given', [])),
                'postal_code':  address.get('postalCode'),
                'state':        address.get('state'),
                'race':         _us_core_race(extensions),
                'ethnicity':    _us_core_ethnicity(extensions),
            }

            if role == 'orphan':
                record = hie_feed.orphan_identity(record, uuid)
            else:
                record = hie_feed.perturb_patient(record, uuid)

            records.append(record)

            if role == 'duplicate':
                # The sending system holds this person twice under two local
                # ids, with the demographics entered independently. An EMPI
                # has to collapse them or it will double-count the patient.
                twin = hie_feed.perturb_patient(
                    {**record, 'patient_id': f'HIEPT-{hie_feed.duplicate_suffix(uuid)}'},
                    uuid + ':twin',
                )
                records.append(twin)

    if not records:
        raise ValueError(f"No FHIR Patient resources found in {FHIR_BASE}")

    print(f"HIE feed composition: {roles} -> {len(records)} rows staged")
    _upload_parquet(pd.DataFrame(records), 'fhir_patients')


def _ref_id(reference: str | None) -> str | None:
    """Strip resource type prefix from a FHIR reference, e.g. 'Patient/uuid' → 'uuid'."""
    if reference and '/' in reference:
        return reference.split('/')[-1]
    return reference


def stage_fhir_encounters():
    """
    Flatten Encounter resources from Synthea FHIR R4 bundles.
    Encounter.class is a v3-ActCode Coding (AMB, EMER, IMP, etc.).
    Encounter.type carries the SNOMED visit type.

    Identifiers are mapped into the HIE's own id space, and encounters for
    patients the HIE does not hold are dropped, so this table stays internally
    consistent with fhir_patients and shares no key with the CSV path.
    """
    records = []
    for path in glob.glob(f'{FHIR_BASE}/*.json'):
        with open(path) as f:
            bundle = json.load(f)

        for entry in bundle.get('entry', []):
            resource = entry.get('resource', {})
            if resource.get('resourceType') != 'Encounter':
                continue

            patient_uuid = _ref_id(resource.get('subject', {}).get('reference'))
            if hie_feed.feed_role(patient_uuid) == 'absent':
                continue

            enc_type = (resource.get('type') or [{}])[0]
            type_coding = (enc_type.get('coding') or [{}])[0]

            reason = (resource.get('reasonCode') or [{}])[0]
            reason_coding = (reason.get('coding') or [{}])[0]

            period = resource.get('period', {})

            records.append({
                'encounter_id':          hie_feed.hie_id('HIEENC', resource.get('id')),
                'patient_id':            hie_feed.hie_id('HIEPT', patient_uuid),
                'encounter_start':       period.get('start'),
                'encounter_stop':        period.get('end'),
                'encounter_class_code':  resource.get('class', {}).get('code'),
                'encounter_type_code':   type_coding.get('code'),
                'encounter_type_display': type_coding.get('display'),
                'reason_code':           reason_coding.get('code'),
                'reason_display':        reason_coding.get('display'),
                'status':                resource.get('status'),
            })

    if not records:
        raise ValueError(f"No FHIR Encounter resources found in {FHIR_BASE}")

    _upload_parquet(pd.DataFrame(records), 'fhir_encounters')


def stage_fhir_conditions():
    """
    Flatten Condition resources from Synthea FHIR R4 bundles.
    clinicalStatus and verificationStatus are CodeableConcepts, not plain strings.
    onset/abatement may be dateTime or Period depending on the condition type.
    """
    records = []
    for path in glob.glob(f'{FHIR_BASE}/*.json'):
        with open(path) as f:
            bundle = json.load(f)

        for entry in bundle.get('entry', []):
            resource = entry.get('resource', {})
            if resource.get('resourceType') != 'Condition':
                continue

            patient_uuid = _ref_id(resource.get('subject', {}).get('reference'))
            if hie_feed.feed_role(patient_uuid) == 'absent':
                continue

            code_coding = (resource.get('code', {}).get('coding') or [{}])[0]
            clinical_status = (
                resource.get('clinicalStatus', {}).get('coding') or [{}]
            )[0].get('code')
            verification_status = (
                resource.get('verificationStatus', {}).get('coding') or [{}]
            )[0].get('code')

            # onset can be onsetDateTime or onsetPeriod
            onset = resource.get('onsetDateTime') or (
                resource.get('onsetPeriod') or {}
            ).get('start')
            abatement = resource.get('abatementDateTime') or (
                resource.get('abatementPeriod') or {}
            ).get('end')

            records.append({
                'condition_id':          hie_feed.hie_id('HIECND', resource.get('id')),
                'patient_id':            hie_feed.hie_id('HIEPT', patient_uuid),
                'encounter_id':          hie_feed.hie_id(
                    'HIEENC', _ref_id((resource.get('encounter') or {}).get('reference'))),
                'condition_code':        code_coding.get('code'),
                'condition_display':     code_coding.get('display'),
                'condition_text':        resource.get('code', {}).get('text'),
                'clinical_status':       clinical_status,
                'verification_status':   verification_status,
                'onset_date':            onset,
                'abatement_date':        abatement,
            })

    if not records:
        raise ValueError(f"No FHIR Condition resources found in {FHIR_BASE}")

    _upload_parquet(pd.DataFrame(records), 'fhir_conditions')


def stage_conditions():
    _stage_csv_to_parquet('conditions', {
        'PATIENT': 'patient_id',
        'ENCOUNTER': 'encounter_id',
        'START': 'condition_start',
        'STOP': 'condition_stop',
        'CODE': 'condition_code',
        'DESCRIPTION': 'condition_description',
    })


with DAG(
    'healthcare_data_pipeline',
    default_args=default_args,
    description='Daily ingest of Synthea data into Polaris-managed Iceberg tables via Trino',
    schedule_interval='@daily',
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=['healthcare'],
) as dag:

    # Phase 1: stage CSVs + FHIR bundles to MinIO as Parquet (all parallel)
    stage_patients_task = PythonOperator(
        task_id='stage_patients',
        python_callable=stage_patients,
    )

    stage_encounters_task = PythonOperator(
        task_id='stage_encounters',
        python_callable=stage_encounters,
    )

    stage_conditions_task = PythonOperator(
        task_id='stage_conditions',
        python_callable=stage_conditions,
    )

    stage_fhir_patients_task = PythonOperator(
        task_id='stage_fhir_patients',
        python_callable=stage_fhir_patients,
    )

    stage_fhir_encounters_task = PythonOperator(
        task_id='stage_fhir_encounters',
        python_callable=stage_fhir_encounters,
    )

    stage_fhir_conditions_task = PythonOperator(
        task_id='stage_fhir_conditions',
        python_callable=stage_fhir_conditions,
    )

    # Phase 2: create Hive staging schema and external tables over MinIO Parquet
    create_staging_schema = TrinoOperator(
        task_id='create_staging_schema',
        trino_conn_id=TRINO_CONN_ID,
        sql="CREATE SCHEMA IF NOT EXISTS file.staging WITH (location = 's3://healthcare/staging/')",
    )

    # Polaris registers the 'default' catalog on bootstrap, but not a namespace within it;
    # CTAS into iceberg.default.* fails with "Schema default not found" without this.
    create_iceberg_schema = TrinoOperator(
        task_id='create_iceberg_schema',
        trino_conn_id=TRINO_CONN_ID,
        sql="CREATE SCHEMA IF NOT EXISTS iceberg.default",
    )

    create_staging_patients = TrinoOperator(
        task_id='create_staging_patients',
        trino_conn_id=TRINO_CONN_ID,
        sql="""
            CREATE TABLE IF NOT EXISTS file.staging.patients (
                patient_id      VARCHAR,
                birth_date      VARCHAR,
                gender          VARCHAR,
                race            VARCHAR,
                ethnicity       VARCHAR,
                first_name      VARCHAR,
                last_name       VARCHAR,
                city            VARCHAR,
                state           VARCHAR,
                postal_code     VARCHAR,
                change_type     VARCHAR,
                source_updated_at VARCHAR
            ) WITH (
                external_location = 's3://healthcare/staging/patients/',
                format = 'PARQUET'
            )
        """,
    )

    create_staging_encounters = TrinoOperator(
        task_id='create_staging_encounters',
        trino_conn_id=TRINO_CONN_ID,
        sql="""
            CREATE TABLE IF NOT EXISTS file.staging.encounters (
                encounter_id        VARCHAR,
                patient_id          VARCHAR,
                encounter_start     VARCHAR,
                encounter_stop      VARCHAR,
                encounter_class     VARCHAR,
                encounter_code      VARCHAR,
                encounter_description VARCHAR,
                reason_code         VARCHAR,
                reason_description  VARCHAR,
                payer_id            VARCHAR,
                total_claim_cost    DOUBLE,
                change_type         VARCHAR,
                source_updated_at   VARCHAR
            ) WITH (
                external_location = 's3://healthcare/staging/encounters/',
                format = 'PARQUET'
            )
        """,
    )

    create_staging_conditions = TrinoOperator(
        task_id='create_staging_conditions',
        trino_conn_id=TRINO_CONN_ID,
        sql="""
            CREATE TABLE IF NOT EXISTS file.staging.conditions (
                patient_id          VARCHAR,
                encounter_id        VARCHAR,
                condition_start     VARCHAR,
                condition_stop      VARCHAR,
                condition_code      VARCHAR,
                condition_description VARCHAR
            ) WITH (
                external_location = 's3://healthcare/staging/conditions/',
                format = 'PARQUET'
            )
        """,
    )

    # ──────────────────────────────────────────────────────────────────────
    # Phase 3: load the Iceberg tables.
    #
    # The CSV path is the local EHR's nightly extract, and its rows get
    # restated: an encounter is repriced, re-coded, or voided weeks after it
    # first loaded. Those tables are therefore created once with explicit DDL
    # and then MERGEd into, so a correction updates the row in place and the
    # table keeps a per-row revision count and the source's own update time.
    # Rebuilding with CREATE OR REPLACE would make every run look like a
    # first load and would discard Iceberg's snapshot lineage, which is the
    # record of what the table said when a report was run against it.
    #
    # The FHIR path is an outside HIE feed delivered as a full snapshot each
    # time, so CREATE OR REPLACE is the honest load pattern there. Conditions
    # have no correction feed and are loaded the same way.
    # ──────────────────────────────────────────────────────────────────────
    create_patients_table = TrinoOperator(
        task_id='create_patients_table',
        trino_conn_id=TRINO_CONN_ID,
        sql="""
            CREATE TABLE IF NOT EXISTS iceberg.default.patients (
                patient_id          VARCHAR,
                birth_date          DATE,
                first_name          VARCHAR,
                last_name           VARCHAR,
                full_name           VARCHAR,
                gender              VARCHAR,
                race                VARCHAR,
                ethnicity           VARCHAR,
                city                VARCHAR,
                state               VARCHAR,
                postal_code         VARCHAR,
                is_voided           BOOLEAN,
                source_updated_at   TIMESTAMP(6),
                revision            INTEGER,
                ingested_at         TIMESTAMP(6)
            )
        """,
    )

    merge_patients = TrinoOperator(
        task_id='merge_patients',
        trino_conn_id=TRINO_CONN_ID,
        sql="""
            MERGE INTO iceberg.default.patients t
            USING (
                SELECT *
                FROM (
                    SELECT
                        patient_id,
                        CAST(birth_date AS DATE)                AS birth_date,
                        first_name,
                        last_name,
                        first_name || ' ' || last_name          AS full_name,
                        gender,
                        race,
                        ethnicity,
                        city,
                        state,
                        postal_code,
                        change_type,
                        CAST(from_iso8601_timestamp(source_updated_at) AS TIMESTAMP(6))
                                                                AS source_updated_at,
                        ROW_NUMBER() OVER (
                            PARTITION BY patient_id
                            ORDER BY from_iso8601_timestamp(source_updated_at) DESC
                        )                                       AS recency_rank
                    FROM file.staging.patients
                )
                WHERE recency_rank = 1
            ) s
            ON t.patient_id = s.patient_id
            WHEN MATCHED AND s.source_updated_at > t.source_updated_at THEN UPDATE SET
                birth_date        = s.birth_date,
                first_name        = s.first_name,
                last_name         = s.last_name,
                full_name         = s.full_name,
                gender            = s.gender,
                race              = s.race,
                ethnicity         = s.ethnicity,
                city              = s.city,
                state             = s.state,
                postal_code       = s.postal_code,
                is_voided         = s.change_type = 'void',
                source_updated_at = s.source_updated_at,
                revision          = t.revision + 1,
                ingested_at       = CAST(localtimestamp AS TIMESTAMP(6))
            WHEN NOT MATCHED THEN INSERT (
                patient_id, birth_date, first_name, last_name, full_name, gender,
                race, ethnicity, city, state, postal_code, is_voided,
                source_updated_at, revision, ingested_at
            ) VALUES (
                s.patient_id, s.birth_date, s.first_name, s.last_name, s.full_name, s.gender,
                s.race, s.ethnicity, s.city, s.state, s.postal_code, s.change_type = 'void',
                s.source_updated_at, 1, CAST(localtimestamp AS TIMESTAMP(6))
            )
        """,
    )

    create_encounters_table = TrinoOperator(
        task_id='create_encounters_table',
        trino_conn_id=TRINO_CONN_ID,
        sql="""
            CREATE TABLE IF NOT EXISTS iceberg.default.encounters (
                encounter_id            VARCHAR,
                patient_id              VARCHAR,
                encounter_start         TIMESTAMP(6),
                encounter_stop          TIMESTAMP(6),
                encounter_class         VARCHAR,
                encounter_code          VARCHAR,
                encounter_description   VARCHAR,
                reason_code             VARCHAR,
                reason_description      VARCHAR,
                payer_id                VARCHAR,
                total_claim_cost        DOUBLE,
                is_voided               BOOLEAN,
                source_updated_at       TIMESTAMP(6),
                revision                INTEGER,
                ingested_at             TIMESTAMP(6)
            )
        """,
    )

    merge_encounters = TrinoOperator(
        task_id='merge_encounters',
        trino_conn_id=TRINO_CONN_ID,
        sql="""
            MERGE INTO iceberg.default.encounters t
            USING (
                SELECT *
                FROM (
                    SELECT
                        encounter_id,
                        patient_id,
                        CAST(from_iso8601_timestamp(encounter_start) AS TIMESTAMP(6))
                                                                AS encounter_start,
                        CAST(from_iso8601_timestamp(encounter_stop)  AS TIMESTAMP(6))
                                                                AS encounter_stop,
                        encounter_class,
                        encounter_code,
                        encounter_description,
                        reason_code,
                        reason_description,
                        payer_id,
                        total_claim_cost,
                        change_type,
                        CAST(from_iso8601_timestamp(source_updated_at) AS TIMESTAMP(6))
                                                                AS source_updated_at,
                        ROW_NUMBER() OVER (
                            PARTITION BY encounter_id
                            ORDER BY from_iso8601_timestamp(source_updated_at) DESC
                        )                                       AS recency_rank
                    FROM file.staging.encounters
                )
                WHERE recency_rank = 1
            ) s
            ON t.encounter_id = s.encounter_id
            WHEN MATCHED AND s.source_updated_at > t.source_updated_at THEN UPDATE SET
                patient_id            = s.patient_id,
                encounter_start       = s.encounter_start,
                encounter_stop        = s.encounter_stop,
                encounter_class       = s.encounter_class,
                encounter_code        = s.encounter_code,
                encounter_description = s.encounter_description,
                reason_code           = s.reason_code,
                reason_description    = s.reason_description,
                payer_id              = s.payer_id,
                total_claim_cost      = s.total_claim_cost,
                is_voided             = s.change_type = 'void',
                source_updated_at     = s.source_updated_at,
                revision              = t.revision + 1,
                ingested_at           = CAST(localtimestamp AS TIMESTAMP(6))
            WHEN NOT MATCHED THEN INSERT (
                encounter_id, patient_id, encounter_start, encounter_stop, encounter_class,
                encounter_code, encounter_description, reason_code, reason_description,
                payer_id, total_claim_cost, is_voided, source_updated_at, revision, ingested_at
            ) VALUES (
                s.encounter_id, s.patient_id, s.encounter_start, s.encounter_stop, s.encounter_class,
                s.encounter_code, s.encounter_description, s.reason_code, s.reason_description,
                s.payer_id, s.total_claim_cost, s.change_type = 'void',
                s.source_updated_at, 1, CAST(localtimestamp AS TIMESTAMP(6))
            )
        """,
    )

    ingest_conditions = TrinoOperator(
        task_id='ingest_conditions',
        trino_conn_id=TRINO_CONN_ID,
        sql="""
            CREATE OR REPLACE TABLE iceberg.default.conditions AS
            SELECT
                patient_id,
                encounter_id,
                CAST(condition_start AS DATE)    AS condition_start,
                CAST(condition_stop  AS DATE)    AS condition_stop,
                condition_code,
                condition_description
            FROM file.staging.conditions
        """,
    )

    wait_for_new_data = PythonSensor(
        task_id='wait_for_new_synthea_data',
        python_callable=check_new_synthea_data,
        poke_interval=300,
        timeout=3600,
        mode='poke',
    )

    staging_tasks = [
        stage_patients_task,
        stage_encounters_task,
        stage_conditions_task,
        stage_fhir_patients_task,
        stage_fhir_encounters_task,
        stage_fhir_conditions_task,
    ]

    wait_for_new_data >> staging_tasks >> create_staging_schema
    wait_for_new_data >> create_iceberg_schema

    # External table DDL runs after schema exists
    create_staging_fhir_patients = TrinoOperator(
        task_id='create_staging_fhir_patients',
        trino_conn_id=TRINO_CONN_ID,
        sql="""
            CREATE TABLE IF NOT EXISTS file.staging.fhir_patients (
                patient_id      VARCHAR,
                birth_date      VARCHAR,
                gender          VARCHAR,
                family_name     VARCHAR,
                given_name      VARCHAR,
                postal_code     VARCHAR,
                state           VARCHAR,
                race            VARCHAR,
                ethnicity       VARCHAR
            ) WITH (
                external_location = 's3://healthcare/staging/fhir_patients/',
                format = 'PARQUET'
            )
        """,
    )

    ingest_fhir_patients = TrinoOperator(
        task_id='ingest_fhir_patients',
        trino_conn_id=TRINO_CONN_ID,
        sql="""
            CREATE OR REPLACE TABLE iceberg.default.fhir_patients AS
            SELECT
                patient_id,
                CAST(birth_date AS DATE)            AS birth_date,
                gender,
                family_name,
                given_name,
                postal_code,
                state,
                race,
                ethnicity
            FROM file.staging.fhir_patients
        """,
    )

    create_staging_fhir_encounters = TrinoOperator(
        task_id='create_staging_fhir_encounters',
        trino_conn_id=TRINO_CONN_ID,
        sql="""
            CREATE TABLE IF NOT EXISTS file.staging.fhir_encounters (
                encounter_id            VARCHAR,
                patient_id              VARCHAR,
                encounter_start         VARCHAR,
                encounter_stop          VARCHAR,
                encounter_class_code    VARCHAR,
                encounter_type_code     VARCHAR,
                encounter_type_display  VARCHAR,
                reason_code             VARCHAR,
                reason_display          VARCHAR,
                status                  VARCHAR
            ) WITH (
                external_location = 's3://healthcare/staging/fhir_encounters/',
                format = 'PARQUET'
            )
        """,
    )

    create_staging_fhir_conditions = TrinoOperator(
        task_id='create_staging_fhir_conditions',
        trino_conn_id=TRINO_CONN_ID,
        sql="""
            CREATE TABLE IF NOT EXISTS file.staging.fhir_conditions (
                condition_id            VARCHAR,
                patient_id              VARCHAR,
                encounter_id            VARCHAR,
                condition_code          VARCHAR,
                condition_display       VARCHAR,
                condition_text          VARCHAR,
                clinical_status         VARCHAR,
                verification_status     VARCHAR,
                onset_date              VARCHAR,
                abatement_date          VARCHAR
            ) WITH (
                external_location = 's3://healthcare/staging/fhir_conditions/',
                format = 'PARQUET'
            )
        """,
    )

    ingest_fhir_encounters = TrinoOperator(
        task_id='ingest_fhir_encounters',
        trino_conn_id=TRINO_CONN_ID,
        sql="""
            CREATE OR REPLACE TABLE iceberg.default.fhir_encounters AS
            SELECT
                encounter_id,
                patient_id,
                CAST(from_iso8601_timestamp(encounter_start) AS TIMESTAMP)  AS encounter_start,
                CAST(from_iso8601_timestamp(encounter_stop)  AS TIMESTAMP)  AS encounter_stop,
                encounter_class_code,
                encounter_type_code,
                encounter_type_display,
                reason_code,
                reason_display,
                status
            FROM file.staging.fhir_encounters
        """,
    )

    ingest_fhir_conditions = TrinoOperator(
        task_id='ingest_fhir_conditions',
        trino_conn_id=TRINO_CONN_ID,
        sql="""
            CREATE OR REPLACE TABLE iceberg.default.fhir_conditions AS
            SELECT
                condition_id,
                patient_id,
                encounter_id,
                condition_code,
                condition_display,
                condition_text,
                clinical_status,
                verification_status,
                CAST(from_iso8601_timestamp(onset_date)      AS DATE)   AS onset_date,
                CAST(from_iso8601_timestamp(abatement_date)  AS DATE)   AS abatement_date,
                abatement_date IS NULL          AS is_active
            FROM file.staging.fhir_conditions
        """,
    )

    create_staging_schema >> [
        create_staging_patients,
        create_staging_encounters,
        create_staging_conditions,
        create_staging_fhir_patients,
        create_staging_fhir_encounters,
        create_staging_fhir_conditions,
    ]

    # Ingest into Iceberg after external tables are defined and the iceberg.default namespace exists
    create_staging_patients        >> create_patients_table   >> merge_patients
    create_staging_encounters      >> create_encounters_table >> merge_encounters
    create_staging_conditions      >> ingest_conditions
    create_staging_fhir_patients   >> ingest_fhir_patients
    create_staging_fhir_encounters >> ingest_fhir_encounters
    create_staging_fhir_conditions >> ingest_fhir_conditions

    create_iceberg_schema >> [
        create_patients_table,
        create_encounters_table,
        ingest_conditions,
        ingest_fhir_patients,
        ingest_fhir_encounters,
        ingest_fhir_conditions,
    ]

    all_ingest = [
        merge_patients,
        merge_encounters,
        ingest_conditions,
        ingest_fhir_patients,
        ingest_fhir_encounters,
        ingest_fhir_conditions,
    ]

    # dbt runs in its own container (built from Dockerfile.dbt) rather than inside the
    # Airflow image: dbt-core's dependency pins conflict with Airflow's, so DockerOperator
    # drives the sibling `dbt` image over the host docker socket instead.
    #
    # `snapshot` runs before `run`: it captures the version of each patient row that the
    # current load produced, so the SCD2 dimension records the state the mart is about to
    # be built on rather than the state after the next correction batch lands.
    dbt_snapshot = DockerOperator(
        task_id='dbt_snapshot',
        image='healthcare-de-dbt',
        command='snapshot',
        docker_url='unix://var/run/docker.sock',
        network_mode='healthcare-de_default',
        auto_remove='success',
        mount_tmp_dir=False,
    )

    dbt_run = DockerOperator(
        task_id='dbt_run',
        image='healthcare-de-dbt',
        command='run',
        docker_url='unix://var/run/docker.sock',
        network_mode='healthcare-de_default',
        auto_remove='success',
        mount_tmp_dir=False,
    )

    dbt_test = DockerOperator(
        task_id='dbt_test',
        image='healthcare-de-dbt',
        command='test',
        docker_url='unix://var/run/docker.sock',
        network_mode='healthcare-de_default',
        auto_remove='success',
        mount_tmp_dir=False,
    )

    update_watermark_task = PythonOperator(
        task_id='update_watermark',
        python_callable=update_watermark,
    )

    all_ingest >> update_watermark_task >> dbt_snapshot >> dbt_run >> dbt_test
