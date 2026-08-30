"""
app.py — Argo Float Monitor (Plotly Dash version)
==================================================
Companion implementation of argo-float-monitor.streamlit.app, ported to
Plotly Dash for production-style web deployment.

Run locally:
    python app.py
Then open http://localhost:8050 in your browser.

Deploy:
    gunicorn app:server
"""
import os
import warnings
from pathlib import Path

import dash
from dash import dcc, html, Input, Output, State, callback, no_update, ctx
import pandas as pd
import numpy as np

import argo_helpers as ah
import argo_storage as storage
from tabs.tab_main import build_tab_main
from tabs.tab_metadata import build_tab_metadata
from tabs.tab_health import build_tab_health
from tabs.tab_profiles import build_tab_profiles
from tabs.tab_qc import build_tab_qc
from tabs.tab_delivery import build_tab_delivery
from tabs.tab_traj import build_tab_traj

warnings.filterwarnings("ignore")


# ── Cache directory for downloaded NetCDF files ───────────────────────────────
# Ephemeral working directory. On AWS this is container-local disk; the
# durable copy lives in S3 (see argo_storage.py). Override with ARGO_DATA_DIR.
CACHE_DIR = Path(os.environ.get("ARGO_DATA_DIR", "./data"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ── In-process dataset cache ──────────────────────────────────────────────────
# Avoid reloading NetCDF files on every callback. Without this, switching
# between tabs would re-open six NetCDF files each time. Single-user/demo
# scope; for multi-user production use flask-caching with disk or Redis backend.
_DATASETS_CACHE: dict = {}


def get_datasets(data_dir: str, wmo: str) -> dict:
    """Return the float's NetCDF datasets, loading from disk only once per WMO."""
    key = (data_dir, wmo)
    if key not in _DATASETS_CACHE:
        # A new container instance (or a restart) has empty local disk but the
        # files may already be in S3 from an earlier session — restore first.
        if not any(Path(data_dir).glob(f"{wmo}_*.nc")):
            storage.restore_from_s3(wmo, data_dir)
        _DATASETS_CACHE[key] = ah.load_datasets(data_dir, wmo)
    return _DATASETS_CACHE[key]


def invalidate_dataset_cache(data_dir: str, wmo: str) -> None:
    """Drop the cached datasets for this WMO (call after re-download)."""
    _DATASETS_CACHE.pop((data_dir, wmo), None)


# ══════════════════════════════════════════════════════════════════════════════
# Dash app
# ══════════════════════════════════════════════════════════════════════════════
app = dash.Dash(
    __name__,
    suppress_callback_exceptions=True,
    title="Argo Float Monitor",
    update_title=None,
)
server = app.server  # for gunicorn / App Runner


@server.route("/healthz")
def healthz():
    """Liveness probe for App Runner / load balancers."""
    return {"status": "ok", "s3_cache": storage.s3_enabled()}, 200


# ── Styles ────────────────────────────────────────────────────────────────────
SIDEBAR_STYLE = {
    "position": "fixed", "top": 0, "left": 0, "bottom": 0,
    "width": "240px", "padding": "20px",
    "backgroundColor": "#f7f7f9", "borderRight": "1px solid #e5e5e5",
    "overflowY": "auto", "fontFamily": "system-ui, sans-serif",
}

CONTENT_STYLE = {
    "marginLeft": "260px", "padding": "20px 30px",
    "fontFamily": "system-ui, sans-serif",
}

METRIC_STYLE = {
    "padding": "10px 14px", "minWidth": "120px",
    "borderRight": "1px solid #e5e5e5",
}

METRIC_LABEL = {"fontSize": "11px", "color": "#666",
                "textTransform": "uppercase", "letterSpacing": "0.04em"}
METRIC_VALUE = {"fontSize": "20px", "fontWeight": "600",
                "color": "#222", "marginTop": "2px"}


# ══════════════════════════════════════════════════════════════════════════════
# Layout
# ══════════════════════════════════════════════════════════════════════════════
app.layout = html.Div([
    # ── Stores: hold the active WMO + data directory across callbacks ─────────
    dcc.Store(id="store-wmo"),
    dcc.Store(id="store-data-dir"),

    # ── Sidebar ───────────────────────────────────────────────────────────────
    html.Div([
        html.H3("🌊 Argo Float Monitor", style={"margin": "0 0 16px 0"}),
        html.Hr(style={"margin": "8px 0"}),

        html.Label("WMO number", style={"fontSize": "13px", "color": "#444"}),
        dcc.Input(
            id="wmo-input", type="text", value="5906551",
            debounce=True,
            style={"width": "100%", "padding": "6px 8px",
                   "marginTop": "4px", "marginBottom": "12px"},
        ),

        html.Button(
            "⬇ Download / refresh from GDAC",
            id="fetch-button", n_clicks=0,
            style={"width": "100%", "padding": "8px",
                   "fontSize": "13px", "cursor": "pointer"},
        ),

        # Loading wrapper: shows a spinner while the fetch callback is running.
        # Without this the user has no visual feedback during the 1-3 minute FTP
        # download. (For real progress streaming we'd need background callbacks
        # with a callback manager — out of scope for MVP.)
        dcc.Loading(
            id="fetch-loading",
            type="circle",
            color="#0077B6",
            children=html.Div(
                id="fetch-status",
                style={"marginTop": "10px", "fontSize": "12px",
                       "color": "#666", "minHeight": "20px"},
            ),
            style={"marginTop": "8px"},
        ),

        # Static expectation-setting text. Visible all the time so the user
        # knows what to expect on first download.
        html.Div(
            "First download of a float takes 1–3 minutes (HTTPS from the "
            "GDAC). Floats already in the S3 cache load in seconds.",
            style={"marginTop": "10px", "fontSize": "11px",
                   "color": "#999", "lineHeight": "1.4"},
        ),

        html.Hr(style={"margin": "16px 0 8px 0"}),
        html.Div("Data: GDAC / IFREMER",
                 style={"fontSize": "11px", "color": "#888"}),
        html.Div("Format: Argo v3.1 NetCDF",
                 style={"fontSize": "11px", "color": "#888"}),
    ], style=SIDEBAR_STYLE),

    # ── Main content ──────────────────────────────────────────────────────────
    html.Div([
        html.H1(id="page-title", children="Argo Float Monitor",
                style={"marginTop": "0", "fontSize": "26px"}),

        # Top metric row (6 cards), populated by callback.
        html.Div(id="metric-row",
                 style={"display": "flex", "flexWrap": "wrap",
                        "border": "1px solid #e5e5e5",
                        "borderRadius": "6px",
                        "padding": "4px 0",
                        "marginBottom": "16px"}),

        dcc.Tabs(
            id="main-tabs", value="tab-main",
            children=[
                dcc.Tab(label="Main Information", value="tab-main"),
                dcc.Tab(label="Float Metadata",  value="tab-meta"),
                dcc.Tab(label="Float Health",    value="tab-health"),
                dcc.Tab(label="Profiles",        value="tab-profiles"),
                dcc.Tab(label="QC",              value="tab-qc"),
                dcc.Tab(label="Data Delivery",   value="tab-delivery"),
                dcc.Tab(label="Trajectory data", value="tab-traj"),
            ],
        ),

        html.Div(id="tab-content", style={"marginTop": "16px"}),
    ], style=CONTENT_STYLE),
])


# ══════════════════════════════════════════════════════════════════════════════
# Callbacks
# ══════════════════════════════════════════════════════════════════════════════
@callback(
    Output("store-wmo", "data"),
    Output("store-data-dir", "data"),
    Output("fetch-status", "children"),
    Input("fetch-button", "n_clicks"),
    State("wmo-input", "value"),
    prevent_initial_call=True,
)
def fetch_files(n_clicks, wmo):
    """Look up float on GDAC, download NetCDF files, update stores."""
    if not wmo:
        return no_update, no_update, "Enter a WMO number."
    wmo = wmo.strip()

    try:
        dac = ah.find_dac_ftp(wmo)
    except Exception as e:
        return no_update, no_update, f"GDAC lookup failed: {e}"

    if dac is None:
        return no_update, no_update, f"Float {wmo} not found on GDAC."

    data_dir = str(CACHE_DIR / wmo)
    try:
        # local → S3 → GDAC, writing new GDAC downloads back to S3
        saved = storage.fetch_float(wmo, dac, data_dir)
    except RuntimeError as e:
        return no_update, no_update, str(e)

    # Drop any stale cached datasets for this WMO so the next read reflects
    # freshly-downloaded files.
    invalidate_dataset_cache(data_dir, wmo)

    n_files = sum(1 for s in saved if "(error" not in s and "(not found)" not in s)
    n_s3    = sum(1 for s in saved if s.endswith("(s3)"))
    src = f"{n_s3} from S3, {n_files - n_s3} from {dac.upper()}/local" if n_s3 else dac.upper()
    msg = f"✓ {wmo} ready ({n_files} files; {src})"
    return wmo, data_dir, msg


@callback(
    Output("page-title", "children"),
    Output("metric-row", "children"),
    Input("store-wmo", "data"),
    Input("store-data-dir", "data"),
)
def update_header(wmo, data_dir):
    """Update title and 6-metric row when a new float is loaded."""
    if not wmo or not data_dir:
        return "Argo Float Monitor", html.Div(
            "Enter a WMO number on the left and click Download to begin.",
            style={"padding": "20px", "color": "#888"},
        )

    datasets = get_datasets(data_dir, wmo)
    prof  = datasets.get("prof")
    sprof = datasets.get("sprof")
    meta  = datasets.get("meta")

    if prof is None:
        return f"Float {wmo}", html.Div(
            f"prof.nc not found for {wmo}.",
            style={"padding": "20px", "color": "#a00"},
        )

    metrics = build_metric_row(meta, prof, sprof)
    return f"Float {wmo} — Monitoring Dashboard", metrics


def build_metric_row(meta, prof, sprof):
    """Top-of-page summary cards: float type, cycles, dates, depths."""
    n_prof = prof.sizes["N_PROF"]
    dates = ah.juld_to_dates(prof["JULD"].values)
    valid_d = [d for d in dates if pd.notna(d)]

    # Float type
    has_bgc = sprof is not None
    designed_depth = (ah.get_config(meta, "CONFIG_ProfilePressure_dbar")
                      if meta is not None else None)
    if designed_depth is not None and designed_depth > 2500:
        float_type = "Deep"
    elif has_bgc:
        float_type = "BGC"
    else:
        float_type = "Core"

    # Actual mean dive depth
    pres = (prof["PRES_ADJUSTED"].values if "PRES_ADJUSTED" in prof
            else prof["PRES"].values)
    pres_clean = np.where(pres > 99990, np.nan, pres)
    max_per_cycle = np.nanmax(pres_clean, axis=1)
    valid_max = max_per_cycle[~np.isnan(max_per_cycle)]
    mean_depth = (f"{float(np.mean(valid_max)):.0f} dbar"
                  if len(valid_max) > 0 else "n/a")
    designed_str = (f"{designed_depth:.0f} dbar"
                    if designed_depth is not None else "n/a")

    first_str = valid_d[0].strftime("%Y-%m-%d") if valid_d else "—"
    last_str  = valid_d[-1].strftime("%Y-%m-%d") if len(valid_d) > 1 else "—"

    cards = [
        ("Float type",     float_type),
        ("Cycles",         str(n_prof)),
        ("First cycle",    first_str),
        ("Latest cycle",   last_str),
        ("Designed depth", designed_str),
        ("Actual mean",    mean_depth),
    ]
    return [
        html.Div([
            html.Div(label, style=METRIC_LABEL),
            html.Div(value, style=METRIC_VALUE),
        ], style=METRIC_STYLE)
        for label, value in cards
    ]


@callback(
    Output("tab-content", "children"),
    Input("main-tabs", "value"),
    Input("store-wmo", "data"),
    Input("store-data-dir", "data"),
)
def render_active_tab(active_tab, wmo, data_dir):
    """Render the body of the currently selected tab."""
    if not wmo or not data_dir:
        return html.Div(
            "Once a float is loaded, tab content will appear here.",
            style={"padding": "30px", "color": "#888"},
        )

    datasets = get_datasets(data_dir, wmo)
    meta  = datasets.get("meta")
    prof  = datasets.get("prof")
    sprof = datasets.get("sprof")
    tech  = datasets.get("tech")
    dtraj = datasets.get("dtraj")
    rtraj = datasets.get("rtraj")

    if active_tab == "tab-main":
        return build_tab_main(meta, prof, sprof, wmo)
    if active_tab == "tab-meta":
        return build_tab_metadata(meta, prof, sprof, wmo)
    if active_tab == "tab-health":
        return build_tab_health(tech, wmo)
    if active_tab == "tab-profiles":
        return build_tab_profiles(prof, sprof, wmo)
    if active_tab == "tab-qc":
        return build_tab_qc(prof, sprof, tech, wmo)
    if active_tab == "tab-delivery":
        return build_tab_delivery(prof, sprof, dtraj, rtraj, wmo)
    if active_tab == "tab-traj":
        return build_tab_traj(rtraj, dtraj, wmo)

    # Fallback for an unknown tab id (shouldn't happen — kept defensively)
    return html.Div(
        f"Unknown tab: {active_tab}",
        style={"padding": "20px", "color": "#a00"},
    )


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 8050)))
