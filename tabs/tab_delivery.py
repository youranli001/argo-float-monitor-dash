"""
tabs/tab_delivery.py — Data Delivery tab for Argo Float Monitor (Dash port).

Mirrors the original Streamlit tab_delivery layout. Three sections, all
static (no callbacks):

    Section 1: Variables and parameters
        - DataTable of parameters with their P02 vocabulary reference, units,
          first/latest observation dates, and number of profiles with valid
          data. Column headers carry tooltips with Streamlit-style helper text.

    Section 2: Delivery performance — "How long does the data take to be
               delivered?"
        - Reads JULD_ASCENT_END and JULD_TRANSMISSION_START from dtraj.nc
          (or rtraj.nc as fallback).
        - 4 metric cards: cycles plotted, on-time %, median delay, max delay.
          On-time % colored green if ≥90% else red — matches Streamlit's
          delta_color behavior.
        - Scatter plot of delay (hours) vs surface date, with the 12-hour
          Argo real-time target as a horizontal line. Two traces split on
          target: green ≤12h, red >12h.

    Section 3: Delayed-mode eligibility
        - For each parameter, shows how many profiles are mature (>12 months
          old, eligible for DMQC) and how many have DMQC actually done.
        - "DMQC done early" surfaces edge cases where DMQC happened on
          profiles younger than the 12-month maturity threshold.

The 12-month maturity rule and the 12-hour delivery target are Argo program
conventions, not knobs — kept as constants here.
"""
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import dcc, html, dash_table

import argo_helpers as ah
from tabs.tab_metadata import CAPTION_STYLE


# ══════════════════════════════════════════════════════════════════════════════
# Constants & shared styles
# ══════════════════════════════════════════════════════════════════════════════
TARGET_HOURS = 12.0   # Argo real-time delivery target
MATURITY_DAYS = 365   # 12 months — minimum age for DMQC eligibility

SECTION_TITLE = {
    "fontSize": "18px", "fontWeight": "600",
    "marginTop": "8px", "marginBottom": "6px", "color": "#222",
}

# Metric card styles (match the top-of-page metric row in app.py for visual
# consistency)
METRIC_CARD = {
    "padding": "10px 14px", "minWidth": "140px",
    "borderRight": "1px solid #e5e5e5",
}
METRIC_LABEL = {"fontSize": "11px", "color": "#666",
                "textTransform": "uppercase", "letterSpacing": "0.04em"}
METRIC_VALUE_BASE = {"fontSize": "20px", "fontWeight": "600",
                     "marginTop": "2px"}


# ══════════════════════════════════════════════════════════════════════════════
# Shared helpers
# ══════════════════════════════════════════════════════════════════════════════
def _ds_params(prof, sprof):
    return sprof if sprof is not None else prof


def _params_in_ds(ds):
    if ds is None:
        return []
    return [p for p in ah.ALL_PARAMS if p[0] in ds]


def _metric(label, value, value_color=None):
    """Single metric card."""
    value_style = dict(METRIC_VALUE_BASE)
    value_style["color"] = value_color if value_color else "#222"
    return html.Div([
        html.Div(label, style=METRIC_LABEL),
        html.Div(value, style=value_style),
    ], style=METRIC_CARD)


# ══════════════════════════════════════════════════════════════════════════════
# Section 1: Variables and parameters table
# ══════════════════════════════════════════════════════════════════════════════
def _section_variables(ds_params, params):
    if ds_params is None or not params:
        return html.Div(
            "No parameters available — profile file missing.",
            style={"color": "#888", "fontStyle": "italic"},
        )

    dates_v = np.array(ah.juld_to_dates(ds_params["JULD"].values), dtype=object)

    rows = []
    for name, label, units, *_ in params:
        vals = ah.get_best(ds_params, name)
        if vals is None:
            continue
        valid_per_prof = ~np.all(np.isnan(vals), axis=1)
        if valid_per_prof.any():
            first_idx = int(np.argmax(valid_per_prof))
            last_idx = (len(valid_per_prof) - 1
                        - int(np.argmax(valid_per_prof[::-1])))
            first_str = (dates_v[first_idx].strftime("%Y-%m-%d")
                         if dates_v[first_idx] is not pd.NaT else "n/a")
            last_str = (dates_v[last_idx].strftime("%Y-%m-%d")
                        if dates_v[last_idx] is not pd.NaT else "n/a")
        else:
            first_str = last_str = "n/a"

        rows.append({
            "Variable":        label,
            "Argo name":       name,
            "P02 reference":   f"SDN:P02::{name}",
            "Units":           units or "",
            "First obs":       first_str,
            "Latest obs":      last_str,
            "N profiles w/ data": int(valid_per_prof.sum()),
        })

    if not rows:
        return html.Div(
            "No valid parameter data available.",
            style={"color": "#888", "fontStyle": "italic"},
        )

    df = pd.DataFrame(rows)

    # Column-header tooltips (Dash equivalent of Streamlit's column_config help)
    tooltips = {
        "Variable": "Each row is a measured parameter on this float.",
        "Argo name": "Argo's standard variable name (used in NetCDF files).",
        "P02 reference": (
            "SeaDataNet's vocabulary mapping each parameter to a standard "
            "ID. Lets different ocean databases (Argo / GO-SHIP / OceanSITES "
            "/ gliders) reference the same physical measurement using a "
            "common identifier."
        ),
        "Units": "Physical units of the measurement.",
        "First obs": "First cycle date with valid data for this parameter.",
        "Latest obs": "Most recent cycle date with valid data.",
        "N profiles w/ data": (
            "Number of cycles with non-fill values for this parameter."
        ),
    }

    return dash_table.DataTable(
        data=df.to_dict("records"),
        columns=[{"name": c, "id": c} for c in df.columns],
        tooltip_header=tooltips,
        # Tooltip styling — small dark popover on header hover
        tooltip_delay=0,
        tooltip_duration=None,
        style_cell={
            "fontSize": "12px", "padding": "6px",
            "fontFamily": "system-ui, sans-serif",
            "textAlign": "left",
        },
        style_header={
            "fontWeight": "600", "backgroundColor": "#f0f0f0",
            "textDecoration": "underline",
            "textDecorationStyle": "dotted",   # signal that headers are interactive
        },
        style_table={"overflowX": "auto"},
        page_size=15,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Section 2: Delivery performance
# ══════════════════════════════════════════════════════════════════════════════
def _section_delivery(dtraj, rtraj):
    intro = html.Div([
        html.Div("How long does the data take to be delivered?",
                 style=SECTION_TITLE),
        dcc.Markdown(
            "After ascent, each cycle the float surfaces and transmits its "
            "data via Iridium satellite to the ground station, then onward "
            "to the GDAC. Argo's real-time target is to have data available "
            "at the GDAC within 12 hours of surfacing."
        ),
    ])

    # Prefer Dtraj (delayed-mode trajectory) over Rtraj (real-time)
    traj = dtraj if dtraj is not None else rtraj
    if traj is None:
        return html.Div([
            intro,
            html.Div(
                "No trajectory file (Dtraj.nc / Rtraj.nc) — cannot compute "
                "delays.",
                style={"color": "#888", "fontStyle": "italic",
                       "marginTop": "10px"},
            ),
        ])

    # Pull surface ascent end + first transmission timestamps (JULD = days
    # since 1950-01-01). Both are typically populated for Iridium floats.
    asc_end_all = ah.mask_fill(traj["JULD_ASCENT_END"].values)
    tx_start_all = ah.mask_fill(traj["JULD_TRANSMISSION_START"].values)

    valid_t = ~np.isnan(asc_end_all) & ~np.isnan(tx_start_all)
    delay_h_all = (tx_start_all[valid_t] - asc_end_all[valid_t]) * 24.0
    asc_dates_all = np.array([
        ah.JREF + pd.Timedelta(days=float(j))
        for j in asc_end_all[valid_t]
    ])

    # Drop implausible delays (<0 or >120h — almost always a clock/encoding bug)
    plausible = (delay_h_all >= 0) & (delay_h_all < 120)
    delay_h = delay_h_all[plausible]
    asc_dates = asc_dates_all[plausible]

    if len(delay_h) == 0:
        return html.Div([
            intro,
            html.Div(
                "Transmission timing variables (JULD_ASCENT_END, "
                "JULD_TRANSMISSION_START) are not populated. This is common "
                "for older Argos floats.",
                style={"color": "#888", "fontStyle": "italic",
                       "marginTop": "10px"},
            ),
        ])

    on_target  = delay_h <= TARGET_HOURS
    off_target = ~on_target
    n_on  = int(on_target.sum())
    n_off = int(off_target.sum())
    pct_on = n_on / len(delay_h) * 100

    # Color the on-time % green if ≥ 90% else red (matches Streamlit
    # delta_color="normal"/"inverse" semantic)
    on_time_color = ah.C_GRN if pct_on >= 90 else ah.C_RED

    metric_row = html.Div([
        _metric("Cycles plotted", str(len(delay_h))),
        _metric("On-time (≤ 12 h)", f"{pct_on:.0f}%", value_color=on_time_color),
        _metric("Median delay", f"{np.median(delay_h):.1f} h"),
        _metric("Max delay",    f"{np.max(delay_h):.1f} h"),
    ], style={
        "display": "flex", "flexWrap": "wrap",
        "border": "1px solid #e5e5e5", "borderRadius": "6px",
        "padding": "4px 0", "marginTop": "12px", "marginBottom": "16px",
    })

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=asc_dates[on_target], y=delay_h[on_target],
        mode="markers",
        marker=dict(size=7, color=ah.C_GRN, opacity=0.85),
        name=f"On target ≤ {TARGET_HOURS:.0f} h  (n={n_on})",
        hovertemplate="%{x|%Y-%m-%d}<br>%{y:.1f} h<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=asc_dates[off_target], y=delay_h[off_target],
        mode="markers",
        marker=dict(size=8, color=ah.C_RED, opacity=0.9),
        name=f"Off target > {TARGET_HOURS:.0f} h  (n={n_off})",
        hovertemplate="%{x|%Y-%m-%d}<br>%{y:.1f} h<extra></extra>",
    ))
    fig.add_hline(
        y=TARGET_HOURS, line_dash="dash", line_color=ah.C_RED, line_width=1,
        annotation_text=f"{TARGET_HOURS:.0f}-h target",
        annotation_position="top right",
        annotation_font_color=ah.C_RED,
    )
    fig.update_layout(
        title="Transmission delay per cycle",
        xaxis_title="Surface date",
        yaxis_title="Delay (hours)",
        height=440,
        hovermode="closest",
    )

    return html.Div([
        intro,
        metric_row,
        dcc.Graph(figure=fig, config={"responsive": True}),
    ])


# ══════════════════════════════════════════════════════════════════════════════
# Section 3: Delayed-mode eligibility
# ══════════════════════════════════════════════════════════════════════════════
def _section_dm_eligibility(prof, ds_params, params):
    intro = html.Div("Delayed-mode eligibility", style=SECTION_TITLE)

    if ds_params is None or prof is None or not params:
        return html.Div([
            intro,
            html.Div(
                "DM eligibility requires both a profile file and parameter list.",
                style={"color": "#888", "fontStyle": "italic",
                       "marginTop": "10px"},
            ),
        ])

    dates_dm = np.array(ah.juld_to_dates(ds_params["JULD"].values), dtype=object)
    now_dm = datetime.utcnow()
    twelve_mo = timedelta(days=MATURITY_DAYS)

    # A profile is "mature" if its date is older than 12 months. Mature profiles
    # are eligible for DMQC; younger ones are not yet expected to be in D-mode.
    eligible_arr = np.array([
        (d is not pd.NaT) and (d is not None)
        and (now_dm - d.to_pydatetime() > twelve_mo)
        for d in dates_dm
    ])
    n_eligible = int(eligible_arr.sum())

    rows = []
    for name, label, *_ in params:
        modes = ah.get_data_mode_per_param(ds_params, name)
        n_total    = len(modes)
        n_dm_total = int((modes == "D").sum())
        n_dm_elig  = int(((modes == "D") & eligible_arr).sum())
        n_dm_early = int(((modes == "D") & ~eligible_arr).sum())
        pct = (n_dm_elig / n_eligible * 100) if n_eligible else 0.0
        rows.append({
            "Parameter":                    name,
            "Profiles":                     n_total,
            "DMQC done":                    n_dm_total,
            "Mature (>12 months old)":      n_eligible,
            "Mature & DMQC done":           n_dm_elig,
            "DMQC coverage":                f"{pct:.1f}%",
            "DMQC done early (<12 months)": n_dm_early,
        })

    df = pd.DataFrame(rows)

    tooltips = {
        "Profiles": "Total number of cycles with this parameter.",
        "DMQC done": (
            "Number of cycles where DATA_MODE = 'D' for this parameter."
        ),
        "Mature (>12 months old)": (
            "Number of cycles older than 12 months. By Argo convention, only "
            "these are eligible for delayed-mode QC."
        ),
        "Mature & DMQC done": (
            "Number of mature cycles that actually have DMQC done."
        ),
        "DMQC coverage": (
            "Percentage of mature cycles that have DMQC done. The headline "
            "metric for fleet-monitoring DMQC progress."
        ),
        "DMQC done early (<12 months)": (
            "Cycles younger than 12 months that already have DMQC done. "
            "Should usually be 0 — surface to surface anomalies."
        ),
    }

    table = dash_table.DataTable(
        data=df.to_dict("records"),
        columns=[{"name": c, "id": c} for c in df.columns],
        tooltip_header=tooltips,
        tooltip_delay=0,
        tooltip_duration=None,
        style_cell={
            "fontSize": "12px", "padding": "6px",
            "fontFamily": "system-ui, sans-serif",
            "textAlign": "left",
        },
        style_header={
            "fontWeight": "600", "backgroundColor": "#f0f0f0",
            "textDecoration": "underline",
            "textDecorationStyle": "dotted",
        },
        style_table={"overflowX": "auto", "marginTop": "10px"},
        page_size=15,
    )

    return html.Div([intro, table])


# ══════════════════════════════════════════════════════════════════════════════
# Tab assembly
# ══════════════════════════════════════════════════════════════════════════════
def build_tab_delivery(prof, sprof, dtraj, rtraj, wmo):
    """Build the Data Delivery tab body."""
    ds_params = _ds_params(prof, sprof)
    params = _params_in_ds(ds_params)

    return html.Div([
        # ── Section 1: Variables and parameters ───────────────────────────────
        html.Div("Variables and parameters", style=SECTION_TITLE),
        _section_variables(ds_params, params),

        html.Hr(style={"margin": "24px 0"}),

        # ── Section 2: Delivery performance ───────────────────────────────────
        _section_delivery(dtraj, rtraj),

        html.Hr(style={"margin": "24px 0"}),

        # ── Section 3: Delayed-mode eligibility ───────────────────────────────
        _section_dm_eligibility(prof, ds_params, params),
    ])
