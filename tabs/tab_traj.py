"""
tabs/tab_traj.py — Trajectory tab for Argo Float Monitor (Dash port).

Mirrors the original Streamlit tab_bgc layout. Three logical pieces:

    Intro + measurement-code reference (expander)
        - Brief explainer of what trajectory data captures
        - Expander showing the MEASUREMENT_CODE → phase mapping table

    Section 1: Trajectory float track
        - Side-by-side Rtraj | Dtraj scatter maps (plain Cartesian, not
          Scattergeo — single float's track stays small enough that a plain
          scatter renders cleanly and fast).
        - Color = observation time index (Viridis); shape = POSITION_QC
          (circle for good, diamond for flagged); blue/red triangles mark
          start and end.
        - Bullet summary with position counts and bad-QC counts.

    Section 2: Parameters along trajectory (collapsed expander by default)
        - Stacked time-series subplots, one per available parameter
          (PRES, TEMP, PSAL, DOXY, NITRATE, pH, CHLA, BBP700, PPOX_DOXY)
        - Gray markers = raw, black markers = DMQC-adjusted.
        - Trajectory data captures *all* measurements along the float's path
          (descent, park drift, deep-stop bursts at MC=290, ascent, surface),
          not just the upward profile — so the time series here is denser
          than what shows up in prof.nc.

This tab requires Rtraj.nc or Dtraj.nc. Core floats sometimes have neither.
"""
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from dash import dcc, html

import argo_helpers as ah
from tabs.tab_metadata import _expander, CAPTION_STYLE


# ══════════════════════════════════════════════════════════════════════════════
# Constants
# ══════════════════════════════════════════════════════════════════════════════
SECTION_TITLE = {
    "fontSize": "18px", "fontWeight": "600",
    "marginTop": "8px", "marginBottom": "6px", "color": "#222",
}

# Parameters expected to appear in trajectory files (order matters — drives
# subplot stacking order). Format: (variable_name, display_label, units).
ALL_TRAJ_PARAMS = [
    ("PRES",             "Pressure",     "dbar"),
    ("TEMP",             "Temperature",  "°C"),
    ("PSAL",             "Salinity",     "PSU"),
    ("DOXY",             "Oxygen",       "µmol/kg"),
    ("NITRATE",          "Nitrate",      "µmol/kg"),
    ("PH_IN_SITU_TOTAL", "pH",           "total"),
    ("CHLA",             "Chlorophyll",  "mg/m³"),
    ("BBP700",           "Backscatter",  "m⁻¹"),
    ("PPOX_DOXY",        "pO₂",          "mbar"),
]

COLOR_RAW_TRAJ = "#888888"   # gray
COLOR_ADJ_TRAJ = "#000000"   # black


# ══════════════════════════════════════════════════════════════════════════════
# Data extraction helpers
# ══════════════════════════════════════════════════════════════════════════════
def _date_str(j):
    """Single JULD float → 'YYYY-MM-DD' string. Returns '—' on fill."""
    if np.isnan(j) or j > 999990:
        return "—"
    return (ah.JREF + pd.Timedelta(days=float(j))).strftime("%Y-%m-%d")


def _extract_track(traj_ds):
    """Return (lat, lon, juld, qc, mc) sorted by JULD, fill-masked.

    Returns None if traj_ds is None.
    Returns 5-tuple of empty arrays if no valid positions exist.
    """
    if traj_ds is None:
        return None
    lat  = traj_ds["LATITUDE"].values.astype(float)
    lon  = traj_ds["LONGITUDE"].values.astype(float)
    juld = traj_ds["JULD"].values.astype(float)
    qc   = ah.decode_bytes(traj_ds["POSITION_QC"].values)
    mc   = traj_ds["MEASUREMENT_CODE"].values

    # Standard fill masking (Argo uses 99999 / 999990 sentinels)
    lat[lat > 9999]      = np.nan
    lon[lon > 9999]      = np.nan
    juld[juld > 999990]  = np.nan

    valid = ~np.isnan(lat) & ~np.isnan(lon) & ~np.isnan(juld)
    lat, lon, juld, qc, mc = lat[valid], lon[valid], juld[valid], qc[valid], mc[valid]

    order = np.argsort(juld)
    return lat[order], lon[order], juld[order], qc[order], mc[order]


def _extract_traj_param_data(traj_ds, base):
    """Extract raw and adjusted (dates, values) arrays for one parameter
    along the trajectory. Returns dict with 4 keys; entries may be None."""
    juld = traj_ds["JULD"].values.astype(float)
    juld[juld > 999990] = np.nan
    dates = np.array(
        [ah.JREF + pd.Timedelta(days=float(j)) if not np.isnan(j) else pd.NaT
         for j in juld],
        dtype=object,
    )
    valid_date = np.array([d is not pd.NaT and d is not None for d in dates])

    out = {"raw_dates": None, "raw_vals": None,
           "adj_dates": None, "adj_vals": None}

    if base in traj_ds:
        raw = traj_ds[base].values.astype(float)
        raw_valid = (raw < 99999) & valid_date
        if raw_valid.any():
            out["raw_dates"] = dates[raw_valid]
            out["raw_vals"]  = raw[raw_valid]

    adj_var = f"{base}_ADJUSTED"
    if adj_var in traj_ds:
        adj = traj_ds[adj_var].values.astype(float)
        adj_valid = (adj < 99999) & valid_date
        if adj_valid.any():
            out["adj_dates"] = dates[adj_valid]
            out["adj_vals"]  = adj[adj_valid]

    return out


# ══════════════════════════════════════════════════════════════════════════════
# Top intro + measurement-code expander
# ══════════════════════════════════════════════════════════════════════════════
def _intro_block():
    intro = dcc.Markdown(
        "Each cycle, the float takes additional measurements beyond the main "
        "upward profile — at the surface, during descent, while drifting at "
        "park depth, and at maximum profile depth. The trajectory file "
        "(`Rtraj.nc` / `Dtraj.nc`) records these scattered measurements "
        "along the float's actual path through the ocean."
    )

    mc_table = dcc.Markdown(
        "Each cycle records measurements at multiple checkpoints:\n\n"
        "| Code | Phase |\n"
        "|---|---|\n"
        "| 100  | Descent start (leaves surface) |\n"
        "| 200  | Reached park depth (~1000 dbar) |\n"
        "| 290  | At max depth — dense sampling for sensor calibration ★ |\n"
        "| 300  | Ascent start |\n"
        "| 500  | Near-surface |\n"
        "| 600  | At surface, transmitting |\n"
        "| 703  | First GPS fix after surfacing |"
    )

    mc_caption = dcc.Markdown(
        "Sometimes at one code, multiple measurements are collected "
        "(especially MC=290 — dense BGC sampling at max depth). For full "
        "code list, see [Argo User's Manual Reference Table 15]"
        "(https://vocab.nerc.ac.uk/collection/R15/current/).",
        style=CAPTION_STYLE,
    )

    return html.Div([
        intro,
        _expander(
            "What is a measurement in trajectory file?",
            html.Div([mc_table, mc_caption]),
        ),
    ])


# ══════════════════════════════════════════════════════════════════════════════
# Section 1: Trajectory float track (Rtraj | Dtraj side-by-side)
# ══════════════════════════════════════════════════════════════════════════════
def _add_track_panel(fig, track, col, n_total_for_color, show_colorbar):
    """Add one float-track panel (good circles + bad diamonds + start/end
    triangles) into the multi-panel subplot."""
    if track is None:
        return
    lat, lon, juld, qc, mc = track
    if len(lat) == 0:
        return

    n = len(lat)
    time_idx = np.arange(n)
    good = qc == "1"
    bad  = ~good

    def _hover(idx):
        return [
            f"{_date_str(juld[i])}<br>"
            f"Lat: {lat[i]:.3f}°  Lon: {lon[i]:.3f}°<br>"
            f"POSITION_QC: {qc[i]}  "
            f"MC: {int(mc[i]) if mc[i] < 99999 else '—'}"
            for i in idx
        ]

    if good.any():
        gi = np.where(good)[0]
        fig.add_trace(go.Scatter(
            x=lon[good], y=lat[good], mode="markers",
            marker=dict(
                size=6, color=time_idx[good], colorscale="Viridis",
                cmin=0, cmax=n - 1,
                showscale=show_colorbar,
                colorbar=dict(title="Obs index<br>(time →)",
                              len=0.7, thickness=12,
                              x=1.02, xanchor="left"),
                symbol="circle",
                line=dict(width=0.5, color="white"),
            ),
            text=_hover(gi),
            hovertemplate="%{text}<extra></extra>",
            showlegend=False,
        ), row=1, col=col)

    if bad.any():
        bi = np.where(bad)[0]
        fig.add_trace(go.Scatter(
            x=lon[bad], y=lat[bad], mode="markers",
            marker=dict(
                size=8, color=time_idx[bad], colorscale="Viridis",
                cmin=0, cmax=n - 1, showscale=False,
                symbol="diamond",
                line=dict(width=1, color="black"),
            ),
            text=_hover(bi),
            hovertemplate="%{text}<extra></extra>",
            showlegend=False,
        ), row=1, col=col)

    # Start (blue) / End (red) triangles
    fig.add_trace(go.Scatter(
        x=[lon[0]], y=[lat[0]], mode="markers+text",
        marker=dict(size=14, symbol="triangle-up", color="blue"),
        text=["Start"], textposition="top right",
        textfont=dict(size=10),
        showlegend=False, hoverinfo="skip",
    ), row=1, col=col)
    fig.add_trace(go.Scatter(
        x=[lon[-1]], y=[lat[-1]], mode="markers+text",
        marker=dict(size=14, symbol="triangle-down", color="red"),
        text=["End"], textposition="bottom right",
        textfont=dict(size=10),
        showlegend=False, hoverinfo="skip",
    ), row=1, col=col)


def _section_track_maps(rtraj, dtraj, wmo):
    rtrack = _extract_track(rtraj)
    dtrack = _extract_track(dtraj)

    title = html.Div("Trajectory float track", style=SECTION_TITLE)

    # Both unavailable / both empty → nothing to plot
    rtrack_has = rtrack is not None and len(rtrack[0]) > 0
    dtrack_has = dtrack is not None and len(dtrack[0]) > 0
    if not rtrack_has and not dtrack_has:
        return html.Div([
            title,
            html.Div("Neither Rtraj nor Dtraj provided position fixes.",
                     style={"color": "#888", "fontStyle": "italic",
                            "marginTop": "10px"}),
        ])

    # Build subplot
    n_panels = int(rtrack_has) + int(dtrack_has)
    titles = []
    if rtrack_has:
        titles.append(f"Rtraj  ({len(rtrack[0])} positions)")
    if dtrack_has:
        titles.append(f"Dtraj  ({len(dtrack[0])} positions)")

    fig = make_subplots(
        rows=1, cols=n_panels,
        subplot_titles=titles,
        horizontal_spacing=0.10,
    )
    col_i = 1
    if rtrack_has:
        # Show colorbar on Rtraj panel ONLY if there's no Dtraj panel to
        # claim it (we want the colorbar on the rightmost panel)
        _add_track_panel(fig, rtrack, col_i,
                         n_total_for_color=len(rtrack[0]),
                         show_colorbar=(not dtrack_has))
        col_i += 1
    if dtrack_has:
        _add_track_panel(fig, dtrack, col_i,
                         n_total_for_color=len(dtrack[0]),
                         show_colorbar=True)

    fig.add_annotation(
        xref="paper", yref="paper", x=0.5, y=-0.16,
        text=("● circle = good (POSITION_QC=1)   |   "
              "◆ diamond = flagged (POSITION_QC≠1)"),
        showarrow=False, font=dict(size=11),
    )
    fig.update_xaxes(title_text="Longitude")
    fig.update_yaxes(title_text="Latitude", row=1, col=1)
    fig.update_layout(
        title=f"Float {wmo} — trajectory positions",
        height=520,
        margin=dict(t=80, b=80, r=120),
    )

    # Bullet description below the figure
    bullet_lines = []
    if rtrack_has:
        n_r_bad = int(np.sum(rtrack[3] != "1"))
        bullet_lines.append(
            f"- **Rtraj** (real-time): {len(rtrack[0])} positions; "
            f"{n_r_bad} with POSITION_QC ≠ 1"
        )
    if dtrack_has:
        n_d_bad = int(np.sum(dtrack[3] != "1"))
        bullet_lines.append(
            f"- **Dtraj** (DMQC-processed): {len(dtrack[0])} positions; "
            f"{n_d_bad} with POSITION_QC ≠ 1"
        )

    return html.Div([
        title,
        dcc.Graph(figure=fig, config={"responsive": True}),
        dcc.Markdown("\n".join(bullet_lines)) if bullet_lines else html.Div(),
    ])


# ══════════════════════════════════════════════════════════════════════════════
# Section 2: Parameters along trajectory (stacked time series)
# ══════════════════════════════════════════════════════════════════════════════
def _section_traj_parameters(traj_use, traj_label, wmo):
    """Stacked time-series subplots for every standard parameter present in
    the chosen trajectory dataset. Wrapped in a collapsed expander to keep
    the tab visually clean."""
    avail = [(b, l, u) for b, l, u in ALL_TRAJ_PARAMS if b in traj_use]

    if not avail:
        body = html.Div(
            f"No standard parameters found in {traj_label}.",
            style={"color": "#888", "fontStyle": "italic"},
        )
        return _expander("Parameters along trajectory", body)

    fig = make_subplots(
        rows=len(avail), cols=1,
        subplot_titles=[f"{label} ({unit})" for _, label, unit in avail],
        shared_xaxes=True,
        vertical_spacing=max(0.02, 0.06 / max(len(avail), 1)),
    )

    for row_i, (base, label, unit) in enumerate(avail, start=1):
        data = _extract_traj_param_data(traj_use, base)

        # Raw markers (gray) — only show legend item once (on top subplot)
        if data["raw_vals"] is not None:
            fig.add_trace(go.Scatter(
                x=data["raw_dates"], y=data["raw_vals"],
                mode="markers",
                marker=dict(size=2, color=COLOR_RAW_TRAJ, opacity=0.5),
                name="raw",
                showlegend=(row_i == 1),
                legendgroup="raw",
                hovertemplate=(f"{label} raw: %{{y:.3f}} {unit}<br>"
                               "Date: %{x|%Y-%m-%d}<extra></extra>"),
            ), row=row_i, col=1)

        # Adjusted markers (black)
        if data["adj_vals"] is not None:
            fig.add_trace(go.Scatter(
                x=data["adj_dates"], y=data["adj_vals"],
                mode="markers",
                marker=dict(size=2.5, color=COLOR_ADJ_TRAJ, opacity=0.85),
                name="adjusted",
                showlegend=(row_i == 1),
                legendgroup="adj",
                hovertemplate=(f"{label} adj: %{{y:.3f}} {unit}<br>"
                               "Date: %{x|%Y-%m-%d}<extra></extra>"),
            ), row=row_i, col=1)

        fig.update_yaxes(title_text=unit, row=row_i, col=1)

    fig.update_xaxes(title_text="Date", row=len(avail), col=1)
    fig.update_layout(
        title=(f"Float {wmo} — parameters along trajectory  ({traj_label})  "
               "<span style='font-size:11px;color:#666'>"
               "gray = raw   |   black = adjusted (DMQC)</span>"),
        height=max(280, 180 * len(avail)),
        showlegend=True,
        legend=dict(orientation="h", y=-0.02),
        margin=dict(t=70, b=80),
    )

    return _expander(
        "Parameters along trajectory",
        dcc.Graph(figure=fig, config={"responsive": True}),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Tab assembly
# ══════════════════════════════════════════════════════════════════════════════
def build_tab_traj(rtraj, dtraj, wmo):
    """Build the Trajectory tab body.

    Args:
        rtraj: xr.Dataset from <wmo>_Rtraj.nc, or None.
        dtraj: xr.Dataset from <wmo>_Dtraj.nc, or None.
        wmo:   WMO number (used in figure titles).
    """
    traj_any = dtraj if dtraj is not None else rtraj

    if traj_any is None:
        return html.Div([
            dcc.Markdown(
                "**No trajectory file found.**\n\n"
                "This tab requires `Rtraj.nc` (real-time trajectory) or "
                "`Dtraj.nc` (DMQC-processed trajectory). Core floats may "
                "have neither if the data centre has not yet generated them."
            ),
        ], style={"padding": "20px"})

    # Pick which dataset to use for parameter time series — prefer Dtraj
    # since DMQC adjustments produce more reliable values
    traj_label = "Dtraj" if dtraj is not None else "Rtraj"
    traj_use   = dtraj   if dtraj is not None else rtraj

    return html.Div([
        # Top intro + measurement-code expander
        _intro_block(),

        html.Hr(style={"margin": "24px 0"}),

        # Section 1: Track maps
        _section_track_maps(rtraj, dtraj, wmo),

        html.Hr(style={"margin": "24px 0"}),

        # Section 2: Parameters along trajectory (collapsed)
        _section_traj_parameters(traj_use, traj_label, wmo),
    ])
