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

    tab1, tab2, tab3, tab4, tab5 = st.tabs(
        ["Population", "Clinical", "FHIR", "Patient Identity", "Restatements"]
    )

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
        st.subheader("EHR vs HIE — Encounter Count per Linked Patient")
        st.caption(
            "The two feeds share no patient identifier, so this comparison runs "
            "through the master patient index rather than joining on a key. Only "
            "patients the EMPI linked across both feeds appear here."
        )

        reconciliation = query("""
            SELECT
                m.mpi_id,
                COUNT(DISTINCT c.encounter_id)           AS ehr_encounters,
                COUNT(DISTINCT f.encounter_id)           AS hie_encounters
            FROM iceberg.default.dim_patient_master m
            LEFT JOIN iceberg.default.encounters c
                ON c.patient_id = m.ehr_patient_id
            LEFT JOIN iceberg.default.fhir_encounters f
                ON f.patient_id = m.hie_patient_id
            WHERE m.record_source = 'both'
            GROUP BY 1
            ORDER BY ehr_encounters DESC
            LIMIT 50
        """)

        if not reconciliation.empty:
            reconciliation['match'] = (
                reconciliation['ehr_encounters'] == reconciliation['hie_encounters']
            )
            st.metric("Linked patients whose encounter counts agree",
                      f"{reconciliation['match'].mean() * 100:.1f}%")

            fig = go.Figure()
            fig.add_trace(go.Bar(name='EHR (CSV)', x=reconciliation['mpi_id'],
                                 y=reconciliation['ehr_encounters']))
            fig.add_trace(go.Bar(name='HIE (FHIR)', x=reconciliation['mpi_id'],
                                 y=reconciliation['hie_encounters']))
            fig.update_layout(barmode='group',
                              title='EHR vs HIE Encounters (top 50 linked patients)',
                              xaxis_title='Master patient identity',
                              yaxis_title='Encounter Count', xaxis_tickangle=45)
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

    # ══════════════════════════════════════════════════════════════════
    # TAB 4 — Patient Identity (EMPI)
    # ══════════════════════════════════════════════════════════════════
    with tab4:
        st.subheader("Resolving two feeds onto one person")
        st.caption(
            "The CSV extract is the local EHR; the FHIR path is treated as a feed "
            "from an outside health information exchange carrying its own "
            "identifiers and its own copy of the demographics. With no shared key "
            "and no national patient identifier, identity is inferred from name, "
            "date of birth, sex and postal code."
        )

        recon = query("""
            SELECT category, metric, value, unit
            FROM iceberg.default.rpt_identity_reconciliation
        """)

        def metric_value(name, default=0.0):
            row = recon.loc[recon['metric'] == name, 'value']
            return float(row.iloc[0]) if not row.empty else default

        i1, i2, i3, i4 = st.columns(4)
        i1.metric("EHR patients", f"{metric_value('EHR patients (CSV extract)'):,.0f}")
        i2.metric("HIE records", f"{metric_value('HIE patient records (FHIR feed)'):,.0f}")
        i3.metric("Auto-linked", f"{metric_value('Auto-linked across both feeds'):,.0f}")
        i4.metric("Link rate", f"{metric_value('Link rate against EHR population'):.1f}%")

        st.divider()
        c1, c2 = st.columns([3, 2])

        with c1:
            st.markdown("**Linkage outcome per master identity**")
            outcome = query("""
                SELECT record_source, COUNT(*) AS identities
                FROM iceberg.default.dim_patient_master
                GROUP BY record_source
                ORDER BY identities DESC
            """)
            st.plotly_chart(
                px.bar(outcome, x='identities', y='record_source', orientation='h',
                       title='Where each master identity came from',
                       labels={'record_source': '', 'identities': 'Master identities'}),
                use_container_width=True,
            )

        with c2:
            st.markdown("**Linkage summary**")
            st.dataframe(
                recon[recon['category'] == 'linkage'][['metric', 'value']],
                hide_index=True, use_container_width=True,
            )

        st.divider()
        st.subheader("Score distribution and the review band")
        st.caption(
            "Scores at or above 85 are linked automatically. Between 60 and 85 a "
            "human decides: merging two people who are not the same person puts "
            "one patient's diagnoses in another's chart, so the threshold is set "
            "conservatively and the review queue is expected to be non-empty."
        )

        scores = query("""
            SELECT match_tier, match_score, COUNT(*) AS pairs
            FROM iceberg.default.int_patient_match_candidates
            GROUP BY match_tier, match_score
            ORDER BY match_score
        """)
        fig = px.bar(scores, x='match_score', y='pairs', color='match_tier',
                     title='Candidate pairs by weighted match score',
                     labels={'match_score': 'Match score', 'pairs': 'Candidate pairs',
                             'match_tier': 'Tier'},
                     color_discrete_map={'auto': '#2a9d8f', 'review': '#e9c46a',
                                         'reject': '#bdbdbd'})
        fig.add_vline(x=85, line_dash='dash', line_color='#264653',
                      annotation_text='auto-link threshold')
        fig.add_vline(x=60, line_dash='dot', line_color='#264653',
                      annotation_text='reject threshold')
        st.plotly_chart(fig, use_container_width=True)

        st.divider()
        st.subheader("Conflicts among records that were linked anyway")
        st.caption(
            "These pairs are confidently the same person, and the two systems "
            "still state different facts about them. A match rate alone hides "
            "this; survivorship rules pick a winner per field, and the loser is "
            "what someone has to adjudicate."
        )

        conflicts = recon[recon['category'] == 'conflicts'].copy()
        cc1, cc2 = st.columns([3, 2])
        with cc1:
            field_conflicts = conflicts[conflicts['metric'].str.startswith('Conflicting')]
            st.plotly_chart(
                px.bar(field_conflicts, x='value', y='metric', orientation='h',
                       title='Linked pairs disagreeing, by field',
                       labels={'metric': '', 'value': 'Linked pairs'}),
                use_container_width=True,
            )
        with cc2:
            st.dataframe(conflicts[['metric', 'value']], hide_index=True,
                         use_container_width=True)

        st.markdown("**Sample of linked pairs with the most disagreement**")
        worst = query("""
            SELECT
                mpi_id, match_score, conflict_count,
                ehr_last_name, hie_last_name,
                ehr_first_name, hie_first_name,
                ehr_birth_date, hie_birth_date,
                ehr_postal_code, hie_postal_code
            FROM iceberg.default.rpt_identity_conflicts
            WHERE conflict_count > 0
            ORDER BY conflict_count DESC, match_score ASC
            LIMIT 25
        """)
        st.dataframe(worst, hide_index=True, use_container_width=True)

    # ══════════════════════════════════════════════════════════════════
    # TAB 5 — Restatements (late-arriving corrections)
    # ══════════════════════════════════════════════════════════════════
    with tab5:
        st.subheader("Rows the source changed its mind about")
        st.caption(
            "Encounters are repriced, re-coded, and voided for months after they "
            "first load. The ingest MERGEs each correction onto the existing row "
            "and increments its revision counter, so a restated figure is "
            "distinguishable from one that was right the first time."
        )

        restated = query("""
            SELECT revision, is_voided, encounters, patients,
                   total_claim_cost, avg_claim_cost, pct_of_encounters,
                   earliest_source_update, latest_source_update
            FROM iceberg.default.rpt_encounter_restatements
            ORDER BY revision, is_voided
        """)

        total_enc = int(restated['encounters'].sum())
        restated_enc = int(restated.loc[restated['revision'] > 1, 'encounters'].sum())
        voided_enc = int(restated.loc[restated['is_voided'], 'encounters'].sum())
        max_rev = int(restated['revision'].max())

        r1, r2, r3, r4 = st.columns(4)
        r1.metric("Encounters", f"{total_enc:,}")
        r2.metric("Restated at least once", f"{restated_enc:,}",
                  f"{100.0 * restated_enc / total_enc:.1f}% of rows" if total_enc else None)
        r3.metric("Voided", f"{voided_enc:,}")
        r4.metric("Highest revision", f"{max_rev}")

        st.divider()
        rc1, rc2 = st.columns(2)

        with rc1:
            st.plotly_chart(
                px.bar(restated, x='revision', y='encounters', color='is_voided',
                       title='Encounters by revision count',
                       labels={'revision': 'Revision', 'encounters': 'Encounters',
                               'is_voided': 'Voided'},
                       log_y=True),
                use_container_width=True,
            )

        with rc2:
            st.plotly_chart(
                px.bar(restated, x='revision', y='avg_claim_cost', color='is_voided',
                       title='Average claim cost by revision count',
                       labels={'revision': 'Revision',
                               'avg_claim_cost': 'Avg claim cost ($)',
                               'is_voided': 'Voided'}),
                use_container_width=True,
            )
            st.caption(
                "Repricing is the most common correction, so restated rows do not "
                "carry the same average as first-pass rows. This is why "
                "\"as reported\" and \"as restated\" are different numbers."
            )

        st.dataframe(restated, hide_index=True, use_container_width=True)

        st.divider()
        st.subheader("Patient demographics over time (SCD2)")
        st.caption(
            "The Iceberg table holds current state only. The dbt snapshot versions "
            "each patient row, so a claim can be read against the address and name "
            "that were on file when it was submitted rather than the ones on file "
            "today."
        )

        scd = query("""
            SELECT
                versions,
                COUNT(*) AS patients
            FROM (
                SELECT patient_id, COUNT(*) AS versions
                FROM iceberg.default.scd_patients
                GROUP BY patient_id
            )
            GROUP BY versions
            ORDER BY versions
        """)
        s1, s2 = st.columns([2, 3])
        with s1:
            st.dataframe(scd, hide_index=True, use_container_width=True)
        with s2:
            history = query("""
                SELECT patient_id, full_name, city, postal_code, revision,
                       dbt_valid_from, dbt_valid_to
                FROM iceberg.default.scd_patients
                WHERE patient_id IN (
                    SELECT patient_id
                    FROM iceberg.default.scd_patients
                    GROUP BY patient_id
                    HAVING COUNT(*) > 1
                )
                ORDER BY patient_id, dbt_valid_from
                LIMIT 40
            """)
            st.markdown("**Patients with more than one version on file**")
            st.dataframe(history, hide_index=True, use_container_width=True)

        st.divider()
        st.subheader("Iceberg snapshot lineage")
        st.caption(
            "Every MERGE commits a new Iceberg snapshot. Because the loads are "
            "merges rather than full rebuilds, this history is a usable audit "
            "trail: any of these snapshots can still be queried with "
            "`SELECT ... FOR VERSION AS OF <snapshot_id>` to reproduce what a "
            "report would have said at the time."
        )
        snapshots = query("""
            SELECT
                committed_at,
                snapshot_id,
                operation,
                -- element_at returns null for an absent key; the map
                -- subscript operator throws, and not every snapshot summary
                -- carries every counter.
                element_at(summary, 'added-records')     AS added_records,
                element_at(summary, 'deleted-records')   AS deleted_records,
                element_at(summary, 'total-records')     AS total_records
            FROM iceberg.default."encounters$snapshots"
            ORDER BY committed_at DESC
            LIMIT 20
        """)
        st.dataframe(snapshots, hide_index=True, use_container_width=True)


except Exception as e:
    st.warning("No data found. Run the pipeline in Airflow first.")
    st.error(f"Error: {e}")
