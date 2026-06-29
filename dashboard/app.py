import streamlit as st
import trino
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

st.set_page_config(page_title="Healthcare Analytics", layout="wide")
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


@st.cache_data(ttl=300)
def query(sql: str) -> pd.DataFrame:
    with get_connection().cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        return pd.DataFrame(rows, columns=cols)


try:
    # ── KPIs ──────────────────────────────────────────────────────────
    kpis = query("""
        SELECT
            COUNT(DISTINCT patient_id)                          AS total_patients,
            COUNT(*)                                            AS total_encounters,
            ROUND(AVG(total_claim_cost), 2)                    AS avg_encounter_cost,
            ROUND(AVG(encounter_duration_minutes), 1)          AS avg_duration_min
        FROM iceberg.default.fct_patient_encounters
    """)

    condition_count = query("""
        SELECT COUNT(DISTINCT condition_code) AS unique_conditions
        FROM iceberg.default.conditions
    """)

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Patients",           f"{int(kpis['total_patients'][0]):,}")
    k2.metric("Encounters",         f"{int(kpis['total_encounters'][0]):,}")
    k3.metric("Unique Conditions",  f"{int(condition_count['unique_conditions'][0]):,}")
    k4.metric("Avg Encounter Cost", f"${kpis['avg_encounter_cost'][0]:,.0f}")
    k5.metric("Avg Duration",       f"{kpis['avg_duration_min'][0]} min")

    st.divider()

    tab1, tab2, tab3 = st.tabs(["Population", "Clinical", "FHIR"])

    # ══════════════════════════════════════════════════════════════════
    # TAB 1 — Population
    # ══════════════════════════════════════════════════════════════════
    with tab1:
        c1, c2 = st.columns(2)

        with c1:
            gender = query("""
                SELECT gender, COUNT(DISTINCT patient_id) AS patients
                FROM iceberg.default.fct_patient_encounters
                GROUP BY gender
            """)
            st.plotly_chart(
                px.pie(gender, names='gender', values='patients',
                       title='Gender Distribution', hole=0.4),
                use_container_width=True,
            )

        with c2:
            age_groups = query("""
                SELECT age_group, COUNT(DISTINCT patient_id) AS patients
                FROM iceberg.default.fct_patient_encounters
                GROUP BY age_group
                ORDER BY age_group
            """)
            st.plotly_chart(
                px.bar(age_groups, x='age_group', y='patients',
                       title='Patients by Age Group',
                       category_orders={'age_group': ['0-17','18-34','35-49','50-64','65+']}),
                use_container_width=True,
            )

        c3, c4 = st.columns(2)

        with c3:
            race = query("""
                SELECT race, COUNT(DISTINCT patient_id) AS patients
                FROM iceberg.default.fct_patient_encounters
                WHERE race IS NOT NULL
                GROUP BY race
                ORDER BY patients DESC
            """)
            st.plotly_chart(
                px.bar(race, x='patients', y='race', orientation='h',
                       title='Patients by Race'),
                use_container_width=True,
            )

        with c4:
            monthly = query("""
                SELECT
                    DATE_FORMAT(encounter_start, '%Y-%m') AS month,
                    COUNT(*) AS encounters
                FROM iceberg.default.fct_patient_encounters
                GROUP BY 1
                ORDER BY 1
            """)
            st.plotly_chart(
                px.line(monthly, x='month', y='encounters',
                        title='Encounter Volume by Month'),
                use_container_width=True,
            )

    # ══════════════════════════════════════════════════════════════════
    # TAB 2 — Clinical
    # ══════════════════════════════════════════════════════════════════
    with tab2:
        top_conditions = query("""
            SELECT
                condition_description,
                COUNT(DISTINCT patient_id) AS patient_count
            FROM iceberg.default.conditions
            GROUP BY condition_description
            ORDER BY patient_count DESC
            LIMIT 15
        """)
        st.plotly_chart(
            px.bar(top_conditions, x='patient_count', y='condition_description',
                   orientation='h', title='Top 15 Conditions by Patient Prevalence',
                   height=500),
            use_container_width=True,
        )

        st.divider()

        c5, c6 = st.columns(2)

        with c5:
            class_cost = query("""
                SELECT
                    encounter_class,
                    COUNT(*) AS encounters,
                    ROUND(AVG(total_claim_cost), 0) AS avg_cost
                FROM iceberg.default.fct_patient_encounters
                GROUP BY encounter_class
                ORDER BY encounters DESC
            """)
            st.plotly_chart(
                px.bar(class_cost, x='encounter_class', y='avg_cost',
                       title='Avg Claim Cost by Encounter Class',
                       color='encounter_class'),
                use_container_width=True,
            )

        with c6:
            st.plotly_chart(
                px.pie(class_cost, names='encounter_class', values='encounters',
                       title='Encounter Class Mix', hole=0.4),
                use_container_width=True,
            )

        st.divider()

        cost_by_age = query("""
            SELECT
                age_group,
                ROUND(AVG(total_claim_cost), 0) AS avg_cost,
                COUNT(*) AS encounters
            FROM iceberg.default.fct_patient_encounters
            GROUP BY age_group
            ORDER BY age_group
        """)
        st.plotly_chart(
            px.bar(cost_by_age, x='age_group', y='avg_cost',
                   title='Avg Claim Cost by Age Group',
                   category_orders={'age_group': ['0-17','18-34','35-49','50-64','65+']},
                   text='avg_cost'),
            use_container_width=True,
        )

    # ══════════════════════════════════════════════════════════════════
    # TAB 3 — FHIR
    # ══════════════════════════════════════════════════════════════════
    with tab3:
        st.subheader("FHIR R4 — Condition Clinical Status")

        clinical = query("""
            SELECT
                clinical_status,
                COUNT(*) AS conditions,
                COUNT(DISTINCT patient_id) AS patients
            FROM iceberg.default.fhir_conditions
            GROUP BY clinical_status
            ORDER BY conditions DESC
        """)

        cf1, cf2 = st.columns(2)
        with cf1:
            st.plotly_chart(
                px.pie(clinical, names='clinical_status', values='conditions',
                       title='Conditions by Clinical Status', hole=0.4),
                use_container_width=True,
            )
        with cf2:
            st.plotly_chart(
                px.bar(clinical, x='clinical_status', y='patients',
                       title='Patients with Each Clinical Status'),
                use_container_width=True,
            )

        st.divider()
        st.subheader("FHIR vs CSV — Encounter Count per Patient")

        reconciliation = query("""
            SELECT
                COALESCE(c.patient_id, f.patient_id)    AS patient_id,
                COUNT(DISTINCT c.encounter_id)           AS csv_encounters,
                COUNT(DISTINCT f.encounter_id)           AS fhir_encounters
            FROM iceberg.default.encounters c
            FULL OUTER JOIN iceberg.default.fhir_encounters f
                ON c.patient_id = f.patient_id
            GROUP BY 1
            ORDER BY csv_encounters DESC
            LIMIT 50
        """)
        reconciliation['match'] = (
            reconciliation['csv_encounters'] == reconciliation['fhir_encounters']
        )
        match_pct = reconciliation['match'].mean() * 100
        st.metric("Patients with matching encounter counts (CSV = FHIR)",
                  f"{match_pct:.1f}%")

        fig = go.Figure()
        fig.add_trace(go.Bar(name='CSV', x=reconciliation['patient_id'],
                             y=reconciliation['csv_encounters']))
        fig.add_trace(go.Bar(name='FHIR', x=reconciliation['patient_id'],
                             y=reconciliation['fhir_encounters']))
        fig.update_layout(barmode='group', title='CSV vs FHIR Encounters (top 50 patients)',
                          xaxis_title='Patient ID', yaxis_title='Encounter Count',
                          xaxis_tickangle=45)
        st.plotly_chart(fig, use_container_width=True)

        st.divider()
        st.subheader("Active Conditions (FHIR)")
        active = query("""
            SELECT
                coalesce(condition_text, condition_display) AS condition,
                COUNT(DISTINCT patient_id)                  AS patients
            FROM iceberg.default.fhir_conditions
            WHERE clinical_status = 'active'
            GROUP BY 1
            ORDER BY patients DESC
            LIMIT 15
        """)
        st.plotly_chart(
            px.bar(active, x='patients', y='condition', orientation='h',
                   title='Top 15 Active Conditions (FHIR clinical_status = active)',
                   height=450),
            use_container_width=True,
        )

except Exception as e:
    st.warning("No data found. Run the pipeline in Airflow first.")
    st.error(f"Error: {e}")
