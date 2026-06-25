FROM python:3.11-slim

WORKDIR /app

RUN pip install streamlit trino pandas plotly

COPY . /app

EXPOSE 8501

HEALTHCHECK CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"

ENTRYPOINT ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
