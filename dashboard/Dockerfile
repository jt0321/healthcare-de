FROM python:3.11-slim

WORKDIR /app

RUN pip install streamlit duckdb pandas plotly pyiceberg adlfs s3fs

COPY . /app

EXPOSE 8501

HEALTHCHECK CMD curl --fail http://localhost:8501/_stcore/health

ENTRYPOINT ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
