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

# Victoria spans roughly 34S to 39.2S and 141E to 150E. These values put the
# whole state in frame with a little margin.
VIC_LAT, VIC_LON, VIC_ZOOM = -37.0, 145.1, 6.15

# Fixed height. Without it the deck fills whatever the container gives it, and
# a tall container at low zoom pulls Tasmania and South Australia into frame.
MAP_HEIGHT = 460

# The view is bounded to Victoria: you cannot zoom out past the state, which
# keeps the demo on track. Tilt and rotate stay enabled because the extrusion
# only reads in 3D — ctrl-drag or right-drag to tilt.
MIN_ZOOM, MAX_ZOOM = 6.0, 11.0

# Two ramps, because a colour that reads on a dark basemap disappears on a
# light one. Both are sequential: this is a single variable increasing, and a
# rainbow would imply categories that do not exist.
RAMP_LIGHT = [
    (0.0, (255, 245, 200)),
    (10.0, (254, 217, 142)),
    (25.0, (254, 173, 84)),
    (50.0, (242, 119, 42)),
    (75.0, (217, 72, 26)),
    (100.0, (153, 26, 12)),
]

RAMP_DARK = [
    (0.0, (44, 52, 64)),
    (10.0, (78, 66, 58)),
    (25.0, (140, 78, 44)),
    (50.0, (196, 96, 38)),
    (75.0, (232, 122, 44)),
    (100.0, (252, 176, 72)),
]

# Carto basemaps, no API key required. Satellite would need a Mapbox token.
MAP_STYLES = {
    "Dark": ("dark", RAMP_DARK),
    "Road": ("road", RAMP_LIGHT),
    "Light": ("light", RAMP_LIGHT),
}


def _colour(pct: Optional[float], ramp=None, alpha: int = 200) -> list[int]:
    """Interpolate a ramp. Missing values fade rather than reading as zero."""
    ramp = ramp or RAMP_DARK
    if pct is None or pd.isna(pct):
        return [*ramp[0][1], 90]

    pct = max(0.0, min(100.0, float(pct)))
    for i in range(len(ramp) - 1):
        lo_v, lo_c = ramp[i]
        hi_v, hi_c = ramp[i + 1]
        if lo_v <= pct <= hi_v:
            span = hi_v - lo_v
            t = 0.0 if span == 0 else (pct - lo_v) / span
            return [int(lo_c[j] + (hi_c[j] - lo_c[j]) * t) for j in range(3)] + [alpha]
    return [*ramp[-1][1], alpha]


def _deck(df: pd.DataFrame, tooltip: dict, zoom: float, lat: float, lon: float,
          elevation: bool = False, style: str = "road") -> pdk.Deck:
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
        get_line_color=[255, 255, 255, 40],
        line_width_min_pixels=0,
        opacity=0.8,
    )
    view_state = pdk.ViewState(
        latitude=lat,
        longitude=lon,
        zoom=zoom,
        min_zoom=MIN_ZOOM,
        max_zoom=MAX_ZOOM,
        pitch=35 if elevation else 0,
        bearing=0,
    )
    return pdk.Deck(
        layers=[layer],
        height=MAP_HEIGHT,
        initial_view_state=view_state,
        views=[
            pdk.View(
                type="MapView",
                controller={"dragRotate": True, "touchRotate": True,
                            "doubleClickZoom": False, "keyboard": True},
            )
        ],
        map_style=style,
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

    st.markdown("#### Where the network meets fire country")
    choice = st.radio(
        "Basemap", list(MAP_STYLES), horizontal=True,
        label_visibility="collapsed", key="map_style",
    )
    style, ramp = MAP_STYLES[choice]

    df["fill_color"] = df["avg_pct_extent_burnt"].apply(lambda v: _colour(v, ramp))
    df["elevation"] = df["high_exposure_segments"].fillna(0) * 90

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

    st.pydeck_chart(
        _deck(df, tooltip, VIC_ZOOM, VIC_LAT, VIC_LON, elevation=True, style=style)
    )

    st.caption(
        "Each hexagon is roughly 8.5 km across. Colour and height show how much "
        "of the overhead network in that cell runs through country burnt by "
        "major bushfires. Hover for detail, ctrl-drag to tilt."
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
    hexes["fill_color"] = [[217, 72, 26, 215]] * len(hexes)

    lat, lon, zoom = _frame(hexes)
    tooltip = {
        "html": "<b>{count} segment(s)</b> in this cell",
        "style": {"backgroundColor": "#11151c", "color": "#e8edf5",
                  "fontSize": "12px", "borderRadius": "6px"},
    }

    style = MAP_STYLES.get(st.session_state.get("map_style", "Road"), ("road", None))[0]
    st.pydeck_chart(_deck(hexes, tooltip, zoom, lat, lon, style=style))

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
    """Result maps stay on the state view. Without cell centroids we cannot
    frame a subset reliably, and guessing wrong puts the camera in the ocean.
    Zooming in from a known-good view is the safer default."""
    return VIC_LAT, VIC_LON, VIC_ZOOM
