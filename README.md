# Healthcare Data Engineering Pipeline

## Overview

A local data engineering pipeline that generates, ingests, and transforms synthetic healthcare data using a modern open lakehouse stack running entirely in Docker. The pipeline demonstrates two ingestion paths — structured CSV and FHIR R4 — to reflect real-world healthcare data engineering patterns.

**Stack:**

| Layer | Technology |
|---|---|
| Data generation | Synthea (CSV + FHIR R4), run independently of the orchestrated pipeline |
| Orchestration | Apache Airflow |
| Object storage | MinIO (S3-compatible) |
| Iceberg catalog | Apache Polaris (REST catalog) |
| Query engine | Trino |
| Transformation | dbt (dbt-trino) |
| Dashboard | Streamlit |

## Architecture

![Workflow Diagram](./workflow.png)

### Pipeline stages

**1. Generation**
Synthea generates synthetic patient records in two formats:
- CSV exports: patients, encounters, conditions
- FHIR R4 bundle JSON: one file per patient containing Patient, Encounter, and Condition resources

Synthea runs as a standalone, one-shot container (`make generate-data`) — it is **not** started or triggered by Airflow. This reflects how synthetic/clinical data generation actually works in practice: it's a separate, independently-run process that drops files onto shared storage, decoupled from the orchestration layer that later picks them up. The Airflow DAG detects new output via a file-mtime sensor rather than depending on the Synthea container directly.

**2. Staging (Airflow + Python)**
Six staging tasks run in parallel. Each reads from the shared data volume and uploads Parquet files to MinIO at `s3://healthcare/staging/<table>/`.

CSV path:
- `patients.csv` → `staging/patients/`
- `encounters.csv` → `staging/encounters/`
- `conditions.csv` → `staging/conditions/`

FHIR R4 path:
- Patient resources + US Core race/ethnicity extensions → `staging/fhir_patients/`
- Encounter resources (v3-ActCode class, SNOMED type) → `staging/fhir_encounters/`
- Condition resources (SNOMED code, clinicalStatus, verificationStatus, onset/abatement) → `staging/fhir_conditions/`

**3. Ingestion (Trino → Polaris)**
Trino reads from Hive external tables over the MinIO staging Parquet and writes to Polaris-managed Iceberg tables via `CREATE OR REPLACE TABLE ... AS SELECT`. Polaris registers each table in the `iceberg.default` namespace from the moment of creation.

**4. Transformation (dbt + Trino)**
dbt models run against Trino, reading and writing within the Polaris catalog:

```
iceberg.default.patients          ──┐
iceberg.default.encounters          ├──▶ stg_* (staging)
iceberg.default.conditions          │         │
iceberg.default.fhir_patients       │         ▼
iceberg.default.fhir_encounters   ──┘   fct_patient_encounters (mart)
iceberg.default.fhir_conditions
```

**5. Serving**
- Streamlit dashboard queries Trino directly for clinical KPIs
- Trino CLI available for ad-hoc queries across all Polaris-registered tables

## Getting Started

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/)
- [Docker Compose](https://docs.docker.com/compose/install/)

### 1. Configure environment

```bash
cp env.example .env
```

### 2. Start the stack

```bash
make up
```

Starts MinIO, Polaris, Postgres, Airflow (webserver + scheduler), Trino, and the Streamlit dashboard. Polaris bootstrap runs automatically and creates the `default` catalog backed by `s3://healthcare/iceberg/`.

### 3. Generate synthetic patient data

```bash
make generate-data
```

Runs the Synthea container once and exits — it's independent of the rest of the stack and can be re-run any time to refresh/grow the dataset. By default it generates **1,000 living patients** (`docker-compose.yml`'s `synthea` service uses `-p 1000`), plus their associated encounters and conditions, writing to `./data/csv` and `./data/fhir`. A 1,000-patient run typically produces ~1,167 total patient records (some die and are replaced during simulation to reach the target of 1,000 living patients), ~85–90k encounters, and ~45–50k conditions, taking a few minutes and several GB of output.

To change the population size, edit the `command:` line for the `synthea` service in `docker-compose.yml` (the `-p <N>` flag).

### 4. Run the pipeline

Trigger via the Airflow UI at [http://localhost:8080](http://localhost:8080) (admin / admin), or:

```bash
make trigger-ingest
```

The DAG first polls (`wait_for_new_synthea_data`, every 5 min for up to 1 hour) for CSV files newer than the last recorded watermark, so make sure `make generate-data` has completed at least once beforehand. It then runs the following task graph:

```
wait_for_new_synthea_data
  └── [stage_patients, stage_encounters, stage_conditions,
        stage_fhir_patients, stage_fhir_encounters, stage_fhir_conditions]
             └── create_staging_schema
                      └── [create_staging_patients, create_staging_encounters, ...]
                               └── [ingest_patients, ingest_encounters, ...]
                                        └── update_watermark
                                                 └── dbt_run
                                                          └── dbt_test
```

### 5. Run dbt transformations

The DAG runs `dbt run` and `dbt test` automatically, but you can also run them standalone:

```bash
make dbt-run
```

To run tests:

```bash
make dbt-test
```

### 6. View the dashboard

```bash
make streamlit
```

Opens at [http://localhost:8501](http://localhost:8501). Shows patient demographics, encounter volume by month, top conditions by prevalence, encounter class mix, and average cost by encounter type.

### 7. Query with Trino

```bash
make trino-cli
```

Example queries:

```sql
-- All Polaris-registered tables
SHOW TABLES IN iceberg.default;

-- Compare CSV vs FHIR encounter counts per patient
SELECT
    c.patient_id,
    COUNT(DISTINCT c.encounter_id)  AS csv_encounters,
    COUNT(DISTINCT f.encounter_id)  AS fhir_encounters
FROM iceberg.default.encounters c
FULL OUTER JOIN iceberg.default.fhir_encounters f USING (patient_id)
GROUP BY 1
LIMIT 20;

-- Active conditions by clinical status (FHIR)
SELECT clinical_status, COUNT(*) AS condition_count
FROM iceberg.default.fhir_conditions
GROUP BY 1
ORDER BY 2 DESC;
```

## Project Structure

```
.
├── dags/
│   └── healthcare_pipeline.py   # Airflow DAG (staging + Trino ingest)
├── dbt_project/
│   ├── models/
│   │   ├── staging/             # stg_patients, stg_encounters, stg_conditions,
│   │   │                        # stg_fhir_patients, stg_fhir_encounters, stg_fhir_conditions
│   │   └── marts/               # fct_patient_encounters
│   └── profiles.yml             # dbt-trino connection
├── trino/
│   └── catalog/
│       ├── iceberg.properties   # Polaris REST catalog
│       └── file.properties      # Hive connector for MinIO staging
├── dashboard/
│   └── app.py                   # Streamlit (trino.dbapi)
├── polaris-bootstrap.sh         # Creates Polaris catalog on startup
├── docker-compose.yml
├── Dockerfile.airflow
├── Dockerfile.dbt
└── Dockerfile.synthea
```

## Accessing Services

| Service | URL | Credentials |
|---|---|---|
| Airflow UI | http://localhost:8080 | admin / admin |
| MinIO Console | http://localhost:9001 | admin / password123 |
| Polaris API | http://localhost:8181 | — |
| Trino UI | http://localhost:8082 | — |
| Streamlit | http://localhost:8501 | — |

## Polaris persistence

Being an Iceberg REST catalog and persisting its own bookkeeping are two different concerns. Polaris's REST API is how Trino looks up "where's the current metadata pointer for table X" — but Polaris itself still needs to store *its own* catalog/namespace/grant registrations somewhere, separate from the actual Iceberg data+metadata files (which always lived safely in MinIO). By default Polaris keeps that bookkeeping in memory, so a container restart would silently forget every catalog, namespace, and grant even though the underlying data was untouched.

This stack instead points Polaris at the existing Postgres container via its `relational-jdbc` persistence backend (`polaris-bootstrap-db` runs `apache/polaris-admin-tool bootstrap` against a dedicated `polaris` database before Polaris starts — see `docker-compose.yml` and `postgres/init-polaris-db.sh`), so catalogs/namespaces/grants survive `make down && make up`. `polaris-bootstrap.sh` and `polaris-admin-bootstrap.sh` are both idempotent, so they're safe to rerun on every startup.

If you only restart the `polaris` container on its own (not the full stack), also restart `trino` (`docker compose restart trino`) — Trino caches its OAuth session with Polaris and needs a fresh one.
