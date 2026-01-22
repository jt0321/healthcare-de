# Health Data Engineering Pipeline

## Overview

This project is a local Data Engineering pipeline designed to simulate, ingest, and transform healthcare data. It leverages a modern data stack running entirely in Docker:

- **Synthea**: Generates synthetic patient health records.
- **MinIO**: S3-compatible object storage.
- **Nessie**: Git-like catalog for Iceberg tables.
- **DuckDB**: In-memory analytical database for data ingestion and processing.
- **dbt**: Defines data transformation models.
- **Apache Iceberg**: Open table format for analytic datasets.

## Architecture

1.  **Generation**: Synthea container generates synthetic CSV data (patients, encounters, etc.) into a shared volume.
2.  **Ingestion**: A Python/DuckDB script reads the CSVs and writes them as Iceberg tables to MinIO, committed to the Nessie catalog.
3.  **Transformation**: dbt (running with DuckDB) transforms the raw Iceberg tables into analytical models.

## Prerequisites

-   [Docker](https://docs.docker.com/get-docker/)
-   [Docker Compose](https://docs.docker.com/compose/install/)

## Getting Started

### 1. Start the Environment

Spin up the entire stack using Docker Compose:

```bash
docker-compose up -d
```

This will start MinIO, Nessie, the Synthea generator, and the processor/dbt containers.

### 2. Generate Data

The `synthea` service automatically runs on startup and generates patient data into the `./data` directory. You can check the logs to see progress:

```bash
docker-compose logs -f synthea
```

### 3. Ingest Data to Iceberg

Once data is generated, run the ingestion script within the `processor` container. This converts the CSVs to Iceberg tables in MinIO:

```bash
docker-compose exec processor python scripts/ingest_to_iceberg.py
```

### 4. Run dbt Transformations

Run dbt models to transform the data:

```bash
docker-compose run dbt compile
# or
docker-compose run dbt run
```

## Project Structure

-   `docker-compose.yml`: Defines the multi-container Docker application.
-   `synthea.Dockerfile`: Custom Dockerfile for the Synthea service.
-   `scripts/`: Python scripts for data ingestion (e.g., `ingest_to_iceberg.py`).
-   `dbt_project/`: The dbt project directory containing models and configuration.
-   `data/`: Shared volume where Synthea outputs raw CSV files (git-ignored).
-   `minio_data/`: Local storage for MinIO (git-ignored).

## Accessing Services

-   **MinIO Console**: [http://localhost:9001](http://localhost:9001)
    -   User: `admin`
    -   Password: `password123`
-   **Nessie API**: [http://localhost:19101/api/v1](http://localhost:19101/api/v1)

## Notes

-   The project uses a local MinIO instance to simulate S3.
-   Iceberg tables are stored in the `healthcare` bucket in MinIO.
