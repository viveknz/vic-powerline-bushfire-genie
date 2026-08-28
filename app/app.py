"""Victorian Powerline Bushfire Exposure Console.

A Databricks App that puts a Genie Agent in front of bushfire exposure data for
Victoria's overhead electricity network.

The chat is the product. Everything else on screen exists to give a first-time
visitor enough context to ask a good question, and enough transparency to trust
the answer.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any, Optional

import pandas as pd
import streamlit as st
from databricks.sdk import WorkspaceClient

import map_view
from genie_client import GenieClient, GenieError, GenieTurn, rows_to_records

# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    stream=sys.stdout,
    force=True,
)
log = logging.getLogger("bushfire.app")

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

GENIE_SPACE_ID = os.environ.get("GENIE_SPACE_ID", "")
WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
CATALOG = os.environ.get("UC_CATALOG", "workspace")
SCHEMA = os.environ.get("UC_SCHEMA", "bushfire")

SUGGESTED_QUESTIONS = [
    "Which councils have the most bushfire-exposed powerline network?",
    "Are SWER lines more exposed to bushfire than other high voltage lines?",
    "Which segments should we inspect first?",
    "What network did the 2019/20 Black Summer fires affect?",
    "Which fires were caused by powerlines?",
]

st.set_page_config(
    page_title="Victorian Powerline Bushfire Exposure",
    page_icon="🔥",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
      .block-container { padding-top: 2rem; max-width: 1200px; }

      div[data-testid="stMetricValue"] { font-size: 1.6rem; }
      div[data-testid="stMetricLabel"] { font-size: 0.8rem; opacity: 0.75; }

      .stChatMessage { background: transparent; }
      code { font-size: 0.85rem; }

      /* Streamlit ships a chevron for the collapsed sidebar. Swap it for a
         hamburger, which people recognise without thinking about it. */
      div[data-testid="collapsedControl"] button svg,
      div[data-testid="stSidebarCollapsedControl"] button svg { display: none; }

      div[data-testid="collapsedControl"] button::after,
      div[data-testid="stSidebarCollapsedControl"] button::after {
        content: "\2630";
        font-size: 1.35rem;
        line-height: 1;
        color: #1a1d23;
      }

      div[data-testid="collapsedControl"] button,
      div[data-testid="stSidebarCollapsedControl"] button {
        background: #f4efe8;
        border: 1px solid #e2d9cc;
        border-radius: 8px;
        padding: 0.35rem 0.6rem;
      }

      /* Give the map room to breathe */
      iframe[title="st.iframe"], .stDeckGlJsonChart { border-radius: 10px; }
    </style>
    """,
    unsafe_allow_html=True,
)


# --------------------------------------------------------------------------
# Clients
# --------------------------------------------------------------------------


@st.cache_resource
def get_workspace_client() -> WorkspaceClient:
    return WorkspaceClient()


@st.cache_resource
def get_genie_client() -> Optional[GenieClient]:
    if not GENIE_SPACE_ID:
        log.error("GENIE_SPACE_ID is not set")
        return None
    try:
        return GenieClient(space_id=GENIE_SPACE_ID, workspace_client=get_workspace_client())
    except Exception:
        log.exception("Could not create Genie client")
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def run_sql(statement: str) -> list[list[Any]]:
    """Run a small statement against the warehouse. Used only for header stats."""
    if not WAREHOUSE_ID:
        log.warning("DATABRICKS_WAREHOUSE_ID not set; skipping header stats")
        return []
    try:
        w = get_workspace_client()
        response = w.statement_execution.execute_statement(
            warehouse_id=WAREHOUSE_ID,
            statement=statement,
            wait_timeout="30s",
        )
        result = getattr(response, "result", None)
        return [list(r) for r in (getattr(result, "data_array", None) or [])]
    except Exception:
        log.exception("Header stats query failed")
        return []


@st.cache_data(ttl=3600, show_spinner=False)
def load_header_stats() -> dict[str, Optional[int]]:
    """Four figures that establish the domain before anyone asks anything."""
    rows = run_sql(
        f"""
        SELECT
          (SELECT COUNT(*) FROM {CATALOG}.{SCHEMA}.v_segment_exposure),
          (SELECT COUNT(*) FROM {CATALOG}.{SCHEMA}.v_fire_history),
          (SELECT COUNT(*) FROM {CATALOG}.{SCHEMA}.v_segment_exposure
             WHERE bushfire_exposure_band = 'High'),
          (SELECT COUNT(*) FROM {CATALOG}.{SCHEMA}.v_fire_history
             WHERE powerline_caused = TRUE)
        """
    )
    if not rows or len(rows[0]) < 4:
        return {"segments": None, "fires": None, "high": None, "powerline": None}

    def as_int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    r = rows[0]
    return {
        "segments": as_int(r[0]),
        "fires": as_int(r[1]),
        "high": as_int(r[2]),
        "powerline": as_int(r[3]),
    }


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def render_header() -> None:
    st.title("Victorian Powerline Bushfire Exposure")
    st.caption(
        "Ask questions about where Victoria's overhead electricity network "
        "meets country that burns. Fire history 1903 to 2026."
    )

    stats = load_header_stats()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Line segments", f"{stats['segments']:,}" if stats["segments"] else "—")
    c2.metric("Fires on record", f"{stats['fires']:,}" if stats["fires"] else "—")
    c3.metric(
        "High exposure segments",
        f"{stats['high']:,}" if stats["high"] is not None else "—",
        help="Segments near four or more major bushfires (1,000 ha or larger)",
    )
    c4.metric(
        "Powerline-caused fires",
        f"{stats['powerline']:,}" if stats["powerline"] is not None else "—",
        help="Cause is investigated for only about 3% of fires, so this is a floor",
    )
    st.divider()


def render_sidebar() -> None:
    """The semantic layer is 90% of the work and 0% visible. This fixes that."""
    with st.sidebar:
        banner = os.path.join(os.path.dirname(os.path.abspath(__file__)), "thumbnail.png")
        if os.path.exists(banner):
            st.image(banner, use_column_width=True)
        st.subheader("How this works")
        st.markdown(
            "Every question goes to a **Genie Agent** which writes SQL against "
            "three curated views. No question is pre-built — ask anything the "
            "data can answer."
        )

        with st.expander("Where the data comes from"):
            st.markdown(
                """
                **Fire History Scar** — DEECA. Fire boundaries since 1903,
                109,219 polygons resolving to 17,934 distinct fires.

                **Vicmap Infrastructure** — powerline network. 396,455 segments,
                filtered to transmission and HV distribution.

                **Vicmap Admin** — local government boundaries.

                The two are joined by **H3 hexagonal indexing at resolution 8**,
                so "near" means sharing a cell roughly 460 m across.
                """
            )

        with st.expander("What Genie has been taught"):
            st.markdown(
                """
                The data has traps. Genie is instructed around each of them:

                - **Seasons run July to June.** Season 2020 is the 2019/20 summer.
                - **Planned burns are not bushfires.** They outnumber bushfires
                  and indicate fuel management, not risk.
                - **Fires are mapped as many polygons.** One fire can be 868
                  fragments, so everything counts distinct fires.
                - **Areas cannot be summed.** Polygons overlap; a total would
                  roughly double count.
                - **Cause is known for ~3% of fires.** Any cause answer is a
                  floor, not a total.
                - **Segments vary from 200 m to 100 km.** Raw counts favour long
                  transmission lines, so exposure is also expressed as a
                  percentage of segment length.
                """
            )

        with st.expander("Known limits"):
            st.markdown(
                """
                - Low voltage lines are excluded.
                - Exposure is proximity within ~460 m, not exact intersection.
                - Each segment is attributed to one council, so cross-boundary
                  fires undercount councils affected.
                - Pre-1980 records lack region, district and cause.
                """
            )

        st.divider()
        if st.button("Clear conversation", use_container_width=True):
            st.session_state.messages = []
            st.session_state.conversation_id = None
            st.rerun()

        st.caption(
            "Source: Vicmap Infrastructure and Fire History Scar, "
            "State Government of Victoria (DEECA), CC BY 4.0."
        )


def pick_chart(df: pd.DataFrame) -> Optional[str]:
    """Decide whether a chart helps. Returns 'bar', 'line' or None.

    Deliberately conservative: a bad chart is worse than no chart, and Genie's
    tables are already readable.
    """
    if df.empty or len(df.columns) < 2 or len(df) > 30:
        return None

    numeric = df.select_dtypes(include="number").columns
    if len(numeric) == 0:
        return None

    first = df.columns[0]
    if first in numeric:
        return None

    # A season or year column reads better as a line
    if any(k in str(first).lower() for k in ("season", "year", "date")):
        return "line"
    return "bar"


def render_result(turn: GenieTurn, key: str) -> None:
    """Everything below the prose answer: SQL, table, chart, download."""
    if turn.sql:
        with st.expander("Show the SQL Genie wrote"):
            if turn.sql_description:
                st.caption(turn.sql_description)
            st.code(turn.sql, language="sql")

    if not turn.has_data:
        return

    df = pd.DataFrame(rows_to_records(turn))

    # Values arrive as strings; convert what is genuinely numeric so charts
    # and sorting behave.
    for col in df.columns:
        converted = pd.to_numeric(df[col], errors="coerce")
        if converted.notna().all():
            df[col] = converted

    chart = pick_chart(df)
    if chart:
        tab_table, tab_chart = st.tabs(["Table", "Chart"])
        with tab_table:
            st.dataframe(df, use_container_width=True, hide_index=True)
        with tab_chart:
            try:
                indexed = df.set_index(df.columns[0])
                numeric_only = indexed.select_dtypes(include="number")
                if chart == "line":
                    st.line_chart(numeric_only)
                else:
                    st.bar_chart(numeric_only)
            except Exception:
                log.exception("Chart rendering failed")
                st.info("Could not chart this result.")
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)

    footer = f"{turn.row_count:,} rows"
    if turn.truncated or len(turn.rows) < turn.row_count:
        footer += f" (showing {len(turn.rows):,})"
    footer += f" · {turn.elapsed_seconds}s"
    st.caption(footer)

    try:
        if map_view.render_result_map(df, run_sql, CATALOG, SCHEMA):
            log.info("Rendered result map for %s", key)
    except Exception:
        log.exception("Result map failed")

    st.download_button(
        "Download CSV",
        df.to_csv(index=False).encode("utf-8"),
        file_name=f"bushfire_result_{key}.csv",
        mime="text/csv",
        key=f"dl_{key}",
    )


def ask_genie(question: str) -> None:
    """Send a question, stream status, store the turn."""
    client = get_genie_client()
    if client is None:
        st.error(
            f"Genie is not configured. GENIE_SPACE_ID is "
            f"{'empty' if not GENIE_SPACE_ID else repr(GENIE_SPACE_ID)}. "
            "Check app.yaml and that the service principal has Can Run on the agent."
        )
        return

    st.session_state.messages.append({"role": "user", "content": question})

    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        status_box = st.status("Sending your question", expanded=False)

        def on_status(_raw: str, label: str) -> None:
            status_box.update(label=label)

        try:
            turn = client.ask(
                question,
                conversation_id=st.session_state.conversation_id,
                on_status=on_status,
            )
        except GenieError as exc:
            log.exception("Genie call failed")
            status_box.update(label="Failed", state="error")
            st.error(f"Genie call failed: {exc}")
            st.session_state.messages.append(
                {"role": "assistant", "content": f"Error: {exc}", "turn": None}
            )
            return
        except Exception as exc:  # noqa: BLE001 - never fail silently
            log.exception("Unexpected error while asking Genie")
            status_box.update(label="Failed", state="error")
            st.error(f"Unexpected error: {type(exc).__name__}: {exc}")
            st.exception(exc)
            st.session_state.messages.append(
                {"role": "assistant", "content": f"Error: {exc}", "turn": None}
            )
            return

        status_box.update(label=f"Answered in {turn.elapsed_seconds}s", state="complete")
        st.session_state.conversation_id = turn.conversation_id

        if turn.follow_up:
            st.info(turn.follow_up)
        if turn.text:
            st.markdown(turn.text)
        if turn.error and not turn.text:
            st.warning(turn.error)

        render_result(turn, key=turn.message_id)

    st.session_state.messages.append(
        {"role": "assistant", "content": turn.text or turn.error or "", "turn": turn}
    )


def replay_history() -> None:
    for i, msg in enumerate(st.session_state.messages):
        with st.chat_message(msg["role"]):
            if msg["role"] == "user":
                st.markdown(msg["content"])
                continue
            turn: Optional[GenieTurn] = msg.get("turn")
            if turn and turn.follow_up:
                st.info(turn.follow_up)
            if msg["content"]:
                st.markdown(msg["content"])
            if turn:
                render_result(turn, key=f"{turn.message_id}_{i}")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def main() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "conversation_id" not in st.session_state:
        st.session_state.conversation_id = None

    render_header()
    render_sidebar()

    pending: Optional[str] = None

    if not st.session_state.messages:
        try:
            map_view.render_state_map(run_sql, CATALOG, SCHEMA)
        except Exception:
            log.exception("State map failed")
            st.info("Map unavailable. Ask a question below.")

        st.markdown("**Try one of these**")
        cols = st.columns(2)
        for i, question in enumerate(SUGGESTED_QUESTIONS):
            if cols[i % 2].button(question, key=f"sq_{i}", use_container_width=True):
                pending = question
        st.caption(
            "Or ask your own. Follow-up questions keep the thread, so "
            "\"just the top three\" works after any answer."
        )
    else:
        replay_history()

    typed = st.chat_input("Ask about bushfire exposure on the network…")
    if typed:
        pending = typed

    if pending:
        ask_genie(pending)


if __name__ == "__main__":
    main()
