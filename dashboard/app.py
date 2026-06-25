import streamlit as st
import trino
import pandas as pd
import plotly.express as px

st.set_page_config(page_title="Healthcare Data Dashboard", layout="wide")
st.title("Healthcare Analytics Dashboard")


@st.cache_resource
def get_connection():
    return trino.dbapi.connect(
        host='trino',
        port=8080,
        user='airflow',
        catalog='iceberg',
        schema='default',
    )


def query(sql: str) -> pd.DataFrame:
    with get_connection().cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        return pd.DataFrame(rows, columns=cols)


try:
    patients = query("SELECT * FROM iceberg.default.patients")
    encounters = query("SELECT * FROM iceberg.default.encounters")
    conditions = query("SELECT * FROM iceberg.default.conditions")

    patients['age'] = (
        pd.Timestamp.now().year - pd.to_datetime(patients['birth_date']).dt.year
    )

    # --- KPI row ---
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total Patients", f"{len(patients):,}")
    k2.metric("Total Encounters", f"{len(encounters):,}")
    k3.metric("Avg Age", f"{patients['age'].mean():.1f}")
    k4.metric("Avg Encounter Cost", f"${encounters['total_claim_cost'].mean():,.0f}")

    st.divider()

    # --- Demographics ---
    st.subheader("Patient Demographics")
    c1, c2 = st.columns(2)
    with c1:
        st.plotly_chart(
            px.pie(patients, names='gender', title='Gender Distribution'),
            use_container_width=True,
        )
    with c2:
        age_group_order = ['0-17', '18-34', '35-49', '50-64', '65+']
        age_counts = (
            patients.assign(age_group=pd.cut(
                patients['age'],
                bins=[0, 17, 34, 49, 64, 120],
                labels=age_group_order,
            ))
            .groupby('age_group', observed=True)
            .size()
            .reset_index(name='count')
        )
        st.plotly_chart(
            px.bar(age_counts, x='age_group', y='count', title='Patients by Age Group',
                   category_orders={'age_group': age_group_order}),
            use_container_width=True,
        )

    st.divider()

    # --- Encounter trends ---
    st.subheader("Encounter Volume by Month")
    enc = encounters.copy()
    enc['month'] = pd.to_datetime(enc['encounter_start']).dt.to_period('M').astype(str)
    monthly = enc.groupby('month').size().reset_index(name='encounters')
    st.plotly_chart(
        px.line(monthly, x='month', y='encounters', title='Encounters per Month'),
        use_container_width=True,
    )

    st.divider()

    # --- Top conditions ---
    st.subheader("Top 10 Conditions by Prevalence")
    top_conditions = (
        conditions.groupby('condition_description')['patient_id']
        .nunique()
        .reset_index(name='patient_count')
        .sort_values('patient_count', ascending=False)
        .head(10)
    )
    st.plotly_chart(
        px.bar(top_conditions, x='patient_count', y='condition_description',
               orientation='h', title='Unique Patients per Condition'),
        use_container_width=True,
    )

    st.divider()

    # --- Encounter class mix ---
    st.subheader("Encounter Class Mix")
    c3, c4 = st.columns(2)
    with c3:
        class_counts = encounters['encounter_class'].value_counts().reset_index()
        class_counts.columns = ['encounter_class', 'count']
        st.plotly_chart(
            px.pie(class_counts, names='encounter_class', values='count',
                   title='Encounter Type Distribution'),
            use_container_width=True,
        )
    with c4:
        cost_by_class = (
            encounters.groupby('encounter_class')['total_claim_cost']
            .mean()
            .reset_index(name='avg_cost')
            .sort_values('avg_cost', ascending=False)
        )
        st.plotly_chart(
            px.bar(cost_by_class, x='encounter_class', y='avg_cost',
                   title='Avg Claim Cost by Encounter Type'),
            use_container_width=True,
        )

except Exception as e:
    st.warning("No data found. Run the pipeline in Airflow first.")
    st.error(f"Error: {e}")
