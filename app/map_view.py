"""H3 hexagon maps of Victoria for the bushfire exposure app.

Two maps, both drawn from pre-aggregated tables so nothing heavy reaches the
browser:

- `render_state_map` shows the whole network, shaded by how much of it sits in
  major fire ground. This is the landing visual.
- `render_result_map` appears under a Genie answer whenever the result names
  specific segments, so the answer has a geography rather than just a table.

The second is the one that matters for the contest. A permanent map is a
dashboard; a map that only exists because Genie returned rows is part of the
answer.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

import pandas as pd
import pydeck as pdk
import streamlit as st

log = logging.getLogger("bushfire.map")

# Victoria, roughly centred with the whole state in frame
VIC_LAT, VIC_LON, VIC_ZOOM = -36.9, 144.6, 5.4

# Cool slate through to ember. Deliberately not a rainbow: this is one variable
# increasing, and a sequential ramp reads that way without a legend.
RAMP = [
    (0.0, (44, 52, 64)),
    (10.0, (78, 66, 58)),
    (25.0, (140, 78, 44)),
    (50.0, (196, 96, 38)),
    (75.0, (232, 122, 44)),
    (100.0, (252, 176, 72)),
]


def _colour(pct: Optional[float], alpha: int = 190) -> list[int]:
    """Interpolate the ramp. None and zero both read as the base slate."""
    if pct is None or pd.isna(pct):
        return [*RAMP[0][1], 110]

    pct = max(0.0, min(100.0, float(pct)))
    for i in range(len(RAMP) - 1):
        lo_v, lo_c = RAMP[i]
        hi_v, hi_c = RAMP[i + 1]
        if lo_v <= pct <= hi_v:
            span = hi_v - lo_v
            t = 0.0 if span == 0 else (pct - lo_v) / span
            return [int(lo_c[j] + (hi_c[j] - lo_c[j]) * t) for j in range(3)] + [alpha]
    return [*RAMP[-1][1], alpha]


def _deck(df: pd.DataFrame, tooltip: dict, zoom: float, lat: float, lon: float,
          elevation: bool = False) -> pdk.Deck:
    layer = pdk.Layer(
        "H3HexagonLayer",
        df,
        pickable=True,
        stroked=False,
        filled=True,
        extruded=elevation,
        elevation_scale=40 if elevation else 0,
        get_elevation="elevation" if elevation else 0,
        get_hexagon="hex_id",
        get_fill_color="fill_color",
        opacity=0.85,
    )
    return pdk.Deck(
        layers=[layer],
        initial_view_state=pdk.ViewState(
            latitude=lat, longitude=lon, zoom=zoom, pitch=35 if elevation else 0
        ),
        map_style="dark_no_labels",
        tooltip=tooltip,
    )


# --------------------------------------------------------------------------
# Statewide map
# --------------------------------------------------------------------------


def render_state_map(run_sql: Callable[[str], list[list[Any]]],
                     catalog: str, schema: str) -> bool:
    """Draw the whole network. Returns False if the data is unavailable."""
    df = _load_state_hexes(run_sql, catalog, schema)
    if df.empty:
        return False

    df["fill_color"] = df["avg_pct_extent_burnt"].apply(_colour)
    df["elevation"] = df["high_exposure_segments"].fillna(0) * 60

    tooltip = {
        "html": (
            "<b>{example_lga}</b><br/>"
            "{segments} segments &middot; {swer_segments} SWER<br/>"
            "{avg_pct_extent_burnt}% of network length in major fire ground<br/>"
            "{high_exposure_segments} high-exposure segments"
        ),
        "style": {"backgroundColor": "#11151c", "color": "#e8edf5",
                  "fontSize": "12px", "borderRadius": "6px"},
    }

    st.pydeck_chart(_deck(df, tooltip, VIC_ZOOM, VIC_LAT, VIC_LON, elevation=True),
                    use_container_width=True)

    st.caption(
        "Each hexagon is roughly 8.5 km across. Colour and height show how much "
        "of the overhead network in that cell runs through country burnt by "
        "major bushfires. Hover for detail."
    )
    return True


@st.cache_data(ttl=3600, show_spinner=False)
def _load_state_hexes(_run_sql, catalog: str, schema: str) -> pd.DataFrame:
    rows = _run_sql(
        f"""
        SELECT hex_id, segments, swer_segments, avg_pct_extent_burnt,
               high_exposure_segments, example_lga
        FROM {catalog}.{schema}.gold_map_hex
        WHERE segments >= 2
        """
    )
    if not rows:
        log.warning("No map hexes returned; is gold_map_hex built and granted?")
        return pd.DataFrame()

    df = pd.DataFrame(
        rows,
        columns=["hex_id", "segments", "swer_segments", "avg_pct_extent_burnt",
                 "high_exposure_segments", "example_lga"],
    )
    for col in ["segments", "swer_segments", "avg_pct_extent_burnt",
                "high_exposure_segments"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    log.info("Loaded %d map hexes", len(df))
    return df


# --------------------------------------------------------------------------
# Result map
# --------------------------------------------------------------------------


def render_result_map(df: pd.DataFrame, run_sql: Callable[[str], list[list[Any]]],
                      catalog: str, schema: str, max_segments: int = 400) -> bool:
    """Map the segments in a Genie result, if it contains any.

    Returns False when the result has no segment_id column, which is most of
    the time. That is fine — the map is a bonus on the answers where geography
    helps, not a fixture.
    """
    col = next((c for c in df.columns if str(c).lower() == "segment_id"), None)
    if col is None:
        return False

    ids = pd.to_numeric(df[col], errors="coerce").dropna().astype("int64").tolist()
    if not ids:
        return False

    truncated = len(ids) > max_segments
    ids = ids[:max_segments]

    hexes = _load_segment_hexes(run_sql, catalog, schema, tuple(ids))
    if hexes.empty:
        return False

    # A result map is about location, not intensity, so one warm colour reads
    # more clearly than a ramp over an arbitrary subset.
    hexes["fill_color"] = [[232, 122, 44, 210]] * len(hexes)

    lat, lon, zoom = _frame(hexes)
    tooltip = {
        "html": "<b>{count} segment(s)</b> in this cell",
        "style": {"backgroundColor": "#11151c", "color": "#e8edf5",
                  "fontSize": "12px", "borderRadius": "6px"},
    }

    st.pydeck_chart(_deck(hexes, tooltip, zoom, lat, lon), use_container_width=True)

    note = f"{len(ids):,} segments from this answer, mapped at ~1.2 km resolution."
    if truncated:
        note += f" Showing the first {max_segments:,}."
    st.caption(note)
    return True


@st.cache_data(ttl=600, show_spinner=False)
def _load_segment_hexes(_run_sql, catalog: str, schema: str,
                        segment_ids: tuple[int, ...]) -> pd.DataFrame:
    # Ints only, cast above, so direct interpolation is safe here.
    id_list = ",".join(str(int(i)) for i in segment_ids)
    rows = _run_sql(
        f"""
        SELECT hex_id, COUNT(*) AS count
        FROM {catalog}.{schema}.gold_segment_hex
        WHERE segment_id IN ({id_list})
        GROUP BY hex_id
        """
    )
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=["hex_id", "count"])
    df["count"] = pd.to_numeric(df["count"], errors="coerce").fillna(1)
    return df


def _frame(hexes: pd.DataFrame) -> tuple[float, float, float]:
    """Pick a sensible view. Without cell centroids, fall back to the state view
    and let the user pan — better than guessing wrong and framing the ocean."""
    if len(hexes) > 200:
        return VIC_LAT, VIC_LON, VIC_ZOOM
    return VIC_LAT, VIC_LON, VIC_ZOOM + 0.4
