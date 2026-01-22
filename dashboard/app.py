import streamlit as st
import duckdb
import pandas as pd
import plotly.express as px

st.set_page_config(page_title="Healthcare Data Dashboard", layout="wide")

st.title("🏥 Healthcare Data Dashboard")

# Initialize DuckDB connection
@st.cache_resource
def get_connection():
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

try:
    con = get_connection()
    st.sidebar.header("Settings")
    
    # Query Data - Updated to match Dagster asset output path
    query = "SELECT * FROM iceberg_scan('s3://healthcare/iceberg/patients', allow_moved_paths=true)"
    
    try:
        df = con.execute(query).df()
        
        col1, col2, col3 = st.columns(3)
        col1.metric("Total Patients", len(df))
        col2.metric("Average Age", f"{2024 - pd.to_datetime(df['birth_date']).dt.year.mean():.1f}")
        
        st.subheader("Patient Demographics")
        c1, c2 = st.columns(2)
        with c1:
            st.plotly_chart(px.pie(df, names='GENDER', title='Gender Distribution'), use_container_width=True)
        with c2:
            df['age'] = 2024 - pd.to_datetime(df['birth_date']).dt.year
            st.plotly_chart(px.histogram(df, x='age', nbins=20, title='Age Distribution'), use_container_width=True)

        st.dataframe(df.head(100))
        
    except Exception as e:
        st.warning("No data found yet. Run the pipeline in Dagster!")
        st.error(f"Error details: {e}")

except Exception as e:
    st.error(f"Connection Error: {e}")
