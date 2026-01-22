import duckdb

# Initialize DuckDB connection
con = duckdb.connect()

# 1. Install and Load Extensions
con.execute("INSTALL httpfs; LOAD httpfs;")
con.execute("INSTALL iceberg; LOAD iceberg;")

# 2. Configure S3 / MinIO Settings
con.execute("""
    SET s3_endpoint='minio:9000';
    SET s3_access_key_id='admin';
    SET s3_secret_access_key='password123';
    SET s3_use_ssl=false;
    SET s3_url_style='path';
""")

# 3. Create Iceberg Table from Synthea CSV
# This reads from the shared volume /data/csv/
print("Creating Iceberg tables in MinIO...")
con.execute("""
    CREATE TABLE iceberg_patients AS 
    SELECT 
        Id as patient_id,
        BIRTHDATE::DATE as birth_date,
        FIRST || ' ' || LAST as full_name,
        GENDER,
        RACE
    FROM read_csv_auto('/app/data/csv/patients.csv');
""")

# 4. Export to Iceberg Format
con.execute("COPY iceberg_patients TO 's3://healthcare/iceberg/patients' (FORMAT ICEBERG);")

print("Ingestion Complete!")