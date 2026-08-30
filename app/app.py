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

AUTHOR = os.environ.get("APP_AUTHOR", "Vivek")
REPO_URL = os.environ.get(
    "REPO_URL", "https://github.com/viveknz/vic-powerline-bushfire-genie"
)
CHALLENGE_URL = os.environ.get(
    "CHALLENGE_URL",
    "https://community.databricks.com/t5/learning-events/"
    "databricks-community-contest-genie-powered-app-challenge/ev-p/165825",
)

GLOSSARY = [
    (
        "SWER line",
        "Single Wire Earth Return. A cheap way to electrify remote farmland: "
        "one wire instead of two or three, using the earth itself as the "
        "return path. Long spans, few poles, mostly through bush. Victoria has "
        "26,646 of these segments and they are measurably more fire-exposed "
        "than the rest of the high voltage network.",
    ),
    (
        "Segment",
        "One stretch of powerline as published in the state's mapping data. "
        "There is no standard length — a segment may be 200 metres of suburban "
        "line or 100 kilometres of transmission route. That is why exposure is "
        "also given as a percentage of each segment's own length.",
    ),
    (
        "High voltage vs low voltage",
        "High voltage is the network that carries power between towns and along "
        "rural roads, typically 22kV or 12.7kV. Low voltage is the last stretch "
        "into houses. Low voltage is excluded here: it is mostly urban, short, "
        "and often underground, so it carries little bushfire risk.",
    ),
    (
        "Fire season",
        "July to June, labelled by the year it ends. Season 2020 covers the "
        "summer of 2019–20, which is the season often called Black Summer.",
    ),
    (
        "Major bushfire",
        "A bushfire of 1,000 hectares or more. The distinction matters: without "
        "it, six small grassfires on Melbourne's fringe rank alongside the "
        "campaign fires that burn through Gippsland.",
    ),
    (
        "Planned burn",
        "A fire lit deliberately by the fire agency to reduce fuel before "
        "summer. Most fires in the record are these. They indicate active "
        "management, not risk, and are counted separately throughout.",
    ),
    (
        "Exposure band",
        "How many major bushfires have come near a stretch of line. "
        "None (never), Low (one), Moderate (two or three), High (four or "
        "more). 301 segments are High.",
    ),
    (
        "H3 hexagons",
        "The method used to work out which fires came near which powerlines. "
        "Both are converted into a grid of hexagonal cells about 460 metres "
        "across, and anything sharing a cell is treated as near. It is an "
        "approximation, and about as precise as the source data deserves.",
    ),
]

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
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
      .block-container { padding-top: 1.4rem; max-width: 1320px; }

      div[data-testid="stMetricValue"] {
        font-size: 1.85rem;
        font-weight: 700;
        color: #f0913f;
      }
      div[data-testid="stMetricLabel"] {
        font-size: 0.78rem;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        color: #9aa6ba;
      }

      .stChatMessage { background: transparent; }
      code { font-size: 0.85rem; }

      /* Streamlit ships a chevron for the sidebar toggle. Swap it for a
         hamburger, which people recognise without thinking about it. The
         literal glyph is used rather than a CSS escape, which browsers
         mis-parse when the next character is a digit. */
      div[data-testid="collapsedControl"] button svg,
      div[data-testid="stSidebarCollapsedControl"] button svg,
      div[data-testid="stSidebarCollapseButton"] button svg { display: none; }

      div[data-testid="collapsedControl"] button::after,
      div[data-testid="stSidebarCollapsedControl"] button::after,
      div[data-testid="stSidebarCollapseButton"] button::after {
        content: "☰";
        font-size: 1.3rem;
        line-height: 1;
        color: #e8edf5;
      }

      div[data-testid="collapsedControl"] button,
      div[data-testid="stSidebarCollapsedControl"] button,
      div[data-testid="stSidebarCollapseButton"] button {
        background: #1a1f2b;
        border: 1px solid #2c3444;
        border-radius: 8px;
        width: 2.3rem;
        height: 2.3rem;
        min-width: 2.3rem;
        min-height: 2.3rem;
        padding: 0;
        display: flex;
        align-items: center;
        justify-content: center;
        transition: none;
      }

      /* Streamlit swaps the icon on hover, which makes a button with a
         replaced glyph jitter. Freeze everything that could move. */
      div[data-testid="collapsedControl"] button:hover,
      div[data-testid="stSidebarCollapsedControl"] button:hover,
      div[data-testid="stSidebarCollapseButton"] button:hover {
        background: #232a38;
        border-color: #e07a2c;
        transform: none;
      }
      div[data-testid="collapsedControl"] button *,
      div[data-testid="stSidebarCollapsedControl"] button *,
      div[data-testid="stSidebarCollapseButton"] button * {
        display: none !important;
      }

      /* Give the map room to breathe */
      iframe[title="st.iframe"], .stDeckGlJsonChart { border-radius: 10px; }

      /* The chat box is the product. Make it look like it. */
      div[data-testid="stChatInput"] {
        border: 2px solid #e07a2c;
        border-radius: 14px;
        background: #1b212e;
        box-shadow: 0 0 22px rgba(224, 122, 44, 0.16);
      }
      div[data-testid="stChatInput"]:focus-within {
        border-color: #ffa657;
        box-shadow: 0 0 0 3px rgba(224, 122, 44, 0.26);
      }
      div[data-testid="stChatInput"] textarea {
        font-size: 1.02rem;
      }
      div[data-testid="stChatInput"] textarea::placeholder {
        color: #b9c3d4;
        opacity: 1;
      }
      div[data-testid="stBottomBlockContainer"] { padding-bottom: 1.4rem; }

      .credit-line {
        font-size: 0.76rem;
        color: #7b869a;
        letter-spacing: 0.02em;
        margin-top: -0.4rem;
      }
      .credit-line b { color: #9aa6ba; font-weight: 600; }

      /* Streamlit fades the collapse button in on hover. Pin it visible so the
         control is discoverable without hunting for it. */
      div[data-testid="stSidebarCollapseButton"],
      div[data-testid="stSidebarCollapseButton"] button,
      div[data-testid="collapsedControl"],
      div[data-testid="stSidebarCollapsedControl"] {
        opacity: 1 !important;
        visibility: visible !important;
        pointer-events: auto !important;
      }
      section[data-testid="stSidebar"] div[data-testid="stSidebarCollapseButton"] {
        display: flex !important;
      }

      /* Images get a hover overlay and a fullscreen button, which makes the
         sidebar banner jump. Neither is wanted here. */
      div[data-testid="stImage"],
      div[data-testid="stImage"] img {
        transform: none !important;
        transition: none !important;
      }
      div[data-testid="stImage"] button,
      button[data-testid="StyledFullScreenButton"],
      div[data-testid="stFullScreenFrame"] button {
        display: none !important;
      }
      div[data-testid="stImage"] img { border-radius: 8px; }
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
        "Bushfire exposure across the overhead electricity network of Victoria, "
        "Australia. Fire history 1903 to 2026."
    )

    with st.spinner("Waking the SQL warehouse and loading the network…"):
        stats = load_header_stats()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Line segments", f"{stats['segments']:,}" if stats["segments"] else "—")
    c2.metric("Fires on record", f"{stats['fires']:,}" if stats["fires"] else "—")
    c3.metric(
        "High exposure segments",
        f"{stats['high']:,}" if stats["high"] is not None else "—",
        help=(
            "A segment counts as high exposure once four or more separate major "
            "bushfires have come near it. Major means 1,000 hectares or larger. "
            "These are places where fire keeps returning, not places that burnt "
            "once."
        ),
    )
    c4.metric(
        "Powerline-caused fires",
        f"{stats['powerline']:,}" if stats["powerline"] is not None else "—",
        help=(
            "Only about 3 in every 100 fires were ever investigated closely "
            "enough to record what started them. So eight is the number of "
            "confirmed powerline-caused fires. The real figure is higher, and "
            "nobody knows by how much."
        ),
    )

    st.markdown(
        '<p class="credit-line">Powered by <b>Databricks Genie</b> on '
        '<b>Databricks Free Edition</b> &middot; developer preview &middot; '
        f'built by {AUTHOR}</p>',
        unsafe_allow_html=True,
    )
    st.divider()


def render_sidebar() -> None:
    """The semantic layer is 90% of the work and 0% visible. This fixes that."""
    with st.sidebar:
        banner = os.path.join(os.path.dirname(os.path.abspath(__file__)), "thumbnail.png")
        if os.path.exists(banner):
            st.image(banner, use_column_width=True)
        st.markdown(map_view.australia_locator_svg(), unsafe_allow_html=True)
        st.caption("Victoria is Australia's south-eastern mainland state.")

        st.subheader("How this works")
        st.markdown(
            "Every question goes to a **Genie Agent**, which writes the SQL "
            "itself. Nothing here is a pre-built report — ask anything the data "
            "can answer."
        )
        st.markdown(
            """
            It has three tables to work with:

            - **The network** — one row for each stretch of powerline, with what
              has burnt near it
            - **The fires** — one row for each fire since 1903
            - **The link between them** — which fires touched which stretches
            """
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
                Public data has traps in it. Six of them would produce answers
                that look right and are wrong, so Genie is told about each one.

                **A fire season is not a calendar year.** Victorian fire seasons
                run July to June, and the record labels each one by the year it
                ends. Season 2020 means the summer of 2019–20. Ask about "the
                2019 fires" and Genie checks which you mean.

                **Planned burns are not bushfires.** Most fires in the record
                are controlled burns lit deliberately by the fire agency to
                reduce fuel. Counting them alongside bushfires would measure
                fire *management* and call it fire *risk*. They are kept apart.

                **One fire is drawn as many shapes.** When a fire is mapped, the
                agency records it as dozens or hundreds of separate patches —
                one large fire in 2019–20 has 868 of them. Counting shapes would
                say a single fire happened 868 times. Everything counts distinct
                fires instead.

                **Burnt areas cannot be added up.** Those shapes overlap each
                other, so adding their areas roughly doubles the real figure. If
                you ask for total hectares burnt, Genie will explain why it
                cannot give you one.

                **Most fires have no recorded cause.** Only about 3 in every 100
                fires were ever investigated closely enough to record what
                started them. So when Genie says eight fires were caused by
                powerlines, that means eight *confirmed* — the real number is
                higher, and nobody knows by how much.

                **Powerline segments come in wildly different lengths.** The
                network is published as segments, and one may be 200 metres of
                suburban line while another is 100 kilometres of transmission
                route. A long line crosses more fires simply by being long. So
                exposure is also measured as a *percentage of each segment's own
                length*, which compares fairly regardless of size.
                """
            )


        st.divider()

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

        with st.expander("About this app"):
            st.markdown(
                "Overhead powerlines and bushfire have a two-way relationship. "
                "Lines run through country that burns, and lines start fires "
                "when vegetation contacts them. Victoria regulates this through "
                "electric line clearance obligations, and every distribution "
                "business runs a vegetation management program against it.\n\n"
                "Those programs have a budgeting problem: there is more network "
                "than there is inspection money. Deciding which spans get "
                "attention needs a view of which parts of the network sit in "
                "country with a real fire history. The data to answer that is "
                "public. It has just never been joined up.\n\n"
                "This app joins it, and puts a natural-language interface in "
                "front."
            )
            st.markdown(
                f"[Source code and notebooks]({REPO_URL}) &nbsp;·&nbsp; "
                f"[Databricks Genie App Challenge]({CHALLENGE_URL})"
            )

        with st.expander("Data glossary"):
            st.markdown(
                "\n\n".join(f"**{t}**  \n{d}" for t, d in GLOSSARY)
            )

        st.divider()
        st.caption(
            "Source: Vicmap Infrastructure and Fire History Scar, "
            "State Government of Victoria (DEECA), CC BY 4.0."
        )
        st.caption(
            f"Powered by Databricks Genie · Databricks Free Edition · "
            f"developer preview · built by {AUTHOR}"
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


def _is_year_column(name: str, values: pd.Series) -> bool:
    """A year is numeric but not a quantity. Formatting it with a thousands
    separator turns 2020 into "2,020", which looks like a bug because it is
    one."""
    lowered = name.lower()
    if any(k in lowered for k in ("season", "year")):
        return True
    # Catch columns Genie names something else but which hold only plausible
    # years, e.g. a bare "burnt" column of 1903-2026 values.
    numeric = values.dropna()
    if numeric.empty:
        return False
    return bool(
        (numeric % 1 == 0).all()
        and numeric.between(1800, 2100).all()
        and numeric.nunique() > 1
    )


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
    # and sorting behave. Years and seasons are the exception: they are
    # numeric but not quantities, and Streamlit renders them with a thousands
    # separator, so 2020 becomes "2,020".
    for col in df.columns:
        converted = pd.to_numeric(df[col], errors="coerce")
        if not converted.notna().all():
            continue
        if _is_year_column(str(col), converted):
            df[col] = converted.astype("Int64").astype(str)
        else:
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
            st.session_state.messages.append(
                {"role": "assistant", "content": f"Error: {exc}", "turn": None}
            )
            return
        except Exception as exc:  # noqa: BLE001 - never fail silently
            log.exception("Unexpected error while asking Genie")
            status_box.update(label="Failed", state="error")
            st.session_state.messages.append(
                {"role": "assistant", "content": f"Error: {exc}", "turn": None}
            )
            return

        status_box.update(label=f"Answered in {turn.elapsed_seconds}s", state="complete")

    st.session_state.conversation_id = turn.conversation_id
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

    # Map and starter questions live in one collapsible block. Collapsing it
    # gives the conversation the full page once someone is a few questions in,
    # without the layout changing on its own.
    with st.expander("Explore the network", expanded=True):
        map_col, ask_col = st.columns([3, 2], gap="large")

        with map_col:
            try:
                map_view.render_state_map(run_sql, CATALOG, SCHEMA)
            except Exception:
                log.exception("State map failed")
                st.info("Map unavailable. Ask a question on the right.")

        with ask_col:
            st.markdown("#### Ask the data a question")
            st.caption(
                "Genie writes the SQL against three curated views. Nothing here "
                "is pre-built."
            )
            for i, question in enumerate(SUGGESTED_QUESTIONS):
                if st.button(question, key=f"sq_{i}", use_container_width=True):
                    pending = question
            st.caption("Or type your own below. Follow-ups keep the thread.")

    if st.session_state.messages:
        st.divider()
        head, clear = st.columns([5, 1])
        head.markdown("#### Conversation")
        if clear.button("Clear", use_container_width=True,
                        help="Start a new conversation"):
            st.session_state.messages = []
            st.session_state.conversation_id = None
            st.rerun()

        replay_history()

    typed = st.chat_input("Ask about bushfire exposure on the network…")
    if typed:
        pending = typed

    if pending:
        ask_genie(pending)
        # Redraw once so the answer is rendered from history rather than
        # inline. Two rendering paths for the same turn is how the second
        # question ended up completing server-side without appearing.
        st.rerun()


if __name__ == "__main__":
    main()
