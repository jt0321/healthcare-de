import duckdb
from datetime import datetime, timedelta
import os

from airflow import DAG
from airflow.sensors.python import PythonSensor
from airflow.operators.python import PythonOperator

default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

def check_synthea_data():
    csv_path = "/opt/airflow/data/csv/patients.csv"
    if os.path.exists(csv_path):
        return True
    return False

def _duckdb_con():
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute("INSTALL iceberg; LOAD iceberg;")
    con.execute("""
        SET s3_endpoint='minio:9000';
        SET s3_access_key_id='admin';
        SET s3_secret_access_key='password123';
        SET s3_use_ssl=false;
        SET s3_url_style='path';
    """)
    return con


def ingest_patients():
    con = _duckdb_con()
    con.execute("""
        CREATE OR REPLACE TABLE raw_patients AS
        SELECT
            Id as patient_id,
            BIRTHDATE::DATE as birth_date,
            FIRST || ' ' || LAST as full_name,
            GENDER as gender,
            RACE as race
        FROM read_csv_auto('/opt/airflow/data/csv/patients.csv');
    """)
    con.execute("COPY raw_patients TO 's3://healthcare/iceberg/default/patients' (FORMAT ICEBERG);")
    print("Patients ingested.")


def ingest_encounters():
    con = _duckdb_con()
    con.execute("""
        CREATE OR REPLACE TABLE raw_encounters AS
        SELECT
            Id as encounter_id,
            PATIENT as patient_id,
            START::TIMESTAMP as encounter_start,
            STOP::TIMESTAMP as encounter_stop,
            ENCOUNTERCLASS as encounter_class,
            CODE as encounter_code,
            DESCRIPTION as encounter_description,
            REASONCODE as reason_code,
            REASONDESCRIPTION as reason_description,
            PAYER as payer_id,
            TOTAL_CLAIM_COST::DOUBLE as total_claim_cost
        FROM read_csv_auto('/opt/airflow/data/csv/encounters.csv');
    """)
    con.execute("COPY raw_encounters TO 's3://healthcare/iceberg/default/encounters' (FORMAT ICEBERG);")
    print("Encounters ingested.")

with DAG(
    'healthcare_data_pipeline',
    default_args=default_args,
    description='Pipeline to ingest Synthea data to Iceberg',
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

    ingest_patients_task = PythonOperator(
        task_id='ingest_patients',
        python_callable=ingest_patients,
    )

    ingest_encounters_task = PythonOperator(
        task_id='ingest_encounters',
        python_callable=ingest_encounters,
    )

    wait_for_synthea_data >> ingest_patients_task >> ingest_encounters_task

