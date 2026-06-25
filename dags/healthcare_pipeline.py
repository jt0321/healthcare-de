import os
import boto3
import pandas as pd
from datetime import datetime, timedelta

from airflow import DAG
from airflow.sensors.python import PythonSensor
from airflow.operators.python import PythonOperator
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
STAGING_BUCKET = 'healthcare'
STAGING_PREFIX = 'staging'

MINIO_CLIENT = dict(
    endpoint_url='http://minio:9000',
    aws_access_key_id='admin',
    aws_secret_access_key='password123',
)


def check_synthea_data():
    return os.path.exists(f'{CSV_BASE}/patients.csv')


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
    description='Ingest Synthea CSVs into Polaris-managed Iceberg tables via Trino',
    schedule_interval=timedelta(days=1),
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=['healthcare'],
) as dag:

    wait_for_synthea_data = PythonSensor(
        task_id='wait_for_synthea_data',
        python_callable=check_synthea_data,
        poke_interval=10,
        timeout=600,
        mode='poke',
    )

    # Phase 1: stage CSVs to MinIO as Parquet
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

    # Phase 2: create Hive staging schema and external tables over MinIO Parquet
    create_staging_schema = TrinoOperator(
        task_id='create_staging_schema',
        trino_conn_id=TRINO_CONN_ID,
        sql="CREATE SCHEMA IF NOT EXISTS file.staging WITH (location = 's3://healthcare/staging/')",
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
                CAST(encounter_start AS TIMESTAMP)  AS encounter_start,
                CAST(encounter_stop  AS TIMESTAMP)  AS encounter_stop,
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

    # Staging tasks can run in parallel after the sensor
    wait_for_synthea_data >> [stage_patients_task, stage_encounters_task, stage_conditions_task]

    # DDL phase runs after all staging is done
    [stage_patients_task, stage_encounters_task, stage_conditions_task] >> create_staging_schema

    # External table DDL runs after schema exists
    create_staging_schema >> [create_staging_patients, create_staging_encounters, create_staging_conditions]

    # Ingest into Iceberg after external tables are defined
    create_staging_patients  >> ingest_patients
    create_staging_encounters >> ingest_encounters
    create_staging_conditions >> ingest_conditions
