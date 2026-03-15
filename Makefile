.PHONY: help up down build restart logs clean ps generate-data airflow-init airflow dbt-compile dbt-run streamlit trigger-ingest

# Default target
help:
	@echo "Available commands:"
	@echo "  make up              - Start all services in the background"
	@echo "  make down            - Stop and remove all containers, networks, and volumes"
	@echo "  make build           - Build or rebuild services"
	@echo "  make restart         - Restart all services"
	@echo "  make logs            - View output from all containers"
	@echo "  make clean           - Stop and remove containers, networks, and named volumes"
	@echo "  make ps              - List containers"
	@echo ""
	@echo "Data Pipeline Tasks:"
	@echo "  make generate-data   - Run the Synthea service to generate synthetic patient data"
	@echo "  make airflow-init    - Initialize Airflow database and create admin user"
	@echo "  make airflow         - Start Airflow webserver and scheduler"
	@echo "  make trigger-ingest  - Trigger the healthcare_data_pipeline DAG to move data to Iceberg"
	@echo "  make dbt-compile     - Compile dbt models"
	@echo "  make dbt-run         - Run dbt models to transform Iceberg tables"
	@echo "  make streamlit       - Start the Streamlit dashboard"

up:
	docker compose up -d

down:
	docker compose down

build:
	docker compose build

restart:
	docker compose restart

logs:
	docker compose logs -f

clean:
	docker compose down -v

ps:
	docker compose ps

# Pipeline-specific commands

generate-data:
	docker compose up synthea

airflow-init:
	docker compose up airflow-init

airflow:
	docker compose up -d airflow-webserver airflow-scheduler

trigger-ingest:
	docker compose exec airflow-webserver airflow dags trigger healthcare_data_pipeline

dbt-compile:
	docker compose run --rm dbt compile

dbt-run:
	docker compose run --rm dbt run

streamlit:
	docker compose up -d dashboard
	docker compose logs -f dashboard
