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

def ingest_iceberg_data():
    con = duckdb.connect()
    
    # Install Extensions
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute("INSTALL iceberg; LOAD iceberg;")
    
    # Configure S3/MinIO
    con.execute("""
        SET s3_endpoint='minio:9000';
        SET s3_access_key_id='admin';
        SET s3_secret_access_key='password123';
        SET s3_use_ssl=false;
        SET s3_url_style='path';
    """)
    
    print("Creating Iceberg tables...")
    
    # Create Table from CSV
    con.execute("""
        CREATE OR REPLACE TABLE raw_patients AS 
        SELECT 
            Id as patient_id,
            BIRTHDATE::DATE as birth_date,
            FIRST || ' ' || LAST as full_name,
            GENDER,
            RACE
        FROM read_csv_auto('/opt/airflow/data/csv/patients.csv');
    """)
    
    # Copy to Iceberg
    con.execute("COPY raw_patients TO 's3://healthcare/iceberg/patients' (FORMAT ICEBERG);")
    
    print("Ingestion Complete!")

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

    ingest_to_iceberg = PythonOperator(
        task_id='ingest_to_iceberg',
        python_callable=ingest_iceberg_data,
    )

    wait_for_synthea_data >> ingest_to_iceberg

