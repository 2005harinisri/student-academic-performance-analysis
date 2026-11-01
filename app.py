"""
Student Academic Performance Dashboard
Karunya-inspired faculty dashboard prototype.
All data is fictional demonstration data.
Run using: streamlit run app.py
"""

from __future__ import annotations

import html
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from io import BytesIO
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Table,
    TableStyle,
    Paragraph,
    Spacer,
)

try:
    import pdfplumber
except ImportError:
    pdfplumber = None



# ============================================================
# CONFIGURATION
# ============================================================

BASE = Path(__file__).parent
DATA_DIR = BASE / "data"
LOGO_PATH = BASE / "assets" / "karunya_logo.png"
ACTIVITY_DB = DATA_DIR / "activity_log.db"

STAFF = "Dr. Diana Andhrusia"
DEPT = "Electronics and Computer Engineering"
IA_MAX = 40
IA_COLS = ["IA1", "IA2", "IA3"]

PAGES = [
    "Dashboard",
    "Student Analysis",
    "Class Analysis",
    "Subject Reports",
    "Export Reports",
]

CATS = [
    "MAINTAIN",
    "PRACTICE",
    "NEEDS ATTENTION",
    "HIGH PRIORITY",
]

INCOMPLETE = "INCOMPLETE"

CAT_COLORS = {
    "MAINTAIN": "#43a047",
    "PRACTICE": "#2f80ed",
    "NEEDS ATTENTION": "#fbbc04",
    "HIGH PRIORITY": "#e53935",
    INCOMPLETE: "#8895ad",
}

CAT_RANGES = {
    "MAINTAIN": "90–100%",
    "PRACTICE": "70–<90%",
    "NEEDS ATTENTION": "50–<70%",
    "HIGH PRIORITY": "<50%",
}

CAT_ADVICE = {
    "MAINTAIN": "Performance is strong. Continue the current study approach.",
    "PRACTICE": "Continue practising to improve consistency and accuracy.",
    "NEEDS ATTENTION": "Revise important concepts and practise more questions.",
    "HIGH PRIORITY": "Prioritise this subject and create a focused improvement plan.",
    INCOMPLETE: "One or more IA marks are missing. A recommendation cannot be assigned.",
}

ALIASES = {
    "register_no": [
        "register_no", "reg_no", "regno", "register_number",
        "registration_no", "registration_number", "roll_no", "rollno"
    ],
    "student_name": ["student_name", "name", "student", "studentname"],
    "subject": ["subject", "subject_name", "course", "course_name"],
    "subject_code": ["subject_code", "course_code", "code"],
    "marks_obtained": [
        "marks_obtained", "marks", "obtained_marks", "mark", "score"
    ],
    "max_marks": [
        "max_marks", "maximum_marks", "max", "total_marks", "out_of"
    ],
    "IA1": ["ia1", "ia_1", "internal_1", "ia1_marks"],
    "IA2": ["ia2", "ia_2", "internal_2", "ia2_marks"],
    "IA3": ["ia3", "ia_3", "internal_3", "ia3_marks"],
}


# ============================================================
# CALCULATIONS
# ============================================================

def classify(pct) -> str:
    if pct is None or pd.isna(pct):
        return INCOMPLETE

    p = round(float(pct), 6)

    if p >= 90:
        return "MAINTAIN"
    if p >= 70:
        return "PRACTICE"
    if p >= 50:
        return "NEEDS ATTENTION"

    return "HIGH PRIORITY"


def add_ia_metrics(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    for col in IA_COLS:
        if col not in df.columns:
            df[col] = np.nan

    ia = df[IA_COLS].apply(pd.to_numeric, errors="coerce")
    df[IA_COLS] = ia

    complete = ia.notna().all(axis=1)
    average = ia.mean(axis=1)

    df["Average Marks"] = average.where(complete).round(2)
    df["Average %"] = (
        average / IA_MAX * 100
    ).where(complete).round(6)

    df["Recommendation"] = df["Average %"].map(classify)

    return df


def category_counts(series: pd.Series) -> dict:
    values = series.value_counts()
    return {category: int(values.get(category, 0)) for category in CATS}


def fmt_pct(value) -> str:
    return "N/A" if pd.isna(value) else f"{value:.1f}%"


def fmt_mark(value) -> str:
    return "Missing" if pd.isna(value) else f"{value:g}"


def esc(value) -> str:
    return html.escape(str(value))


# ============================================================
# REAL ACTIVITY LOGGING
# ============================================================

def log_activity(event_type: str, details: str) -> None:
    """Persist an actual dashboard event to a local SQLite audit log."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    with sqlite3.connect(ACTIVITY_DB) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                details TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO activity_log (timestamp, event_type, details) VALUES (?, ?, ?)",
            (timestamp, str(event_type), str(details)),
        )
        connection.commit()


def get_recent_activity(limit: int = 8) -> pd.DataFrame:
    """Return the newest real events recorded by this dashboard."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(ACTIVITY_DB) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                details TEXT NOT NULL
            )
            """
        )
        return pd.read_sql_query(
            """
            SELECT timestamp AS 'Date & Time',
                   event_type AS 'Type',
                   details AS 'Details'
            FROM activity_log
            WHERE event_type IN ('Student History', 'Student Analysis', 'Subject Report Search')
            ORDER BY id DESC
            LIMIT ?
            """,
            connection,
            params=(int(limit),),
        )


# ============================================================
# LOAD DATA
# ============================================================

@st.cache_data(show_spinner=False)
def load_data():
    required = {
        "demo_students": [
            "register_no", "student_name", "department", "semester"
        ],
        "demo_marks": [
            "register_no", "subject_code", "subject", "IA1", "IA2", "IA3"
        ],
        "demo_attendance": [
            "register_no", "subject_code",
            "classes_attended", "classes_held"
        ],
    }

    frames = {}
    errors = []
    warnings = []

    for name, columns in required.items():
        path = DATA_DIR / f"{name}.csv"

        if not path.exists():
            errors.append(f"Missing file: data/{name}.csv")
            continue

        try:
            df = pd.read_csv(path, dtype=str)
            df = df.apply(lambda col: col.str.strip())
        except Exception as exc:
            errors.append(f"Could not read {path.name}: {exc}")
            continue

        missing = [col for col in columns if col not in df.columns]

        if missing:
            errors.append(
                f"{path.name} is missing columns: {', '.join(missing)}"
            )
            continue

        frames[name] = df

    if errors:
        return None, None, None, errors, warnings

    students = frames["demo_students"].copy()
    marks = frames["demo_marks"].copy()
    attendance = frames["demo_attendance"].copy()

    if students["register_no"].duplicated().any():
        warnings.append(
            "Duplicate register numbers found. The first student record is retained."
        )
        students = students.drop_duplicates("register_no")

    for col in IA_COLS:
        marks[col] = pd.to_numeric(marks[col], errors="coerce")

        invalid = (marks[col] < 0) | (marks[col] > IA_MAX)

        if invalid.any():
            warnings.append(
                f"Invalid values in {col} were treated as missing."
            )
            marks.loc[invalid, col] = np.nan

    if marks.duplicated(["register_no", "subject_code"]).any():
        warnings.append(
            "Duplicate student-subject mark records found. First records retained."
        )
        marks = marks.drop_duplicates(["register_no", "subject_code"])

    for col in ["classes_attended", "classes_held"]:
        attendance[col] = pd.to_numeric(
            attendance[col], errors="coerce"
        )

    attendance = attendance.drop_duplicates(
        ["register_no", "subject_code"]
    )

    return (
        students.reset_index(drop=True),
        marks.reset_index(drop=True),
        attendance.reset_index(drop=True),
        errors,
        warnings,
    )


def student_report(
    register_no: str,
    marks: pd.DataFrame,
    attendance: pd.DataFrame,
) -> pd.DataFrame:

    student_marks = marks[
        marks["register_no"] == register_no
    ].copy()

    student_attendance = attendance[
        attendance["register_no"] == register_no
    ][["subject_code", "classes_attended", "classes_held"]]

    report = student_marks.merge(
        student_attendance,
        on="subject_code",
        how="left",
    )

    report = add_ia_metrics(report)

    valid = report["classes_held"].fillna(0) > 0

    report["Attendance %"] = (
        report["classes_attended"]
        / report["classes_held"]
        * 100
    ).where(valid).round(1)

    return report


# ============================================================
# HTML TABLES AND CSS
# ============================================================

def md(markup: str):
    st.markdown(markup, unsafe_allow_html=True)


def html_table(
    df: pd.DataFrame,
    badge_col: str | None = None,
) -> str:

    headers = "".join(
        f"<th>{esc(column)}</th>" for column in df.columns
    )

    rows = []

    for _, row in df.iterrows():
        cells = []

        for column in df.columns:
            value = row[column]

            if column == badge_col:
                color = CAT_COLORS.get(value, "#8895ad")
                text_color = (
                    "#1f2f4d"
                    if value == "NEEDS ATTENTION"
                    else "#ffffff"
                )

                cells.append(
                    f'<td><span class="badge" '
                    f'style="background:{color};color:{text_color}">'
                    f'{esc(value)}</span></td>'
                )
            else:
                cells.append(f"<td>{esc(value)}</td>")

        rows.append("<tr>" + "".join(cells) + "</tr>")

    return (
        '<div class="tbl-wrap"><table class="tbl">'
        f"<thead><tr>{headers}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def inject_css():
    st.markdown(
        """
        <style>
        .stApp {
            background: #f2f6fc;
            color: #172b4d;
            font-family: "Segoe UI", Arial, sans-serif;
        }

        #MainMenu, footer {
            visibility: hidden;
        }

        header[data-testid="stHeader"] {
            background: transparent;
        }

        .block-container {
            max-width: 1550px;
            padding: 1.4rem 2rem 2rem;
        }

        section[data-testid="stSidebar"] {
            background: #ffffff;
            border-right: 1px solid #e2eaf5;
        }

        section[data-testid="stSidebar"] .block-container {
            padding: 1.2rem 1rem;
        }

        .logo-fallback {
            font-size: 1.7rem;
            font-weight: 900;
            color: #c62828;
            padding: 8px 4px 16px;
        }

        .side-note {
            border-top: 1px solid #e5ebf4;
            padding-top: 16px;
            margin-top: 24px;
            color: #8392aa;
            font-size: 0.78rem;
            line-height: 1.5;
        }

        section[data-testid="stSidebar"]
        [data-testid="stBaseButton-primary"],
        section[data-testid="stSidebar"]
        [data-testid="stBaseButton-secondary"] {
            width: 100%;
            min-height: 42px;
            border-radius: 8px;
            justify-content: flex-start;
            padding: 8px 12px;
            margin-bottom: 5px;
            font-size: 0.92rem;
            font-weight: 600;
            text-align: left;
        }

        section[data-testid="stSidebar"]
        [data-testid="stBaseButton-primary"] {
            background: #1877ed;
            border: 1px solid #1877ed;
        }

        section[data-testid="stSidebar"]
        [data-testid="stBaseButton-primary"] p {
            color: white;
        }

        section[data-testid="stSidebar"]
        [data-testid="stBaseButton-secondary"] {
            background: transparent;
            border: 1px solid transparent;
        }

        section[data-testid="stSidebar"]
        [data-testid="stBaseButton-secondary"] p {
            color: #102a56;
        }

        section[data-testid="stSidebar"]
        [data-testid="stBaseButton-secondary"]:hover {
            background: #edf4ff;
            border-color: #dce9ff;
        }

        .hdr {
            background: linear-gradient(
                100deg, #d9e9ff, #eaf3ff 55%, #d1e4ff
            );
            border: 1px solid #d5e4f8;
            border-radius: 16px;
            padding: 22px 28px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 18px;
            flex-wrap: wrap;
            margin-bottom: 18px;
        }

        .hdr .title {
            font-size: 1.65rem;
            font-weight: 800;
            color: #102a56;
            line-height: 1.25;
        }

        .hdr .subtitle {
            color: #48658e;
            font-size: 0.95rem;
            margin-top: 7px;
        }

        .hdr .staff {
            text-align: right;
            color: #48658e;
            font-size: 0.85rem;
        }

        .hdr .staff b {
            display: block;
            color: #102a56;
            font-size: 1.25rem;
            font-weight: 800;
            margin-bottom: 4px;
        }

        .stat {
            background: white;
            border: 1px solid #e1eaf6;
            border-radius: 15px;
            padding: 18px;
            display: flex;
            align-items: center;
            gap: 16px;
            box-shadow: 0 3px 10px rgba(30, 64, 120, 0.05);
            min-height: 115px;
        }

        .ico {
            width: 56px;
            height: 56px;
            border-radius: 50%;
            background: #e3efff;
            color: #1877ed;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.5rem;
            flex-shrink: 0;
        }

        .stat .label {
            color: #5a7094;
            font-size: 0.88rem;
        }

        .stat .number {
            color: #102a56;
            font-size: 2rem;
            font-weight: 800;
            line-height: 1.2;
        }

        .stat .small {
            color: #6c80a0;
            font-size: 0.8rem;
        }

        [class*="st-key-card"] {
            background: #ffffff;
            border: 1px solid #e1eaf6;
            border-radius: 15px;
            padding: 17px;
            box-shadow: 0 3px 10px rgba(30, 64, 120, 0.04);
        }

        .card-title {
            font-size: 1.15rem;
            font-weight: 800;
            color: #102a56;
            margin-bottom: 14px;
        }

        .muted {
            color: #7184a3;
            font-size: 0.82rem;
        }

        .tbl-wrap {
            overflow-x: auto;
            border: 1px solid #e1eaf6;
            border-radius: 10px;
            background: #ffffff;
        }

        .tbl {
            width: 100%;
            border-collapse: collapse;
            font-size: 0.9rem;
            color: #203653;
        }

        .tbl th {
            background: #e8f1ff;
            color: #102a56;
            text-align: left;
            padding: 11px 12px;
            font-weight: 700;
            white-space: nowrap;
        }

        .tbl td {
            padding: 10px 12px;
            border-top: 1px solid #eaf0f8;
        }

        .tbl tbody tr:hover {
            background: #f7faff;
        }

        .badge {
            display: inline-block;
            padding: 4px 10px;
            border-radius: 999px;
            font-size: 0.72rem;
            font-weight: 700;
            white-space: nowrap;
        }

        .action-card {
            background: #eaf3ff;
            border: 1px solid #d5e6ff;
            border-radius: 13px;
            padding: 16px;
            display: flex;
            align-items: center;
            gap: 14px;
            margin-bottom: 10px;
        }

        .action-card .action-title {
            font-weight: 800;
            color: #102a56;
            font-size: 1rem;
        }

        .action-card .description {
            color: #49658e;
            font-size: 0.85rem;
            line-height: 1.5;
        }

        .arrow {
            width: 35px;
            height: 35px;
            border-radius: 50%;
            background: #1877ed;
            color: #ffffff;
            display: flex;
            align-items: center;
            justify-content: center;
            margin-left: auto;
            flex-shrink: 0;
        }

        .info-grid {
            display: flex;
            flex-wrap: wrap;
            gap: 18px 38px;
            background: white;
            border: 1px solid #e1eaf6;
            border-radius: 14px;
            padding: 18px;
            margin: 12px 0;
        }

        .info-grid span {
            display: block;
            color: #7184a3;
            font-size: 0.78rem;
        }

        .info-grid b {
            color: #102a56;
            font-size: 1rem;
        }

        .tiles {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(145px, 1fr));
            gap: 12px;
            margin: 12px 0;
        }

        .tile {
            background: white;
            border: 1px solid #e1eaf6;
            border-top: 4px solid;
            border-radius: 12px;
            padding: 14px;
        }

        .tile b {
            font-size: 1.7rem;
            color: #102a56;
            display: block;
        }

        .tile span {
            font-size: 0.78rem;
            color: #5a7094;
        }

        div[data-testid="stDownloadButton"] button {
            width: 100%;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


# ============================================================
# NAVIGATION
# ============================================================

def goto(page: str):
    st.session_state["page"] = page


def sidebar():
    with st.sidebar:
        st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

        if LOGO_PATH.exists():
            st.image(str(LOGO_PATH), width=205)
        else:
            md('<div class="logo-fallback">KARUNYA</div>')

        st.markdown("<div style='height:15px'></div>", unsafe_allow_html=True)
        menu_items = [
            ("▦", "Dashboard"),
            ("👤", "Student Analysis"),
            ("👥", "Class Analysis"),
            ("▥", "Subject Reports"),
            ("⇩", "Export Reports"),
        ]

        for icon, page_name in menu_items:
            active = st.session_state["page"] == page_name

            if st.button(
                f"{icon}   {page_name}",
                key=f"nav_{page_name}",
                type="primary" if active else "secondary",
                use_container_width=True,
            ):
                goto(page_name)
                st.rerun()

        md(
            '<div class="side-note">'
            'STAFF PORTAL<br><br>'
            'Demo data · Not connected to EduServe'
            '</div>'
        )


def header(subtitle: str = ""):
    md(
        '<div class="hdr">'
        '<div>'
        '<div class="title">Student Academic Performance Dashboard</div>'
        f'<div class="subtitle">{esc(subtitle)}</div>'
        '</div>'
        '<div class="staff">'
        f'<b>{esc(STAFF)}</b>'
        f'{esc(DEPT)}'
        '</div>'
        '</div>'
    )


# ============================================================
# CHARTS AND SUMMARY CARDS
# ============================================================

def category_chart(counts: dict, height: int = 300) -> go.Figure:
    labels = [
        f"{category.title()}<br>({CAT_RANGES[category]})"
        for category in CATS
    ]

    values = [counts[category] for category in CATS]

    fig = go.Figure(
        go.Bar(
            x=labels,
            y=values,
            text=values,
            textposition="outside",
            cliponaxis=False,
            marker_color=[CAT_COLORS[c] for c in CATS],
            hovertemplate="%{x}: %{y} students<extra></extra>",
        )
    )

    fig.update_layout(
        height=height,
        margin=dict(l=10, r=10, t=25, b=15),
        showlegend=False,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        yaxis=dict(
            title="No. of Students",
            range=[0, max(max(values), 1) * 1.25],
            gridcolor="#e8eef7",
            rangemode="tozero",
        ),
        xaxis=dict(tickangle=0, tickfont=dict(size=10)),
        font=dict(color="#203653"),
    )

    return fig


def category_tiles(counts: dict):
    md(
        '<div class="tiles">'
        + "".join(
            f'<div class="tile" style="border-top-color:{CAT_COLORS[c]}">'
            f'<b>{counts[c]}</b>'
            f'<span>{c.title()} ({CAT_RANGES[c]})</span>'
            '</div>'
            for c in CATS
        )
        + "</div>"
    )


def stat_card(icon: str, label: str, number, small: str):
    md(
        f'<div class="stat">'
        f'<div class="ico">{icon}</div>'
        f'<div>'
        f'<div class="label">{label}</div>'
        f'<div class="number">{number}</div>'
        f'<div class="small">{small}</div>'
        f'</div></div>'
    )


# ============================================================
# PAGE 1: DASHBOARD
# ============================================================

def page_dashboard(students, marks, attendance):
    header("ECE · Internal Assessment Overview")

    student_count = students["register_no"].nunique()

    subjects = (
        marks[["subject_code", "subject"]]
        .drop_duplicates("subject_code")
        .reset_index(drop=True)
    )

    subject_count = len(subjects)
    record_count = len(marks)

    c1, c2, c3 = st.columns(3)

    with c1:
        stat_card("👥", "Assigned Students", student_count, "Students")

    with c2:
        stat_card("📘", "Subjects Managed", subject_count, "Subjects")

    with c3:
        stat_card(
            "▤",
            "Total Records",
            record_count,
            f"Student-subject records ({student_count} × {subject_count})",
        )

    st.write("")

    left, right = st.columns([1.08, 1])

    with left:
        with st.container(key="card_subjects"):
            md(f'<div class="card-title">Subjects ({subject_count})</div>')

            table = pd.DataFrame({
                "S. No.": range(1, subject_count + 1),
                "Subject Code": subjects["subject_code"],
                "Subject Name": subjects["subject"],
            })

            md(html_table(table))

    with right:
        with st.container(key="card_actions"):
            md('<div class="card-title">Quick Actions</div>')

            actions = [
                (
                    "👤",
                    "Individual Student Analysis",
                    "Search a student and view IA marks, average percentages and recommendations.",
                    "Student Analysis",
                ),
                (
                    "👥",
                    "Whole-Class Analysis",
                    "Upload class marks and analyse overall performance with recommendations.",
                    "Class Analysis",
                ),
            ]

            for icon, title, description, page_name in actions:
                md(
                    '<div class="action-card">'
                    f'<div class="ico">{icon}</div>'
                    '<div>'
                    f'<div class="action-title">{title}</div>'
                    f'<div class="description">{description}</div>'
                    '</div>'
                    '<div class="arrow">›</div>'
                    '</div>'
                )

                if st.button(
                    f"Open {page_name}",
                    key=f"quick_{page_name}",
                    type="primary",
                    use_container_width=True,
                ):
                    goto(page_name)
                    st.rerun()

    st.write("")


    with st.container(key="card_chart"):
        md('<div class="card-title">Class Performance Overview</div>')

        if not subjects.empty:
            labels = dict(
                zip(
                    subjects["subject_code"],
                    subjects["subject_code"]
                    + " — "
                    + subjects["subject"],
                )
            )

            selected_code = st.selectbox(
                "Select Subject",
                subjects["subject_code"].tolist(),
                format_func=labels.get,
                key="dashboard_subject",
            )

            subject_marks = marks[
                marks["subject_code"] == selected_code
            ].copy()

            subject_marks = add_ia_metrics(subject_marks)
            counts = category_counts(subject_marks["Recommendation"])

            st.plotly_chart(
                category_chart(counts, 330),
                use_container_width=True,
                config={"displayModeBar": False},
                key="dashboard_chart",
            )

            incomplete = int(
                (subject_marks["Recommendation"] == INCOMPLETE).sum()
            )

            if incomplete:
                st.caption(
                    f"{incomplete} incomplete record(s) excluded from "
                    "recommendation categories."
                )


# ============================================================
# PAGE 2: INDIVIDUAL STUDENT ANALYSIS
# ============================================================

def page_student(students, marks, attendance):
    header("Individual Student Analysis")

    with st.container(key="card_search"):
        md('<div class="card-title">Find a Student</div>')

        query = st.text_input(
            "Search by student name or register number",
            placeholder="Enter a name or register number",
        ).strip()

    if not query:
        st.info(
            "Enter a student name or register number to view the student's "
            "subject-wise performance."
        )
        return

    mask = (
        students["student_name"].str.contains(
            query, case=False, regex=False, na=False
        )
        | students["register_no"].str.contains(
            query, case=False, regex=False, na=False
        )
    )

    found = students[mask]

    if found.empty:
        st.warning("No student found. Check the name or register number.")
        return

    exact = found[
        found["register_no"].str.lower() == query.lower()
    ]

    if len(exact) == 1:
        found = exact

    if len(found) > 1:
        options = dict(
            zip(
                found["register_no"],
                found["register_no"] + " — " + found["student_name"],
            )
        )

        register_no = st.selectbox(
            "Select the correct student",
            found["register_no"].tolist(),
            format_func=options.get,
        )
    else:
        register_no = found.iloc[0]["register_no"]

    student = students[
        students["register_no"] == register_no
    ].iloc[0]

    student_event_key = f"{register_no}|{query.lower()}"
    if st.session_state.get("last_logged_student_search") != student_event_key:
        log_activity("Student History", f"Searched student: {student['student_name']} ({register_no})")
        st.session_state["last_logged_student_search"] = student_event_key

    md(
        '<div class="info-grid">'
        f'<div><span>Student Name</span><b>{esc(student["student_name"])}</b></div>'
        f'<div><span>Register Number</span><b>{esc(register_no)}</b></div>'
        f'<div><span>Department</span><b>{esc(student["department"])}</b></div>'
        f'<div><span>Semester</span><b>{esc(student["semester"])}</b></div>'
        '</div>'
    )

    report = student_report(register_no, marks, attendance)

    if report.empty:
        st.warning("No marks records exist for this student.")
        return

    display = pd.DataFrame({
        "Subject Code": report["subject_code"],
        "Subject Name": report["subject"],
        "IA1": report["IA1"].map(fmt_mark),
        "IA2": report["IA2"].map(fmt_mark),
        "IA3": report["IA3"].map(fmt_mark),
        "Average %": report["Average %"].map(fmt_pct),
        "Study Recommendation": report["Recommendation"],
        "Attendance %": report["Attendance %"].map(fmt_pct),
    })

    with st.container(key="card_student_report"):
        md(
            '<div class="card-title">'
            'Subject-wise Assessment · IA marks out of 40'
            '</div>'
        )

        md(html_table(display, badge_col="Study Recommendation"))

        md(
            '<div class="muted" style="margin-top:10px">'
            'Recommendations depend only on the average of IA1, IA2 and IA3. '
            'Attendance is displayed separately and does not change the '
            'study recommendation.'
            '</div>'
        )

        with st.expander("Recommendation Guide"):
            for category in CATS:
                md(
                    f'<p><span class="badge" '
                    f'style="background:{CAT_COLORS[category]};color:'
                    f'{"#203653" if category == "NEEDS ATTENTION" else "#fff"}">'
                    f'{category}</span> &nbsp;'
                    f'<b>{CAT_RANGES[category]}</b> — '
                    f'{CAT_ADVICE[category]}</p>'
                )

    download = display.copy()
    download.insert(0, "Register Number", register_no)
    download.insert(1, "Student Name", student["student_name"])

    student_pdf = create_pdf_report(
        download,
        "Individual Student Academic Report",
        f"Student: {student['student_name']} | Register Number: {register_no}",
    )

    st.download_button(
        "Download Student Report (PDF)",
        data=student_pdf,
        file_name=f"{register_no}_report.pdf",
        mime="application/pdf",
        use_container_width=True,
        on_click=log_activity,
        args=("Report Download", f"Student report requested: {register_no}"),
    )


# ============================================================
# FILE UPLOAD HELPERS FOR WHOLE-CLASS ANALYSIS
# ============================================================

def norm_name(column) -> str:
    return re.sub(
        r"[^a-z0-9]+",
        "_",
        str(column).strip().lower(),
    ).strip("_")


def read_upload(file) -> pd.DataFrame:
    name = file.name.lower()

    try:
        if name.endswith(".csv"):
            return pd.read_csv(file, dtype=str)

        if name.endswith((".xlsx", ".xls")):
            return pd.read_excel(file, dtype=str)

        if name.endswith(".pdf"):
            if pdfplumber is None:
                raise ValueError(
                    "PDF support requires pdfplumber. Install it using "
                    "'python -m pip install pdfplumber'."
                )

            rows = []

            with pdfplumber.open(file) as pdf:
                for page in pdf.pages:
                    for table in page.extract_tables():
                        rows.extend(table)

            if not rows:
                raise ValueError(
                    "No readable table was found in the PDF. "
                    "Try a CSV or Excel file instead."
                )

            header_row = [
                str(value or "").strip()
                for value in rows[0]
            ]

            body = [
                row for row in rows[1:]
                if len(row) == len(header_row)
            ]

            return pd.DataFrame(body, columns=header_row)

    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Could not read the uploaded file: {exc}") from exc

    raise ValueError(
        "Unsupported file type. Upload CSV, Excel or a text-based PDF."
    )


def clean_cell(value):
    if value is None or (
        isinstance(value, float) and np.isnan(value)
    ):
        return np.nan

    text = str(value).strip()

    if text == "" or text.lower() in ("nan", "none"):
        return np.nan

    return text


def process_class(raw: pd.DataFrame):
    errors = []
    warnings = []

    if raw is None or raw.empty or raw.dropna(how="all").empty:
        return None, None, ["The file contains no data rows."], warnings

    lookup = {
        alias: canonical
        for canonical, aliases in ALIASES.items()
        for alias in aliases
    }

    new_columns = [
        lookup.get(norm_name(column), str(column).strip())
        for column in raw.columns
    ]

    duplicates = {
        column for column in new_columns
        if new_columns.count(column) > 1
    }

    if duplicates:
        errors.append(
            "Duplicate columns after column-name normalisation: "
            + ", ".join(sorted(duplicates))
        )

    data = raw.copy()
    data.columns = new_columns

    if "register_no" not in data.columns:
        errors.append(
            "Missing register number column. Use register_no or reg_no."
        )

    if "subject" not in data.columns and "subject_code" not in data.columns:
        errors.append("Missing subject or subject_code column.")

    ia_mode = all(column in data.columns for column in IA_COLS)
    single_marks_mode = all(
        column in data.columns
        for column in ["marks_obtained", "max_marks"]
    )

    if not ia_mode and not single_marks_mode:
        errors.append(
            "Provide IA1, IA2 and IA3, or marks_obtained and max_marks."
        )

    if errors:
        return None, None, errors, warnings

    data = (
        data.reset_index(drop=True)
        .apply(lambda column: column.map(clean_cell))
    )

    file_rows = data.index + 2

    if "subject" not in data.columns:
        data["subject"] = data["subject_code"]

    if "student_name" not in data.columns:
        data["student_name"] = np.nan
        warnings.append(
            "No student_name column was provided. Names will be shown as "
            "'Name not provided'."
        )

    reasons = [[] for _ in range(len(data))]

    def flag(mask, message):
        for index in np.flatnonzero(np.asarray(mask)):
            reasons[index].append(message)

    flag(data["register_no"].isna(), "Missing register number")
    flag(data["subject"].isna(), "Missing subject")

    if ia_mode:
        for column in IA_COLS:
            numeric = pd.to_numeric(data[column], errors="coerce")
            data[column] = numeric

            flag(numeric.isna(), f"{column} missing or not numeric")
            flag(
                (numeric < 0) | (numeric > IA_MAX),
                f"{column} outside 0–{IA_MAX}",
            )

    else:
        obtained = pd.to_numeric(
            data["marks_obtained"], errors="coerce"
        )

        maximum = pd.to_numeric(
            data["max_marks"], errors="coerce"
        )

        flag(obtained.isna(), "Marks obtained missing or invalid")
        flag(maximum.isna(), "Maximum marks missing or invalid")
        flag(maximum <= 0, "Maximum marks must be positive")
        flag(obtained < 0, "Negative marks are invalid")
        flag(
            (obtained > maximum) & (maximum > 0),
            "Marks exceed maximum marks",
        )

    keys = [
        "register_no",
        "subject_code" if "subject_code" in data.columns else "subject",
    ]

    valid_indices = [
        index for index, row_reasons in enumerate(reasons)
        if not row_reasons
    ]

    duplicate_mask = np.zeros(len(data), dtype=bool)

    if valid_indices:
        duplicate_values = (
            data.iloc[valid_indices]
            .duplicated(keys, keep="first")
            .to_numpy()
        )

        duplicate_mask[
            np.array(valid_indices, dtype=int)[duplicate_values]
        ] = True

    flag(
        duplicate_mask,
        "Duplicate student-subject record",
    )

    valid_mask = np.array([
        not row_reasons for row_reasons in reasons
    ])

    rejected = data[~valid_mask].copy()
    rejected.insert(0, "File Row", file_rows[~valid_mask])
    rejected["Reason"] = [
        "; ".join(row_reasons)
        for row_reasons, valid in zip(reasons, valid_mask)
        if not valid
    ]

    good = data[valid_mask].copy()

    if good.empty:
        return (
            None,
            rejected,
            ["No valid records remain after validation."],
            warnings,
        )

    if ia_mode:
        good = add_ia_metrics(good)
    else:
        good["Marks Obtained"] = obtained[valid_mask].values
        good["Max Marks"] = maximum[valid_mask].values

        good["Average %"] = (
            good["Marks Obtained"] / good["Max Marks"] * 100
        ).round(6)

        good["Recommendation"] = good["Average %"].map(classify)

    good["student_name"] = good["student_name"].fillna(
        "Name not provided"
    )

    rename = {
        "register_no": "Register Number",
        "student_name": "Student Name",
        "subject_code": "Subject Code",
        "subject": "Subject Name",
    }

    columns = [
        column for column in [
            "register_no",
            "student_name",
            "subject_code",
            "subject",
        ]
        if column in good.columns
    ]

    columns += (
        IA_COLS
        if ia_mode
        else ["Marks Obtained", "Max Marks"]
    )

    columns += ["Average %", "Recommendation"]

    result = good[columns].rename(columns=rename)
    result["Average %"] = result["Average %"].round(1)

    return (
        result.reset_index(drop=True),
        rejected.reset_index(drop=True),
        errors,
        warnings,
    )


# ============================================================
# PAGE 3: WHOLE-CLASS ANALYSIS
# ============================================================

def page_class():
    header("Whole-Class Analysis")

    with st.container(key="card_upload"):
        md('<div class="card-title">Upload Class Marks</div>')

        st.write(
            "Upload a CSV, Excel or text-based PDF containing student marks. "
            "For IA analysis, provide IA1, IA2 and IA3 as marks out of 40."
        )

        st.caption(
            "Required columns: register_no, subject, IA1, IA2, IA3. "
            "student_name and subject_code are recommended."
        )

        uploaded_file = st.file_uploader(
            "Choose class marks file",
            type=["csv", "xlsx", "xls", "pdf"],
        )

    if uploaded_file is None:
        st.info("Upload a class marks file to begin the analysis.")
        return

    upload_key = f"{uploaded_file.name}|{getattr(uploaded_file, 'size', 0)}"
    if st.session_state.get("last_logged_class_upload") != upload_key:
        log_activity("Class Analysis", f"Uploaded class marks file: {uploaded_file.name}")
        st.session_state["last_logged_class_upload"] = upload_key

    try:
        raw = read_upload(uploaded_file)
    except ValueError as exc:
        st.error(str(exc))
        return

    result, rejected, errors, warnings = process_class(raw)

    process_key = f"{upload_key}|{len(raw)}"
    if result is not None and st.session_state.get("last_logged_class_processed") != process_key:
        log_activity("Class Analysis", f"Processed {len(result)} valid record(s) from {uploaded_file.name}")
        st.session_state["last_logged_class_processed"] = process_key

    for error in errors:
        st.error(error)

    for warning in warnings:
        st.warning(warning)

    if rejected is not None and not rejected.empty:
        st.warning(
            f"{len(rejected)} row(s) were excluded. Review the reasons below."
        )

        with st.expander("View rejected rows"):
            st.dataframe(rejected, use_container_width=True, hide_index=True)

            rejected_pdf = create_pdf_report(
                rejected,
                "Rejected Class-Marks Rows",
                f"Rows excluded during validation: {len(rejected)}",
            )

            st.download_button(
                "Download Rejected Rows (PDF)",
                data=rejected_pdf,
                file_name="rejected_rows.pdf",
                mime="application/pdf",
                use_container_width=True,
                on_click=log_activity,
                args=("Report Download", f"Rejected rows report requested: {len(rejected)} row(s)"),
            )

    if result is None:
        return

    subjects = sorted(result["Subject Name"].unique())

    selected_subject = "All subjects"

    if len(subjects) > 1:
        selected_subject = st.selectbox(
            "Filter by subject",
            ["All subjects"] + subjects,
        )

    view = (
        result
        if selected_subject == "All subjects"
        else result[result["Subject Name"] == selected_subject]
    )

    st.success(
        f"Processed {len(view)} valid record(s) across "
        f"{view['Register Number'].nunique()} student(s)."
    )

    counts = category_counts(view["Recommendation"])
    category_tiles(counts)

    left, right = st.columns([1, 1.4])

    with left:
        with st.container(key="card_class_chart"):
            md('<div class="card-title">Category Distribution</div>')

            st.plotly_chart(
                category_chart(counts, 330),
                use_container_width=True,
                config={"displayModeBar": False},
                key="class_chart",
            )

    with right:
        with st.container(key="card_class_table"):
            md('<div class="card-title">Processed Results</div>')

            st.dataframe(
                view,
                use_container_width=True,
                hide_index=True,
            )

    st.subheader("Download Reports")

    class_pdf = create_pdf_report(
        view,
        "Complete Class Academic Report",
        f"Subject filter: {selected_subject} | Records: {len(view)}",
    )

    st.download_button(
        "Download Complete Class Report (PDF)",
        data=class_pdf,
        file_name="class_report.pdf",
        mime="application/pdf",
        use_container_width=True,
        on_click=log_activity,
        args=("Report Download", f"Complete class report requested: {len(view)} record(s)"),
    )

    cols = st.columns(4)

    for column, category in zip(cols, CATS):
        subset = view[view["Recommendation"] == category]

        with column:
            category_pdf = create_pdf_report(
                subset,
                f"{category.title()} Student Report",
                f"Matching records: {len(subset)}",
            )

            st.download_button(
                f"Download {category.title()} PDF ({len(subset)})",
                data=category_pdf,
                file_name=f"{category.lower().replace(' ', '_')}.pdf",
                mime="application/pdf",
                disabled=subset.empty,
                use_container_width=True,
                on_click=log_activity,
                args=("Report Download", f"{category.title()} class report requested: {len(subset)} record(s)"),
            )


# ============================================================
# PAGE 4: SUBJECT REPORTS
# ============================================================

def page_subject_reports(students, marks, attendance):
    header("Subject Reports")

    subjects = (
        marks[["subject_code", "subject"]]
        .drop_duplicates("subject_code")
        .reset_index(drop=True)
    )

    if subjects.empty:
        st.warning("No subject records are available.")
        return

    options = dict(
        zip(
            subjects["subject_code"],
            subjects["subject_code"] + " — " + subjects["subject"],
        )
    )

    selected_code = st.selectbox(
        "Select Subject",
        subjects["subject_code"].tolist(),
        format_func=options.get,
        key="subject_reports_select",
    )

    # Record a subject search only when the selected subject changes.
    subject_event_key = f"subject_report_search|{selected_code}"
    if st.session_state.get("last_logged_subject_search") != subject_event_key:
        log_activity(
            "Subject Report Search",
            f"Searched subject: {options[selected_code]}",
        )
        st.session_state["last_logged_subject_search"] = subject_event_key

    # Get marks for the selected subject
    subject_marks = marks[
        marks["subject_code"] == selected_code
    ].copy()

    # Ensure student names are obtained from the student dataset
    subject_marks = subject_marks.drop(
        columns=["student_name"], errors="ignore"
    )

    student_details = students[
        ["register_no", "student_name"]
    ].drop_duplicates("register_no")

    subject_marks = subject_marks.merge(
        student_details,
        on="register_no",
        how="left",
    )

    subject_marks["student_name"] = (
        subject_marks["student_name"].fillna("Name not available")
    )

    # Calculate average IA marks and recommendations
    subject_marks = add_ia_metrics(subject_marks)

    # Get attendance for the selected subject
    subject_attendance = attendance[
        attendance["subject_code"] == selected_code
    ][["register_no", "classes_attended", "classes_held"]].copy()

    subject_attendance = subject_attendance.drop_duplicates(
        "register_no"
    )

    subject_report = subject_marks.merge(
        subject_attendance,
        on="register_no",
        how="left",
    )

    # Calculate attendance percentage
    valid_attendance = (
        pd.to_numeric(
            subject_report["classes_held"], errors="coerce"
        ).fillna(0) > 0
    )

    attended = pd.to_numeric(
        subject_report["classes_attended"], errors="coerce"
    )

    held = pd.to_numeric(
        subject_report["classes_held"], errors="coerce"
    )

    subject_report["Attendance %"] = (
        attended / held * 100
    ).where(valid_attendance).round(1)

    # Build the report table
    report = pd.DataFrame({
        "Register Number": subject_report["register_no"],
        "Student Name": subject_report["student_name"],
        "Subject Code": subject_report["subject_code"],
        "Subject Name": subject_report["subject"],
        "IA1": subject_report["IA1"].map(fmt_mark),
        "IA2": subject_report["IA2"].map(fmt_mark),
        "IA3": subject_report["IA3"].map(fmt_mark),
        "Average %": subject_report["Average %"].map(fmt_pct),
        "Recommendation": subject_report["Recommendation"],
        "Attendance %": subject_report["Attendance %"].map(fmt_pct),
    })

    # Show recommendation category totals
    counts = category_counts(subject_report["Recommendation"])
    category_tiles(counts)

    st.subheader("Student Performance")

    md(html_table(report, badge_col="Recommendation"))

    st.caption(
        "Recommendations are based on the average of IA1, IA2 and IA3. "
        "Attendance is calculated separately and does not change the "
        "study recommendation."
    )

    # Download report as PDF
    pdf_bytes = create_pdf_report(
        report,
        "Subject-wise Academic Performance Report",
        f"Subject: {options[selected_code]} | "
        f"Total Students: {len(report)}",
    )

    st.download_button(
        "Download Subject Report (PDF)",
        data=pdf_bytes,
        file_name=f"{selected_code}_subject_report.pdf",
        mime="application/pdf",
        use_container_width=True,
        on_click=log_activity,
        args=("Report Download", f"Subject report requested: {options[selected_code]}"),
    )



# ============================================================
# PAGE 5: EXPORT REPORTS
# ============================================================


def create_pdf_report(report, title, subtitle=""):
    """Create a professionally formatted, multi-page PDF table."""
    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=15 * mm,
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Title"],
        textColor=colors.HexColor("#102A56"),
        alignment=TA_CENTER,
        fontSize=17,
        leading=21,
        spaceAfter=5,
    )

    subtitle_style = ParagraphStyle(
        "ReportSubtitle",
        parent=styles["Normal"],
        textColor=colors.HexColor("#526A8B"),
        alignment=TA_CENTER,
        fontSize=9,
        leading=12,
        spaceAfter=12,
    )

    cell_style = ParagraphStyle(
        "TableCell",
        parent=styles["Normal"],
        fontSize=7.5,
        leading=9,
        textColor=colors.HexColor("#203653"),
        wordWrap="CJK",
    )

    header_style = ParagraphStyle(
        "TableHeader",
        parent=cell_style,
        textColor=colors.white,
        fontName="Helvetica-Bold",
    )

    story = [
        Paragraph("KARUNYA", title_style),
        Paragraph(title, title_style),
    ]

    if subtitle:
        story.append(Paragraph(subtitle, subtitle_style))

    story.append(Spacer(1, 4 * mm))

    if report.empty:
        story.append(Paragraph("No matching records found.", cell_style))
    else:
        table_data = [
            [Paragraph(xml_escape(str(col)), header_style) for col in report.columns]
        ]

        for _, row in report.iterrows():
            table_data.append([
                Paragraph(
                    xml_escape(str(value)) if pd.notna(value) else "N/A",
                    cell_style,
                )
                for value in row
            ])

        available_width = landscape(A4)[0] - 24 * mm
        column_count = len(report.columns)
        col_width = available_width / max(column_count, 1)

        table = Table(
            table_data,
            colWidths=[col_width] * column_count,
            repeatRows=1,
            hAlign="LEFT",
        )

        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0),
             colors.HexColor("#176FE5")),
            ("GRID", (0, 0), (-1, -1), 0.35,
             colors.HexColor("#D9E4F2")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [
                colors.white,
                colors.HexColor("#F2F7FF"),
            ]),
        ]))

        story.append(table)

    def add_page_number(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#526A8B"))
        canvas.drawRightString(
            landscape(A4)[0] - 12 * mm,
            7 * mm,
            f"Page {document.page}",
        )
        canvas.restoreState()

    doc.build(
        story,
        onFirstPage=add_page_number,
        onLaterPages=add_page_number,
    )

    buffer.seek(0)
    return buffer.getvalue()


def page_export_reports(students, marks, attendance):
    header("Export Reports")

    st.write(
        "Select a subject, assessment and recommendation level "
        "to generate a downloadable PDF report."
    )

    # --------------------------------------------------------
    # 1. Subject selection
    # --------------------------------------------------------
    subjects = (
        marks[["subject_code", "subject"]]
        .drop_duplicates("subject_code")
        .reset_index(drop=True)
    )

    if subjects.empty:
        st.warning("No subject records are available.")
        return

    subject_options = {
        row["subject_code"]:
            f'{row["subject_code"]} — {row["subject"]}'
        for _, row in subjects.iterrows()
    }

    selected_code = st.selectbox(
        "1. Select Subject",
        subjects["subject_code"].tolist(),
        format_func=subject_options.get,
        key="export_subject",
    )

    # --------------------------------------------------------
    # 2. Assessment selection
    # --------------------------------------------------------
    assessment = st.selectbox(
        "2. Select Assessment",
        ["IA1", "IA2", "IA3", "All"],
        key="export_assessment",
    )

    # --------------------------------------------------------
    # 3. Recommendation selection
    # --------------------------------------------------------
    selected_level = st.selectbox(
        "3. Select Recommendation Level",
        [
            "All",
            "MAINTAIN",
            "PRACTICE",
            "NEEDS ATTENTION",
            "HIGH PRIORITY",
        ],
        format_func=lambda x: (
            "All Students" if x == "All" else x.title()
        ),
        key="export_level",
    )

    # --------------------------------------------------------
    # 4. Prepare the subject records and student names
    # --------------------------------------------------------
    subject_marks = marks[
        marks["subject_code"] == selected_code
    ].copy()

    subject_marks = subject_marks.drop(
        columns=["student_name"], errors="ignore"
    )

    student_details = students[
        ["register_no", "student_name"]
    ].drop_duplicates("register_no")

    subject_marks = subject_marks.merge(
        student_details,
        on="register_no",
        how="left",
    )

    subject_marks["student_name"] = (
        subject_marks["student_name"].fillna("Name not available")
    )

    for col in ["IA1", "IA2", "IA3"]:
        subject_marks[col] = pd.to_numeric(
            subject_marks[col], errors="coerce"
        )

    # The original IA marks are out of 40.
    # Convert each assessment to a percentage out of 100.
    for col in ["IA1", "IA2", "IA3"]:
        subject_marks[f"{col} %"] = (
            subject_marks[col] / 40 * 100
        ).round(1)

    # Average is calculated from all three assessments.
    complete = subject_marks[["IA1", "IA2", "IA3"]].notna().all(axis=1)

    subject_marks["Average %"] = (
        subject_marks[["IA1", "IA2", "IA3"]].mean(axis=1)
        / 40 * 100
    ).where(complete).round(1)

    # Recommendation follows the selected assessment.
    if assessment == "All":
        subject_marks["Recommendation"] = (
            subject_marks["Average %"].map(classify)
        )
    else:
        subject_marks["Recommendation"] = (
            subject_marks[f"{assessment} %"].map(classify)
        )

    # --------------------------------------------------------
    # 5. Filter by recommendation
    # --------------------------------------------------------
    filtered = subject_marks.copy()

    if selected_level != "All":
        filtered = filtered[
            filtered["Recommendation"] == selected_level
        ].copy()

    # Sort by recommendation priority and then student name.
    category_order = {
        "HIGH PRIORITY": 0,
        "NEEDS ATTENTION": 1,
        "PRACTICE": 2,
        "MAINTAIN": 3,
        "INCOMPLETE": 4,
    }

    filtered["_sort"] = filtered["Recommendation"].map(
        category_order
    ).fillna(4)

    filtered = filtered.sort_values(
        ["_sort", "student_name"],
        kind="stable",
    ).drop(columns="_sort")

    # --------------------------------------------------------
    # 6. Show a preview
    # --------------------------------------------------------
    st.subheader("Report Preview")

    st.caption(
        f"Subject: {subject_options[selected_code]} | "
        f"Assessment: {assessment} | "
        f"Recommendation: "
        f"{'All students' if selected_level == 'All' else selected_level}"
    )

    st.metric("Matching Student Records", len(filtered))

    if assessment == "All":
        report = pd.DataFrame({
            "Register Number": filtered["register_no"],
            "Student Name": filtered["student_name"],
            "IA1 / 40": filtered["IA1"],
            "IA2 / 40": filtered["IA2"],
            "IA3 / 40": filtered["IA3"],
            "Average %": filtered["Average %"].map(fmt_pct),
            "Recommendation": filtered["Recommendation"],
        })
    else:
        report = pd.DataFrame({
            "Register Number": filtered["register_no"],
            "Student Name": filtered["student_name"],
            f"{assessment} Marks / 40": filtered[assessment],
            f"{assessment} Percentage": (
                filtered[f"{assessment} %"].map(fmt_pct)
            ),
            "Recommendation": filtered["Recommendation"],
        })

    if report.empty:
        st.info("No students match the selected filters.")
    else:
        st.dataframe(
            report,
            use_container_width=True,
            hide_index=True,
        )

    # --------------------------------------------------------
    # 7. Generate PDF download
    # --------------------------------------------------------
    subtitle = (
        f"Subject: {subject_options[selected_code]} | "
        f"Assessment: {assessment} | "
        f"Level: {selected_level} | "
        f"Records: {len(report)}"
    )

    pdf_bytes = create_pdf_report(
        report,
        "Student Academic Performance Report",
        subtitle,
    )

    safe_subject = re.sub(
        r"[^A-Za-z0-9_-]+", "_", selected_code
    )

    safe_assessment = assessment.lower()
    safe_level = re.sub(
        r"[^A-Za-z0-9_-]+", "_", selected_level.lower()
    )

    st.download_button(
        "Download PDF Report",
        data=pdf_bytes,
        file_name=(
            f"{safe_subject}_{safe_assessment}_{safe_level}_report.pdf"
        ),
        mime="application/pdf",
        use_container_width=True,
        disabled=report.empty,
        on_click=log_activity,
        args=("Report Download", f"Export report requested: {subject_options[selected_code]}, assessment {assessment}, level {selected_level}"),
    )



# ============================================================
# APPLICATION ENTRY POINT
# ============================================================

def main():
    st.set_page_config(
        page_title="Student Academic Performance Dashboard",
        page_icon="🎓",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    inject_css()

    st.session_state.setdefault("page", "Dashboard")

    students, marks, attendance, errors, warnings = load_data()

    sidebar()

    if errors:
        header("Data Files Could Not Be Loaded")

        for error in errors:
            st.error(error)

        st.info(
            "Check that your data folder contains "
            "demo_students.csv, demo_marks.csv and demo_attendance.csv."
        )
        st.stop()

    page = st.session_state["page"]

    if page == "Student Analysis":
        page_student(students, marks, attendance)

    elif page == "Class Analysis":
        page_class()

    elif page == "Subject Reports":
        page_subject_reports(students, marks, attendance)

    elif page == "Export Reports":
        page_export_reports(students, marks, attendance)

    else:
        page_dashboard(students, marks, attendance)

    for warning in warnings:
        st.caption(f"Data note: {warning}")


if __name__ == "__main__":
    main()
