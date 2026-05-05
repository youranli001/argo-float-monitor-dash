"""
tabs/tab_profiles.py — Profiles tab for Argo Float Monitor (Dash port).

Mirrors the original Streamlit tab_profiles layout:
    Section 1: Section plots — one collapsible heatmap per available parameter
               (T, S, plus BGC params if present). Static. Built eagerly.
    Section 2: All-profiles overlay grid — multi-panel grid (T-S + each param)
               with a column-count selector. Re-renders via callback.
    Section 3: Single-cycle profile — choose any cycle from a dropdown; render
               (T / S / T-S) row plus BGC row if available. Re-renders via
               callback.

The two interactive controls are this project's first non-trivial Dash
callbacks. They lazy-import `get_datasets` from app.py to avoid a circular
import at module load time. With `suppress_callback_exceptions=True` set on
the app, it's fine that the referenced component IDs don't exist in the
layout until the user activates this tab.
"""
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from dash import dcc, html, callback, Input, Output

import argo_helpers as ah
from tabs.tab_metadata import _expander, CAPTION_STYLE


# ══════════════════════════════════════════════════════════════════════════════
# Small helpers (mostly inline functions lifted out of the Streamlit body)
# ══════════════════════════════════════════════════════════════════════════════
def _ds_params(prof, sprof):
    """Prefer Sprof (BGC) over prof (Core). Mirrors `DS_PARAMS` in Streamlit."""
    return sprof if sprof is not None else prof


def _params_in_ds(ds):
    """Filter ALL_PARAMS to those actually present in this dataset."""
    if ds is None:
        return []
    return [p for p in ah.ALL_PARAMS if p[0] in ds]


def _valid_mask(x, p):
    if x is None or p is None:
        return None
    return ~np.isnan(x) & ~np.isnan(p)


def _get_cycle_var(ds, name, i):
    """Pull a single cycle's variable, masked for fill values. Returns None
    if the variable isn't in the dataset."""
    if name in ds:
        return ah.mask_fill(ds[name].values[i])
    return None


def _data_mode_arr(ds):
    """Cycle-level DATA_MODE array for the dropdown labels. Length N_PROF."""
    n = ds.sizes["N_PROF"]
    if "DATA_MODE" in ds:
        return ah.decode_bytes(ds["DATA_MODE"].values)
    if "PARAMETER_DATA_MODE" in ds:
        # Use first parameter's mode as a stand-in.
        return ah.decode_bytes(ds["PARAMETER_DATA_MODE"].values[:, 0])
    return np.array(["?"] * n)


# ══════════════════════════════════════════════════════════════════════════════
# Single-cycle figure builders (used by the cycle dropdown callback)
# ══════════════════════════════════════════════════════════════════════════════
def _add_profile_pair(fig, col, x_raw, x_adj, p_raw, p_adj, c_raw, c_adj, label):
    """T or S: light dotted line for raw, solid markers+line for adjusted.

    Streamlit code factors this out as a closure inside the tab body; we
    promote it to module level so both Streamlit and Dash port can stay
    visually identical.
    """
    m_r = _valid_mask(x_raw, p_raw)
    if m_r is not None and m_r.any():
        fig.add_trace(go.Scatter(
            x=x_raw[m_r], y=p_raw[m_r], mode="lines",
            line=dict(color=c_raw, width=1.2, dash="dot"),
            name=f"{label} raw",
            hovertemplate=(f"{label} raw<br>x=%{{x:.3f}}<br>"
                           "P=%{y:.0f} dbar<extra></extra>"),
        ), row=1, col=col)
    m_a = _valid_mask(x_adj, p_adj)
    if m_a is not None and m_a.any():
        fig.add_trace(go.Scatter(
            x=x_adj[m_a], y=p_adj[m_a], mode="lines+markers",
            marker=dict(size=3, color=c_adj),
            line=dict(color=c_adj, width=2),
            name=f"{label} adjusted",
            hovertemplate=(f"{label} adjusted<br>x=%{{x:.3f}}<br>"
                           "P=%{y:.0f} dbar<extra></extra>"),
        ), row=1, col=col)


def _mode_for_param(ds, param_name, i_sel, fallback):
    """Per-parameter DATA_MODE for cycle i_sel. Falls back to the
    cycle-level mode if PARAMETER_DATA_MODE isn't available."""
    try:
        modes = ah.get_data_mode_per_param(ds, param_name)
        if modes is not None and len(modes) > i_sel:
            return str(modes[i_sel])
    except Exception:
        pass
    return str(fallback)


def build_ts_figure(ds, i_sel):
    """1×3 row: Temperature profile | Salinity profile | T-S diagram colored
    by pressure. Subplot titles include the per-parameter DATA_MODE."""
    pres_raw = _get_cycle_var(ds, "PRES", i_sel)
    pres_adj_v = _get_cycle_var(ds, "PRES_ADJUSTED", i_sel)
    pres_adj = pres_adj_v if pres_adj_v is not None else pres_raw
    temp_raw = _get_cycle_var(ds, "TEMP", i_sel)
    temp_adj = _get_cycle_var(ds, "TEMP_ADJUSTED", i_sel)
    psal_raw = _get_cycle_var(ds, "PSAL", i_sel)
    psal_adj = _get_cycle_var(ds, "PSAL_ADJUSTED", i_sel)

    dm_arr = _data_mode_arr(ds)
    fallback_mode = dm_arr[i_sel] if i_sel < len(dm_arr) else "?"
    temp_mode = _mode_for_param(ds, "TEMP", i_sel, fallback_mode)
    psal_mode = _mode_for_param(ds, "PSAL", i_sel, fallback_mode)

    fig = make_subplots(
        rows=1, cols=3,
        subplot_titles=[
            f"Temperature (°C)  [{temp_mode}]",
            f"Salinity (PSU)  [{psal_mode}]",
            f"T-S Diagram  [{temp_mode}/{psal_mode}]",
        ],
        horizontal_spacing=0.08,
    )

    _add_profile_pair(fig, 1, temp_raw, temp_adj, pres_raw, pres_adj,
                      "#a8c8e8", ah.C_BLUE, "Temp")
    _add_profile_pair(fig, 2, psal_raw, psal_adj, pres_raw, pres_adj,
                      "#f4b8a0", ah.C_RED, "Sal")

    # T-S diagram in column 3 — points colored by pressure.
    t_plot = temp_adj if temp_adj is not None else temp_raw
    s_plot = psal_adj if psal_adj is not None else psal_raw
    m_ts = _valid_mask(t_plot, s_plot)
    if m_ts is not None and m_ts.any():
        p_col = pres_adj if pres_adj is not None else pres_raw
        fig.add_trace(go.Scatter(
            x=s_plot[m_ts], y=t_plot[m_ts],
            mode="markers",
            marker=dict(
                size=5, color=p_col[m_ts],
                colorscale="Blues_r",
                showscale=True,
                colorbar=dict(
                    title="Pressure<br>(dbar)",
                    len=0.7, thickness=12,
                    tickformat=".0f",
                    x=1.02, xanchor="left",
                ),
                cmin=float(np.nanmin(p_col[m_ts])),
                cmax=float(np.nanmax(p_col[m_ts])),
            ),
            showlegend=False,
            hovertemplate=("S=%{x:.3f}  T=%{y:.2f}°C  "
                           "P=%{marker.color:.0f} dbar<extra></extra>"),
        ), row=1, col=3)
        fig.update_xaxes(title_text="Salinity (PSU)", row=1, col=3)
        fig.update_yaxes(title_text="Temperature (°C)", row=1, col=3)

    fig.update_yaxes(autorange="reversed", title_text="Pressure (dbar)", col=1)
    fig.update_yaxes(autorange="reversed", title_text="Pressure (dbar)", col=2)
    fig.update_xaxes(title_text="Temperature (°C)", row=1, col=1)
    fig.update_xaxes(title_text="Salinity (PSU)", row=1, col=2)
    fig.update_layout(
        height=460,
        showlegend=True,
        legend=dict(orientation="h", y=-0.18, font=dict(size=11)),
        margin=dict(b=80, t=70, r=120),
    )
    return fig


def build_bgc_figure(ds, i_sel):
    """1×N row of BGC parameter profiles for one cycle. Returns None if no
    BGC parameters are present (Core floats)."""
    bgc_spec_order = ["DOXY", "CHLA", "NITRATE", "PH_IN_SITU_TOTAL", "BBP700"]
    bgc_spec = []
    for base in bgc_spec_order:
        u = ah.BGC_UNITS.get(base, "")
        label = ah.BGC_LABELS.get(base, base)
        u_str = f"{label} ({u})" if u else label
        bgc_spec.append((f"{base}_ADJUSTED", u_str, ah.BGC_COLORS[base]))

    # Resolve to actual variable name (prefer adjusted, fall back to raw)
    resolved = []
    for v, u, c in bgc_spec:
        if v in ds:
            resolved.append((v, u, c))
        elif v.replace("_ADJUSTED", "") in ds:
            resolved.append((v.replace("_ADJUSTED", ""), u, c))

    if not resolved:
        return None

    pres_adj_v = _get_cycle_var(ds, "PRES_ADJUSTED", i_sel)
    pres_raw   = _get_cycle_var(ds, "PRES", i_sel)
    pres_adj   = pres_adj_v if pres_adj_v is not None else pres_raw

    dm_arr = _data_mode_arr(ds)
    fallback_mode = dm_arr[i_sel] if i_sel < len(dm_arr) else "?"

    titles = []
    for actual_var, u, _ in resolved:
        base_name = actual_var.replace("_ADJUSTED", "")
        titles.append(f"{u}  [{_mode_for_param(ds, base_name, i_sel, fallback_mode)}]")

    fig = make_subplots(
        rows=1, cols=len(resolved),
        subplot_titles=titles,
        shared_yaxes=True,
        horizontal_spacing=0.05,
    )

    for col_j, (var, unit, color) in enumerate(resolved, start=1):
        vals = _get_cycle_var(ds, var, i_sel)
        m = _valid_mask(vals, pres_adj)
        if m is not None and m.any():
            fig.add_trace(go.Scatter(
                x=vals[m], y=pres_adj[m],
                mode="lines+markers",
                marker=dict(size=3, color=color),
                line=dict(color=color, width=1.5),
                name=var, showlegend=False,
            ), row=1, col=col_j)
        fig.update_xaxes(title_text=unit, row=1, col=col_j,
                         title_font=dict(size=10))

    fig.update_yaxes(autorange="reversed",
                     title_text="Pressure (dbar)", col=1)
    fig.update_layout(
        height=400,
        showlegend=False,
        margin=dict(b=60, t=50),
    )
    return fig


# ══════════════════════════════════════════════════════════════════════════════
# Section 1: Section plots (per-parameter heatmaps, collapsible)
# ══════════════════════════════════════════════════════════════════════════════
def _section_plots(ds_params, params):
    """Build one collapsible expander per available parameter."""
    if not params:
        return html.Div(
            "No supported parameters found in this file.",
            style={"color": "#888", "fontStyle": "italic"},
        )

    children = []
    for name, label, units, cmap, cstep in params:
        unit_part = f" ({units})" if units else ""
        fig = ah.make_section_plot(ds_params, name, label, units, cmap, cstep)
        if fig is None:
            body = html.Div(
                f"No data available for {name}.",
                style={"color": "#888", "fontStyle": "italic"},
            )
        else:
            body = dcc.Graph(figure=fig, config={"responsive": True})
        children.append(_expander(f"{label}{unit_part}", body))

    return html.Div(children)


# ══════════════════════════════════════════════════════════════════════════════
# Section 2 & 3: Static skeleton (callbacks fill in the figures)
# ══════════════════════════════════════════════════════════════════════════════
# IDs must be unique across the whole app. Prefix with "profiles-" for clarity.
ID_OVERLAY_NCOLS  = "profiles-overlay-ncols"
ID_OVERLAY_GRAPH  = "profiles-overlay-graph"

ID_CYCLE_SELECT   = "profiles-cycle-select"
ID_CYCLE_LABEL    = "profiles-cycle-label"
ID_TS_GRAPH       = "profiles-ts-graph"
ID_BGC_GRAPH      = "profiles-bgc-graph"


def _overlay_section():
    return html.Div([
        html.Div([
            html.Span("Layout (columns):",
                      style={"fontSize": "13px", "marginRight": "10px"}),
            dcc.Dropdown(
                id=ID_OVERLAY_NCOLS,
                options=[{"label": str(n), "value": n} for n in (2, 3, 4)],
                value=3,
                clearable=False,
                style={"width": "120px", "display": "inline-block",
                       "verticalAlign": "middle"},
            ),
        ], style={"display": "flex", "alignItems": "center",
                  "marginBottom": "12px"}),
        dcc.Loading(
            id="profiles-overlay-loading",
            type="default", color="#0077B6",
            children=dcc.Graph(
                id=ID_OVERLAY_GRAPH,
                config={"responsive": True},
                # Empty placeholder — populated by callback once stores have data.
                figure=go.Figure(),
            ),
        ),
    ])


def _single_cycle_section(ds_params):
    """Build the cycle dropdown + two empty graphs. Options are populated
    here (statically, from the dataset); figures come from the callback."""
    cycles = ds_params["CYCLE_NUMBER"].values.astype(int)
    dates = ah.juld_to_dates(ds_params["JULD"].values)
    dates_str = [str(d)[:10] if pd.notna(d) else "—" for d in dates]
    options = [
        {"label": f"Cycle {int(c):3d}   {ds}", "value": int(c)}
        for c, ds in zip(cycles, dates_str)
    ]
    default_cycle = int(cycles[0]) if len(cycles) else None

    return html.Div([
        html.Div([
            html.Div([
                html.Span("Select cycle:",
                          style={"fontSize": "13px", "marginRight": "10px"}),
                dcc.Dropdown(
                    id=ID_CYCLE_SELECT,
                    options=options, value=default_cycle,
                    clearable=False,
                    style={"width": "260px", "display": "inline-block",
                           "verticalAlign": "middle"},
                ),
            ], style={"display": "flex", "alignItems": "center",
                      "flex": "0 0 auto"}),
            html.Div(
                id=ID_CYCLE_LABEL,
                style={"marginLeft": "20px", "fontSize": "14px",
                       "color": "#444", "alignSelf": "center"},
            ),
        ], style={"display": "flex", "alignItems": "center",
                  "marginBottom": "12px"}),

        dcc.Loading(
            type="default", color="#0077B6",
            children=[
                dcc.Graph(id=ID_TS_GRAPH, config={"responsive": True},
                          figure=go.Figure()),
                dcc.Graph(id=ID_BGC_GRAPH, config={"responsive": True},
                          figure=go.Figure(),
                          # Hidden by default; callback flips display when BGC available
                          style={"display": "none"}),
            ],
        ),
    ])


# ══════════════════════════════════════════════════════════════════════════════
# Callbacks
# Note on lazy import of `get_datasets`: tab_profiles is imported by app.py at
# startup, so importing app at module level here would create a circular
# dependency. Importing inside the function works because by the time these
# callbacks fire, app is fully initialized.
# ══════════════════════════════════════════════════════════════════════════════
@callback(
    Output(ID_OVERLAY_GRAPH, "figure"),
    Input(ID_OVERLAY_NCOLS, "value"),
    Input("store-wmo", "data"),
    Input("store-data-dir", "data"),
)
def _update_overlay(ncols, wmo, data_dir):
    if not wmo or not data_dir or not ncols:
        return go.Figure()
    from app import get_datasets  # avoid circular import at module load
    datasets = get_datasets(data_dir, wmo)
    ds_params = _ds_params(datasets.get("prof"), datasets.get("sprof"))
    if ds_params is None:
        return go.Figure()
    params = _params_in_ds(ds_params)
    if not params:
        return go.Figure()
    return ah.make_overlay_grid(ds_params, params, ncols=int(ncols))


@callback(
    Output(ID_TS_GRAPH, "figure"),
    Output(ID_BGC_GRAPH, "figure"),
    Output(ID_BGC_GRAPH, "style"),
    Output(ID_CYCLE_LABEL, "children"),
    Input(ID_CYCLE_SELECT, "value"),
    Input("store-wmo", "data"),
    Input("store-data-dir", "data"),
)
def _update_single_cycle(sel_cycle, wmo, data_dir):
    empty = go.Figure()
    hidden = {"display": "none"}
    if sel_cycle is None or not wmo or not data_dir:
        return empty, empty, hidden, ""

    from app import get_datasets
    datasets = get_datasets(data_dir, wmo)
    ds_params = _ds_params(datasets.get("prof"), datasets.get("sprof"))
    if ds_params is None:
        return empty, empty, hidden, ""

    cycles = ds_params["CYCLE_NUMBER"].values.astype(int)
    matches = np.where(cycles == int(sel_cycle))[0]
    if len(matches) == 0:
        return empty, empty, hidden, ""
    i_sel = int(matches[0])

    dates = ah.juld_to_dates(ds_params["JULD"].values)
    date_str = (str(dates[i_sel])[:10]
                if pd.notna(dates[i_sel]) else "—")
    label = f"Cycle {int(sel_cycle)}  ·  {date_str}"

    fig_ts  = build_ts_figure(ds_params, i_sel)
    fig_bgc = build_bgc_figure(ds_params, i_sel)

    if fig_bgc is None:
        return fig_ts, empty, hidden, label
    return fig_ts, fig_bgc, {"display": "block"}, label


# ══════════════════════════════════════════════════════════════════════════════
# Tab assembly
# ══════════════════════════════════════════════════════════════════════════════
SECTION_TITLE = {
    "fontSize": "18px", "fontWeight": "600",
    "marginTop": "8px", "marginBottom": "4px", "color": "#222",
}


def build_tab_profiles(prof, sprof, wmo):
    """Build the Profiles tab body."""
    ds_params = _ds_params(prof, sprof)
    if ds_params is None:
        return html.Div(
            "No profile file found (prof.nc / Sprof.nc).",
            style={"color": "#a00", "padding": "30px"},
        )

    params = _params_in_ds(ds_params)

    return html.Div([
        # ── Section 1: Section plots ──────────────────────────────────────────
        html.Div("Section Plots", style=SECTION_TITLE),
        html.Div(
            "Good data only (QC flags 1, 2, 5, 8). "
            "Refer to QC tab for detailed QC flags.",
            style=CAPTION_STYLE,
        ),
        dcc.Loading(
            type="default", color="#0077B6",
            children=_section_plots(ds_params, params),
        ),

        html.Hr(style={"margin": "24px 0"}),

        # ── Section 2: All-profiles overlay grid ──────────────────────────────
        html.Div("All Profiles Overlay", style=SECTION_TITLE),
        _overlay_section() if params else html.Div(
            "No parameters available for overlay.",
            style={"color": "#888", "fontStyle": "italic"},
        ),

        html.Hr(style={"margin": "24px 0"}),

        # ── Section 3: Single-cycle profile ───────────────────────────────────
        html.Div("Single-cycle profile", style=SECTION_TITLE),
        _single_cycle_section(ds_params),
    ])
