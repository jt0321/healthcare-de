import glob
import json
import os
import boto3
import pandas as pd
from datetime import datetime, timedelta

from airflow import DAG
from airflow.models import Variable
from airflow.operators.python import PythonOperator
from airflow.sensors.python import PythonSensor
from airflow.providers.docker.operators.docker import DockerOperator
from airflow.providers.trino.operators.trino import TrinoOperator

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


def check_new_synthea_data():
    last_mtime = float(Variable.get(WATERMARK_VAR, default_var=0))
    return any(
        os.path.exists(p) and os.path.getmtime(p) > last_mtime
        for p in SOURCE_FILES
    )


def update_watermark():
    mtimes = [os.path.getmtime(p) for p in SOURCE_FILES if os.path.exists(p)]
    if mtimes:
        Variable.set(WATERMARK_VAR, max(mtimes))


def _s3_client():
    return boto3.client('s3', **MINIO_CLIENT)


def _stage_csv_to_parquet(table_name: str, column_map: dict):
    """Read a Synthea CSV, rename columns, upload as Parquet to MinIO staging area."""
    df = pd.read_csv(f'{CSV_BASE}/{table_name}.csv', low_memory=False)
    df = df.rename(columns=column_map)[list(column_map.values())]

    parquet_path = f'/tmp/{table_name}.parquet'
    df.to_parquet(parquet_path, index=False)

    s3 = _s3_client()
    s3.upload_file(
        parquet_path,
        STAGING_BUCKET,
        f'{STAGING_PREFIX}/{table_name}/{table_name}.parquet',
    )
    print(f"Staged {len(df)} rows to s3://{STAGING_BUCKET}/{STAGING_PREFIX}/{table_name}/")


def stage_patients():
    _stage_csv_to_parquet('patients', {
        'Id': 'patient_id',
        'BIRTHDATE': 'birth_date',
        'GENDER': 'gender',
        'RACE': 'race',
        'FIRST': 'first_name',
        'LAST': 'last_name',
    })


def stage_encounters():
    _stage_csv_to_parquet('encounters', {
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
    })


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
    Flatten Patient resources from Synthea FHIR R4 bundles.
    Each file is one patient bundle; we extract the Patient entry plus
    US Core race/ethnicity extensions and address.
    """
    records = []
    for path in glob.glob(f'{FHIR_BASE}/*.json'):
        with open(path) as f:
            bundle = json.load(f)

        for entry in bundle.get('entry', []):
            resource = entry.get('resource', {})
            if resource.get('resourceType') != 'Patient':
                continue

            name = next(
                (n for n in resource.get('name', []) if n.get('use') == 'official'),
                resource.get('name', [{}])[0],
            )
            address = resource.get('address', [{}])[0]
            extensions = resource.get('extension', [])

            records.append({
                'patient_id':   resource.get('id'),
                'birth_date':   resource.get('birthDate'),
                'gender':       resource.get('gender'),
                'family_name':  name.get('family'),
                'given_name':   ' '.join(name.get('given', [])),
                'postal_code':  address.get('postalCode'),
                'state':        address.get('state'),
                'race':         _us_core_race(extensions),
                'ethnicity':    _us_core_ethnicity(extensions),
            })

    if not records:
        raise ValueError(f"No FHIR Patient resources found in {FHIR_BASE}")

    df = pd.DataFrame(records)
    parquet_path = '/tmp/fhir_patients.parquet'
    df.to_parquet(parquet_path, index=False)

    _s3_client().upload_file(
        parquet_path,
        STAGING_BUCKET,
        f'{STAGING_PREFIX}/fhir_patients/fhir_patients.parquet',
    )
    print(f"Staged {len(df)} FHIR patients to MinIO.")


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
    """
    records = []
    for path in glob.glob(f'{FHIR_BASE}/*.json'):
        with open(path) as f:
            bundle = json.load(f)

        for entry in bundle.get('entry', []):
            resource = entry.get('resource', {})
            if resource.get('resourceType') != 'Encounter':
                continue

            enc_type = (resource.get('type') or [{}])[0]
            type_coding = (enc_type.get('coding') or [{}])[0]

            reason = (resource.get('reasonCode') or [{}])[0]
            reason_coding = (reason.get('coding') or [{}])[0]

            period = resource.get('period', {})

            records.append({
                'encounter_id':          resource.get('id'),
                'patient_id':            _ref_id(resource.get('subject', {}).get('reference')),
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

    df = pd.DataFrame(records)
    parquet_path = '/tmp/fhir_encounters.parquet'
    df.to_parquet(parquet_path, index=False)

    _s3_client().upload_file(
        parquet_path,
        STAGING_BUCKET,
        f'{STAGING_PREFIX}/fhir_encounters/fhir_encounters.parquet',
    )
    print(f"Staged {len(df)} FHIR encounters to MinIO.")


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
                'condition_id':          resource.get('id'),
                'patient_id':            _ref_id(resource.get('subject', {}).get('reference')),
                'encounter_id':          _ref_id((resource.get('encounter') or {}).get('reference')),
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

    df = pd.DataFrame(records)
    parquet_path = '/tmp/fhir_conditions.parquet'
    df.to_parquet(parquet_path, index=False)

    _s3_client().upload_file(
        parquet_path,
        STAGING_BUCKET,
        f'{STAGING_PREFIX}/fhir_conditions/fhir_conditions.parquet',
    )
    print(f"Staged {len(df)} FHIR conditions to MinIO.")


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
                first_name      VARCHAR,
                last_name       VARCHAR
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
                total_claim_cost    DOUBLE
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

    # Phase 3: CTAS from Hive staging into Polaris-managed Iceberg
    ingest_patients = TrinoOperator(
        task_id='ingest_patients',
        trino_conn_id=TRINO_CONN_ID,
        sql="""
            CREATE OR REPLACE TABLE iceberg.default.patients AS
            SELECT
                patient_id,
                CAST(birth_date AS DATE)         AS birth_date,
                first_name || ' ' || last_name   AS full_name,
                gender,
                race
            FROM file.staging.patients
        """,
    )

    ingest_encounters = TrinoOperator(
        task_id='ingest_encounters',
        trino_conn_id=TRINO_CONN_ID,
        sql="""
            CREATE OR REPLACE TABLE iceberg.default.encounters AS
            SELECT
                encounter_id,
                patient_id,
                CAST(from_iso8601_timestamp(encounter_start) AS TIMESTAMP)  AS encounter_start,
                CAST(from_iso8601_timestamp(encounter_stop)  AS TIMESTAMP)  AS encounter_stop,
                encounter_class,
                encounter_code,
                encounter_description,
                reason_code,
                reason_description,
                payer_id,
                total_claim_cost
            FROM file.staging.encounters
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
    create_staging_patients        >> ingest_patients
    create_staging_encounters      >> ingest_encounters
    create_staging_conditions      >> ingest_conditions
    create_staging_fhir_patients   >> ingest_fhir_patients
    create_staging_fhir_encounters >> ingest_fhir_encounters
    create_staging_fhir_conditions >> ingest_fhir_conditions

    create_iceberg_schema >> [
        ingest_patients,
        ingest_encounters,
        ingest_conditions,
        ingest_fhir_patients,
        ingest_fhir_encounters,
        ingest_fhir_conditions,
    ]

    all_ingest = [
        ingest_patients,
        ingest_encounters,
        ingest_conditions,
        ingest_fhir_patients,
        ingest_fhir_encounters,
        ingest_fhir_conditions,
    ]

    # dbt runs in its own container (built from Dockerfile.dbt) rather than inside the
    # Airflow image: dbt-core's dependency pins conflict with Airflow's, so DockerOperator
    # drives the sibling `dbt` image over the host docker socket instead.
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

    all_ingest >> update_watermark_task >> dbt_run >> dbt_test
