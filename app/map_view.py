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
# keeps the demo on track. The map is flat — extruded hexagons were tried and
# the height channel only duplicated what colour already carries, at the cost
# of obscuring the coastline.
MIN_ZOOM, MAX_ZOOM = 6.0, 11.0

# Two ramps, because a colour that reads on a dark basemap disappears on a
# light one. Both are sequential: this is a single variable increasing, and a
# rainbow would imply categories that do not exist.
#
# The zero value matters as much as the top of the ramp. A cell with network
# that has never burnt is a real, meaningful result — not an absence. It gets
# a cool blue that is clearly a colour rather than a hole, so the only black
# on the map means "no network here".
RAMP_LIGHT = [
    (0.0, (108, 148, 178)),
    (0.1, (255, 241, 186)),
    (10.0, (254, 214, 133)),
    (25.0, (253, 168, 79)),
    (50.0, (240, 114, 40)),
    (75.0, (214, 68, 24)),
    (100.0, (148, 24, 10)),
]

RAMP_DARK = [
    (0.0, (62, 96, 130)),
    (0.1, (120, 104, 84)),
    (10.0, (162, 108, 60)),
    (25.0, (198, 118, 48)),
    (50.0, (224, 140, 48)),
    (75.0, (243, 172, 64)),
    (100.0, (255, 214, 122)),
]

# Carto basemaps, no API key required. Satellite would need a Mapbox token.
#
# Voyager is the default: it carries terrain, parks, water and town names, so
# a reader who has never seen Victoria can place what they are looking at.
# Positron was tried and is too washed out to be worth a slot.
MAP_STYLES = {
    "Colour": ("road", RAMP_LIGHT),
    "Dark": ("dark", RAMP_DARK),
}


def _colour(pct: Optional[float], ramp=None, alpha: int = 175) -> list[int]:
    """Interpolate a ramp. Missing values fade rather than reading as zero."""
    ramp = ramp or RAMP_DARK
    if pct is None or pd.isna(pct):
        return [120, 120, 120, 70]

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
          style: str = "dark") -> pdk.Deck:
    layer = pdk.Layer(
        "H3HexagonLayer",
        df,
        pickable=True,
        stroked=True,
        filled=True,
        extruded=False,
        get_hexagon="hex_id",
        get_fill_color="fill_color",
        get_line_color=[255, 255, 255, 25],
        line_width_min_pixels=0.5,
        opacity=0.78,
    )
    view_state = pdk.ViewState(
        latitude=lat,
        longitude=lon,
        zoom=zoom,
        min_zoom=MIN_ZOOM,
        max_zoom=MAX_ZOOM,
        pitch=0,
        bearing=0,
    )
    return pdk.Deck(
        layers=[layer],
        height=MAP_HEIGHT,
        initial_view_state=view_state,
        views=[
            pdk.View(
                type="MapView",
                controller={"dragRotate": False, "touchRotate": False,
                            "doubleClickZoom": False, "keyboard": True},
            )
        ],
        map_style=style,
        tooltip=tooltip,
    )


def _legend(ramp) -> str:
    """Inline HTML legend. Deck.gl has no built-in one and a colour scale
    without labels is just decoration."""
    stops = [
        (0.0, "Never burnt"),
        (10.0, "10%"),
        (25.0, "25%"),
        (50.0, "50%"),
        (75.0, "75%"),
        (100.0, "100%"),
    ]
    swatches = []
    for value, label in stops:
        r, g, b, _ = _colour(value, ramp)
        swatches.append(
            f'<span style="display:inline-flex;align-items:center;gap:5px;'
            f'margin-right:14px;">'
            f'<span style="width:15px;height:15px;border-radius:3px;'
            f'background:rgb({r},{g},{b});'
            f'border:1px solid rgba(255,255,255,0.18);"></span>'
            f'<span style="font-size:0.74rem;color:#9aa6ba;">{label}</span>'
            f"</span>"
        )
    return (
        '<div style="margin:6px 0 2px 0;">'
        '<span style="font-size:0.74rem;color:#7b869a;margin-right:12px;">'
        "Share of network length burnt by major bushfire:</span>"
        + "".join(swatches)
        + '<div style="font-size:0.72rem;color:#7b869a;margin-top:6px;">'
        "Areas with no hexagon have no overhead network in this dataset."
        "</div></div>"
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

    st.markdown(
        "#### Victoria, Australia — bushfire exposure across the powerline network"
    )
    choice = st.radio(
        "Basemap", list(MAP_STYLES), horizontal=True,
        label_visibility="collapsed", key="map_style",
    )
    style, ramp = MAP_STYLES[choice]

    df["fill_color"] = df["avg_pct_extent_burnt"].apply(lambda v: _colour(v, ramp))

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

    st.pydeck_chart(_deck(df, tooltip, VIC_ZOOM, VIC_LAT, VIC_LON, style=style))

    st.markdown(_legend(ramp), unsafe_allow_html=True)

    st.caption(
        "The state of Victoria, in south-eastern Australia. Each hexagon is "
        "roughly 8.5 km across, shaded by how much of the overhead network in "
        "that cell runs through country burnt by major bushfires. Hover for detail."
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


# --------------------------------------------------------------------------
# Locator
#
# A reader in Chicago should not have to work out where Victoria is. This is a
# simplified coastline on an equirectangular projection — accuracy beyond
# "clearly Australia, clearly the south-eastern corner" is wasted at 220px.
# --------------------------------------------------------------------------

# (lon, lat) clockwise from the north-west cape
AUSTRALIA = [
    (113.2, -22.0), (114.1, -21.8), (115.0, -21.0), (117.0, -20.4),
    (119.0, -20.0), (121.0, -19.5), (122.2, -18.0), (122.0, -16.5),
    (123.5, -16.4), (124.5, -16.0), (125.5, -14.5), (127.5, -13.9),
    (129.0, -15.0), (130.0, -12.5), (131.0, -12.2), (132.5, -12.0),
    (133.5, -11.7), (135.0, -12.2), (136.0, -12.0), (136.5, -13.5),
    (135.8, -15.0), (137.5, -16.0), (139.5, -17.5), (140.8, -17.5),
    (141.5, -15.0), (141.6, -12.5), (142.5, -10.7), (143.5, -14.0),
    (145.5, -15.0), (146.5, -19.0), (149.0, -21.0), (150.0, -22.5),
    (152.5, -25.0), (153.5, -28.0), (153.0, -31.0), (151.0, -33.5),
    (150.0, -37.0), (148.0, -37.8), (146.5, -38.8), (145.0, -38.5),
    (144.5, -38.4), (143.0, -38.8), (141.0, -38.4), (139.5, -37.0),
    (138.0, -35.0), (137.5, -35.5), (136.0, -35.0), (135.0, -34.7),
    (134.0, -32.7), (132.0, -32.0), (129.0, -31.7), (126.0, -32.3),
    (123.5, -34.0), (120.0, -34.0), (118.0, -35.1), (115.5, -34.5),
    (115.0, -33.5), (115.7, -31.5), (114.8, -29.0), (114.0, -27.0),
    (113.5, -24.5),
]

# Murray River border along the north, then the coast back west
VICTORIA = [
    (141.0, -34.15), (142.5, -34.8), (143.5, -35.4), (144.6, -35.9),
    (146.0, -36.0), (147.3, -36.0), (148.2, -36.5), (149.9, -37.5),
    (148.0, -37.8), (146.5, -38.8), (145.4, -38.4), (144.9, -38.5),
    (144.5, -38.1), (143.5, -38.9), (142.0, -38.4), (141.0, -38.4),
]

TASMANIA = [
    (144.7, -40.7), (146.0, -41.1), (148.3, -40.9), (148.3, -42.9),
    (147.4, -43.1), (146.2, -43.6), (145.2, -42.2), (144.7, -40.7),
]

LON_MIN, LON_MAX = 112.0, 155.0
LAT_MIN, LAT_MAX = -44.5, -10.0

W, H = 220, 176  # locator only
PAD = 6


def _loc_project(lon: float, lat: float) -> tuple[float, float]:
    x = PAD + (lon - LON_MIN) / (LON_MAX - LON_MIN) * (W - 2 * PAD)
    y = PAD + (LAT_MAX - lat) / (LAT_MAX - LAT_MIN) * (H - 2 * PAD)
    return round(x, 1), round(y, 1)


def _loc_path(points) -> str:
    d = " ".join(
        f"{'M' if i == 0 else 'L'}{x},{y}"
        for i, (x, y) in enumerate(_loc_project(lon, lat) for lon, lat in points)
    )
    return d + " Z"


def australia_locator_svg() -> str:
    return f"""<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" \
width="50%" role="img" aria-label="Map of Australia with Victoria highlighted">
  <rect width="{W}" height="{H}" rx="8" fill="#141a24"/>
  <path d="{_loc_path(AUSTRALIA)}" fill="#2a323f" stroke="#3d4757" stroke-width="1"/>
  <path d="{_loc_path(TASMANIA)}" fill="#2a323f" stroke="#3d4757" stroke-width="1"/>
  <path d="{_loc_path(VICTORIA)}" fill="#e07a2c" stroke="#ffb066" stroke-width="1.2"/>
  <text x="{_loc_project(143.8, -32.2)[0]}" y="{_loc_project(143.8, -32.2)[1]}" \
fill="#f0913f" font-size="10" font-weight="700" text-anchor="middle" \
font-family="sans-serif">VICTORIA</text>
</svg>"""


