import duckdb
from dagster import asset, Deps
from .synthea import raw_patients_csv

@asset(
    deps=[raw_patients_csv],
    description="Ingests raw CSV data into an Iceberg table using DuckDB."
)
def iceberg_patients(context) -> None:
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
    
    context.log.info("Creating Iceberg tables...")
    
    # Create Table from CSV
    con.execute("""
        CREATE OR REPLACE TABLE raw_patients AS 
        SELECT 
            Id as patient_id,
            BIRTHDATE::DATE as birth_date,
            FIRST || ' ' || LAST as full_name,
            GENDER,
            RACE
        FROM read_csv_auto('/opt/dagster/app/data/csv/patients.csv');
    """)
    
    # Copy to Iceberg
    # Note: In a real run, we'd check if table exists or append. 
    # For now, we overwrite or just create.
    con.execute("COPY raw_patients TO 's3://healthcare/iceberg/patients' (FORMAT ICEBERG);")
    
    context.log.info("Ingestion Complete!")
