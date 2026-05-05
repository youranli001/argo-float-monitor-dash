"""
tabs/tab_qc.py — QC & Processing tab for Argo Float Monitor (Dash port).

Mirrors the original Streamlit tab_qc layout. Four sections top-down:

    Section 1: Reference area (static)
        - DATA_MODE code reference (R / A / D)
        - "Data mode of all cycles" — per-parameter-group strips, where
          parameters with identical (R, A, D) signatures collapse into one row
        - QC flag reference (0–9) + Profile-level QC grade reference (A–F),
          side by side

    Section 2: QC of every parameter (static, one collapsible per param)
        - QC section heatmap: QC flag by depth × cycle, with optional
          Profile QC strip and DATA_MODE strip below the heatmap
        - QC overlay scatter: one trace per QC level, colored, with count + %
        - Three vertical legends drawn manually in paper coords
          (QC flag / Profile QC / DATA_MODE)

    Section 3: Scientific calibration records (callback)
        - Cycle dropdown (default = latest)
        - DataTable: per-parameter Equation / Coefficient / Comment for the
          selected cycle

    Section 4: Subsurface velocity quality (callback, uses tech.nc)
        - Reposition-threshold slider (cycles with >= N repositions are flagged)
        - Bar chart with below/above-threshold coloring
        - Diagnosis bullets

Callbacks lazy-import `get_datasets` from app.py to break the circular
dependency at module load time.
"""
from collections import defaultdict

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from scipy.interpolate import interp1d
from dash import dcc, html, dash_table, callback, Input, Output

import argo_helpers as ah
from tabs.tab_metadata import _expander, CAPTION_STYLE


# ══════════════════════════════════════════════════════════════════════════════
# Module-level constants
# ══════════════════════════════════════════════════════════════════════════════

# Profile-level QC grades (A=best → F=worst). Distinct palette from
# the cycle-level QC flags so the eye doesn't conflate them.
PROFILE_QC_COLORS = {
    "A": "#1a9850",  # deep green
    "B": "#91cf60",  # light green
    "C": "#fee08b",  # yellow
    "D": "#fc8d59",  # orange
    "E": "#d73027",  # red
    "F": "#7f0000",  # dark red
    " ": "#dddddd",  # missing
    "":  "#dddddd",
}

# Discrete colorscale for the DATA_MODE strip in Section 1.
# Index: D=0, A=1, R=2, missing=3.
_DM_IDX = {"D": 0, "A": 1, "R": 2}
_DM_COLORS_IDX = ["#0077B6", "#f77f00", "#e63946", "#cccccc"]
_DM_HEATMAP_SCALE = []
for _k, _c in enumerate(_DM_COLORS_IDX):
    _DM_HEATMAP_SCALE.append([_k / 4, _c])
    _DM_HEATMAP_SCALE.append([(_k + 1) / 4, _c])

# CTD core parameters — used for sorting groups (CTD shows up first).
_CTD_CORE = {"PRES", "TEMP", "PSAL"}


# ══════════════════════════════════════════════════════════════════════════════
# Small shared helpers
# ══════════════════════════════════════════════════════════════════════════════
def _ds_params(prof, sprof):
    """Prefer Sprof (BGC) over prof (Core)."""
    return sprof if sprof is not None else prof


def _params_in_ds(ds):
    if ds is None:
        return []
    return [p for p in ah.ALL_PARAMS if p[0] in ds]


def _profile_qc(ds, param):
    """PROFILE_<param>_QC as length-N_PROF char array. Returns array of ' '
    if not present in the dataset."""
    v = f"PROFILE_{param}_QC"
    if v in ds:
        return ah.decode_bytes(ds[v].values)
    return np.array([" "] * ds.sizes["N_PROF"])


def _cycle_tickvals(cycles_arr, n_show=6):
    """Return (tickvals, ticktext) for sparse cycle-number axis labels."""
    if len(cycles_arr) <= 1:
        vals = [int(c) for c in cycles_arr]
        return vals, [str(c) for c in vals]
    n = min(n_show, len(cycles_arr))
    idx = np.linspace(0, len(cycles_arr) - 1, n, dtype=int)
    return ([int(cycles_arr[i]) for i in idx],
            [str(int(cycles_arr[i])) for i in idx])


# ══════════════════════════════════════════════════════════════════════════════
# Section 1.1: DATA_MODE reference (static markdown table)
# ══════════════════════════════════════════════════════════════════════════════
def _data_mode_reference():
    return dcc.Markdown(
        "**DATA_MODE reference**\n\n"
        "| Code | Meaning |\n"
        "|---|---|\n"
        "| R | Real-time only — no adjustment applied |\n"
        "| A | Adjusted — automated correction, not expert-reviewed |\n"
        "| D | Delayed-mode — DMQC complete, use these for science |"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Section 1.2: Data mode of all cycles — one strip per parameter group
# ══════════════════════════════════════════════════════════════════════════════
def _data_mode_strips(ds_params):
    """Build a list of dcc.Graph strips, one per group of parameters that
    share the same (R, A, D) signature."""
    if "STATION_PARAMETERS" in ds_params:
        stat_params = ah.decode_bytes(
            ds_params["STATION_PARAMETERS"].values[0, :])
    else:
        stat_params = np.array([])

    param_counts = {}
    param_modes  = {}
    for p in stat_params:
        if not p:
            continue
        modes_p = ah.get_data_mode_per_param(ds_params, p)
        r = int(np.sum(modes_p == "R"))
        a = int(np.sum(modes_p == "A"))
        d = int(np.sum(modes_p == "D"))
        param_counts[p] = (r, a, d)
        param_modes[p]  = modes_p

    if not param_counts:
        return [html.Div(
            "STATION_PARAMETERS not in dataset — DATA_MODE strips unavailable.",
            style={"color": "#888", "fontStyle": "italic"},
        )]

    # Group parameters by identical (R, A, D) signature
    sig_groups = defaultdict(list)
    for p, sig in param_counts.items():
        sig_groups[sig].append(p)

    # Sort groups: CTD core first, then by D-count desc, then alpha
    def _grp_key(item):
        sig, ps = item
        r, a, d = sig
        is_core = bool(_CTD_CORE & set(ps))
        return (0 if is_core else 1, -d, ",".join(sorted(ps)))

    groups_sorted = sorted(sig_groups.items(), key=_grp_key)

    cycles_qc = ds_params["CYCLE_NUMBER"].values.astype(int)
    tick_vals, tick_text = _cycle_tickvals(cycles_qc, n_show=6)

    strips = [dcc.Markdown("**Data mode of all cycles**")]
    n_groups = len(groups_sorted)

    for i_grp, (sig, ps) in enumerate(groups_sorted):
        r, a, d = sig
        counts_label = f"D={d}, A={a}, R={r}"
        params_label = ", ".join(sorted(ps))

        # All params in group share the same per-cycle modes — use the first.
        mode_arr = param_modes[ps[0]]
        z_row = [[_DM_IDX.get(m, 3) for m in mode_arr]]  # 1 × N

        hover_strip = [f"Cycle {c} — DATA_MODE={m}"
                       for c, m in zip(cycles_qc, mode_arr)]

        is_last = (i_grp == n_groups - 1)

        fig = go.Figure(go.Heatmap(
            x=cycles_qc, y=[0], z=z_row,
            colorscale=_DM_HEATMAP_SCALE,
            zmin=-0.5, zmax=3.5,
            showscale=False,
            customdata=[hover_strip],
            hovertemplate="%{customdata}<extra></extra>",
            xgap=0, ygap=0,
        ))
        fig.update_layout(
            height=85 if is_last else 70,
            margin=dict(t=32, b=(28 if is_last else 6), l=10, r=10),
            xaxis=dict(
                title=("Cycle number" if is_last else None),
                showticklabels=is_last,
                tickmode="array" if is_last else "auto",
                tickvals=(tick_vals if is_last else None),
                ticktext=(tick_text if is_last else None),
            ),
            yaxis=dict(visible=False, range=[-0.5, 0.5]),
            title=dict(
                text=f"{params_label}  —  {counts_label}",
                font=dict(size=14),
                x=0.0, xanchor="left",
            ),
        )
        strips.append(dcc.Graph(figure=fig, config={"responsive": True}))

    return strips


# ══════════════════════════════════════════════════════════════════════════════
# Section 1.3: QC flag reference + Profile-QC grade reference (side by side)
# ══════════════════════════════════════════════════════════════════════════════
def _qc_grade_reference():
    return html.Div([
        html.Div(
            dcc.Markdown(
                "**QC flag reference**\n\n"
                "| Code | Meaning |\n"
                "|---|---|\n"
                "| 0 | No QC performed |\n"
                "| 1 | Good data |\n"
                "| 2 | Probably good |\n"
                "| 3 | Probably bad — correctable |\n"
                "| 4 | Bad — uncorrectable |\n"
                "| 5 | Value changed (DMQC) |\n"
                "| 8 | Estimated (interpolated / extrapolated) |\n"
                "| 9 | Missing value |"
            ),
        ),
        html.Div(
            dcc.Markdown(
                "**Profile-level QC grade**  (per parameter, per cycle)\n\n"
                "| Grade | Meaning |\n"
                "|---|---|\n"
                "| A | 100% of profile data is good (QC1) |\n"
                "| B | ≥75% good |\n"
                "| C | ≥50% good |\n"
                "| D | ≥25% good |\n"
                "| E | <25% good |\n"
                "| F | None of the data is good |"
            ),
        ),
    ], style={
        "display": "grid",
        "gridTemplateColumns": "1fr 1fr",
        "gap": "30px",
    })


# ══════════════════════════════════════════════════════════════════════════════
# Section 2: Per-parameter QC (static figures)
# ══════════════════════════════════════════════════════════════════════════════
def _build_qc_section_figure(ds_params, name, label):
    """QC heatmap (depth × cycle) + Profile-QC strip + DATA_MODE strip,
    plus three manually drawn vertical legends. Returns a Plotly Figure or
    None if required data is missing."""
    qc_arr = ah.get_qc(ds_params, name)
    if qc_arr is None:
        return None

    pres_2d = ah.mask_fill(
        ds_params["PRES_ADJUSTED"].values if "PRES_ADJUSTED" in ds_params
        else ds_params["PRES"].values
    )
    if not np.isfinite(np.nanmax(pres_2d)):
        return None

    pres_max = float(np.nanmax(pres_2d))
    pres_grid = np.arange(0, np.ceil(pres_max / 10) * 10 + 1, 5.0)

    # Discrete QC colorscale
    n_qc = len(ah.QC_LEVELS)
    discrete_scale = []
    for k, q in enumerate(ah.QC_LEVELS):
        color = ah.QC_COLOR_MAP[q]
        discrete_scale.append([k / n_qc,       color])
        discrete_scale.append([(k + 1) / n_qc, color])
    code_to_idx = {q: k for k, q in enumerate(ah.QC_LEVELS)}

    # Interpolate per-cycle QC onto pressure grid (nearest-neighbor)
    grid_q = np.full((qc_arr.shape[0], len(pres_grid)), 9, dtype=np.int8)
    for i in range(qc_arr.shape[0]):
        p = pres_2d[i]
        m = ~np.isnan(p)
        if m.sum() < 2:
            continue
        pv, qv = p[m], qc_arr[i][m]
        order = np.argsort(pv)
        pv, qv = pv[order], qv[order]
        _, ui = np.unique(pv, return_index=True)
        pv, qv = pv[ui], qv[ui]
        f_intp = interp1d(pv, qv, kind="nearest",
                          bounds_error=False, fill_value=9)
        grid_q[i, :] = f_intp(pres_grid).astype(np.int8)
    grid_idx = np.vectorize(
        lambda q: code_to_idx.get(int(q), n_qc - 1))(grid_q)

    # Per-cycle modes & profile QC
    modes      = ah.get_data_mode_per_param(ds_params, name)
    profile_qc = _profile_qc(ds_params, name)
    has_profile_qc = bool((profile_qc != " ").any() and (profile_qc != "").any())

    dates_q  = ah.juld_to_dates(ds_params["JULD"].values)
    cycles_q = ds_params["CYCLE_NUMBER"].values.astype(int)

    if has_profile_qc:
        fig = make_subplots(
            rows=3, cols=1,
            shared_xaxes=True,
            row_heights=[0.84, 0.08, 0.08],
            vertical_spacing=0.04,
        )
    else:
        fig = make_subplots(
            rows=2, cols=1,
            shared_xaxes=True,
            row_heights=[0.92, 0.08],
            vertical_spacing=0.04,
        )

    # Heatmap (row 1)
    fig.add_trace(go.Heatmap(
        x=dates_q, y=pres_grid, z=grid_idx.T,
        colorscale=discrete_scale,
        zmin=-0.5, zmax=n_qc - 0.5,
        showscale=False,
        hovertemplate=("Date: %{x|%Y-%m-%d}<br>"
                       "Pressure: %{y:.0f} dbar<br>"
                       "QC bin: %{z}<extra></extra>"),
        zsmooth=False,
    ), row=1, col=1)

    # Profile QC strip (row 2 if present)
    if has_profile_qc:
        pqc_colors = [PROFILE_QC_COLORS.get(g, "#dddddd") for g in profile_qc]
        pqc_text   = [f"Cycle {c} — Profile {name}_QC: {g}"
                      for c, g in zip(cycles_q, profile_qc)]
        fig.add_trace(go.Scatter(
            x=dates_q, y=[0] * len(dates_q),
            mode="markers",
            marker=dict(color=pqc_colors, size=14, symbol="square"),
            text=pqc_text,
            hovertemplate="%{text}<extra></extra>",
            showlegend=False,
        ), row=2, col=1)

    # DATA_MODE strip (last row)
    dm_row = 3 if has_profile_qc else 2
    mode_colors = [ah.DM_COLORS.get(m, "#cccccc") for m in modes]
    mode_text   = [f"Cycle {c} — DATA_MODE: {m}"
                   for c, m in zip(cycles_q, modes)]
    fig.add_trace(go.Scatter(
        x=dates_q, y=[0] * len(dates_q),
        mode="markers",
        marker=dict(color=mode_colors, size=14, symbol="square"),
        text=mode_text,
        hovertemplate="%{text}<extra></extra>",
        showlegend=False,
    ), row=dm_row, col=1)

    # Axes
    fig.update_yaxes(title_text="Pressure (dbar)",
                     autorange="reversed", row=1, col=1)
    if has_profile_qc:
        fig.update_yaxes(visible=False, range=[-0.5, 0.5], row=2, col=1)
        fig.update_yaxes(visible=False, range=[-0.5, 0.5], row=3, col=1)
    else:
        fig.update_yaxes(visible=False, range=[-0.5, 0.5], row=2, col=1)

    # Cycle-number top axis on row 1
    n_show = min(6, len(cycles_q))
    if n_show > 1:
        tick_idx = np.linspace(0, len(cycles_q) - 1, n_show, dtype=int)
        fig.update_xaxes(
            side="top",
            tickmode="array",
            tickvals=[dates_q[i] for i in tick_idx],
            ticktext=[str(cycles_q[i]) for i in tick_idx],
            title_text="Cycle number",
            row=1, col=1,
        )
    fig.update_xaxes(title_text="Date", row=dm_row, col=1)

    # Strip annotations (left labels)
    if has_profile_qc:
        fig.add_annotation(
            x=-0.01, y=0,
            xref="paper", yref="y2",
            text="profile QC",
            showarrow=False,
            xanchor="right", yanchor="middle",
            font=dict(size=10),
        )
    fig.add_annotation(
        x=-0.01, y=0,
        xref="paper", yref=f"y{dm_row}",
        text="DATA_MODE",
        showarrow=False,
        xanchor="right", yanchor="middle",
        font=dict(size=10),
    )

    # Three manually drawn vertical legends
    LEGEND_X = 1.015
    BLOCK_W  = 0.025

    ah.add_colorbar_legend(
        fig,
        items=[(f"QC{q}", ah.QC_COLOR_MAP[q]) for q in ah.QC_LEVELS],
        x_left=LEGEND_X, y_top=0.97, total_height=0.32,
        title="<b>QC flag</b>", block_width=BLOCK_W,
    )
    if has_profile_qc:
        ah.add_colorbar_legend(
            fig,
            items=[(g, PROFILE_QC_COLORS[g])
                   for g in ["A", "B", "C", "D", "E", "F"]],
            x_left=LEGEND_X, y_top=0.55, total_height=0.24,
            title="<b>Profile QC</b>", block_width=BLOCK_W,
        )
    ah.add_colorbar_legend(
        fig,
        items=[(m, ah.DM_COLORS[m]) for m in ["D", "A", "R"]],
        x_left=LEGEND_X, y_top=0.20, total_height=0.12,
        title="<b>DATA_MODE</b>", block_width=BLOCK_W,
    )

    fig.update_layout(
        height=600 if has_profile_qc else 540,
        margin=dict(t=70, b=50, l=100, r=140),
    )
    return fig


def _build_qc_overlay_figure(ds_params, name, label, units):
    """All-cycle QC overlay scatter: pressure on Y, parameter value on X,
    one trace per QC level (with count + percentage in legend)."""
    qc_arr = ah.get_qc(ds_params, name)
    vals   = ah.get_best(ds_params, name)
    if qc_arr is None or vals is None:
        return None

    pres_2d = ah.mask_fill(
        ds_params["PRES_ADJUSTED"].values if "PRES_ADJUSTED" in ds_params
        else ds_params["PRES"].values
    )

    valid_total = int(((qc_arr != 9)
                       & ~np.isnan(vals)
                       & ~np.isnan(pres_2d)).sum())
    if valid_total == 0:
        valid_total = 1   # avoid div-by-zero in label

    fig = go.Figure()
    for q in ah.QC_LEVELS:
        m = ((qc_arr == q) & ~np.isnan(vals) & ~np.isnan(pres_2d))
        n_q = int(m.sum())
        pct = n_q / valid_total * 100
        if n_q > 0:
            fig.add_trace(go.Scattergl(
                x=vals[m], y=pres_2d[m],
                mode="markers",
                marker=dict(size=3, color=ah.QC_COLOR_MAP[q], opacity=0.65),
                name=f"QC{q}  (n={n_q:,}, {pct:.1f}%)",
                hovertemplate=(f"QC{q}<br>value=%{{x:.4g}}<br>"
                               "P=%{y:.0f} dbar<extra></extra>"),
            ))
        else:
            # Show empty levels in legend so the legend layout is stable
            fig.add_trace(go.Scatter(
                x=[None], y=[None],
                mode="markers",
                marker=dict(size=3, color=ah.QC_COLOR_MAP[q], opacity=0.4),
                name=f"QC{q}  (n=0, 0.0%)",
                showlegend=True,
                hoverinfo="skip",
            ))

    xlabel = f"{label} ({units})" if units else label
    fig.update_yaxes(autorange="reversed", title_text="Pressure (dbar)")
    fig.update_xaxes(title_text=xlabel)
    fig.update_layout(
        height=520,
        legend=dict(itemsizing="constant"),
        margin=dict(t=30, l=70, r=20, b=60),
    )
    return fig


def _qc_per_param_section(ds_params, params):
    """One collapsible expander per parameter, each containing QC heatmap +
    QC overlay scatter."""
    if not params:
        return html.Div(
            "No parameters to display QC for.",
            style={"color": "#888", "fontStyle": "italic"},
        )

    children = []
    for name, label, units, _, _ in params:
        unit_part = f" ({units})" if units else ""

        body = []

        # 1. QC section heatmap
        body.append(dcc.Markdown(
            f"**{label} — QC flags per depth & cycle**"
        ))
        fig_sec = _build_qc_section_figure(ds_params, name, label)
        if fig_sec is None:
            body.append(html.Div(
                f"No QC array available for {name}.",
                style={"color": "#888", "fontStyle": "italic"},
            ))
        else:
            body.append(dcc.Graph(figure=fig_sec, config={"responsive": True}))

        # 2. QC overlay scatter
        body.append(dcc.Markdown(
            f"**{label} — overlaid profiles colored by QC**"
        ))
        fig_ov = _build_qc_overlay_figure(ds_params, name, label, units)
        if fig_ov is None:
            body.append(html.Div(
                f"No values array for {name}.",
                style={"color": "#888", "fontStyle": "italic"},
            ))
        else:
            body.append(dcc.Graph(figure=fig_ov, config={"responsive": True}))

        children.append(_expander(f"{label}{unit_part}", html.Div(body)))

    return html.Div(children)


# ══════════════════════════════════════════════════════════════════════════════
# Section 3: Scientific calibration records (callback)
# ══════════════════════════════════════════════════════════════════════════════
ID_CALIB_CYCLE = "qc-calib-cycle"
ID_CALIB_TABLE = "qc-calib-table"


def _calibration_section(ds_params):
    """Static skeleton for the calibration table + dropdown."""
    intro = html.Div([
        html.Div("Scientific calibration records",
                 style={"fontSize": "18px", "fontWeight": "600",
                        "marginBottom": "4px"}),
        html.Div(
            "DMQC adjustments applied per cycle, per parameter. These records "
            "are written by the DMQC expert and document exactly what "
            "adjustment was applied. Select a cycle below.",
            style=CAPTION_STYLE,
        ),
    ])

    if "SCIENTIFIC_CALIB_EQUATION" not in ds_params:
        return html.Div([
            intro,
            html.Div(
                "SCIENTIFIC_CALIB_* not present in this profile file.",
                style={"color": "#888", "fontStyle": "italic",
                       "marginTop": "8px"},
            ),
        ])

    cycles = ds_params["CYCLE_NUMBER"].values.astype(int)
    options = [{"label": f"Cycle {int(c)}", "value": int(c)} for c in cycles]
    default_cycle = int(cycles[-1]) if len(cycles) else None

    return html.Div([
        intro,
        html.Div([
            html.Span("Cycle number:",
                      style={"fontSize": "13px", "marginRight": "10px"}),
            dcc.Dropdown(
                id=ID_CALIB_CYCLE,
                options=options, value=default_cycle,
                clearable=False,
                style={"width": "180px", "display": "inline-block",
                       "verticalAlign": "middle"},
            ),
        ], style={"display": "flex", "alignItems": "center",
                  "marginTop": "10px", "marginBottom": "10px"}),
        dcc.Loading(
            type="default", color="#0077B6",
            children=html.Div(id=ID_CALIB_TABLE),
        ),
    ])


@callback(
    Output(ID_CALIB_TABLE, "children"),
    Input(ID_CALIB_CYCLE, "value"),
    Input("store-wmo", "data"),
    Input("store-data-dir", "data"),
)
def _update_calibration_table(sel_cycle, wmo, data_dir):
    if sel_cycle is None or not wmo or not data_dir:
        return ""
    from app import get_datasets
    datasets = get_datasets(data_dir, wmo)
    ds_params = _ds_params(datasets.get("prof"), datasets.get("sprof"))
    if ds_params is None or "SCIENTIFIC_CALIB_EQUATION" not in ds_params:
        return ""

    cycles = ds_params["CYCLE_NUMBER"].values.astype(int)
    matches = np.where(cycles == int(sel_cycle))[0]
    if len(matches) == 0:
        return ""
    i = int(matches[0])

    try:
        params_c = ah.decode_bytes(ds_params["PARAMETER"].values[i, 0, :])
        eq_c     = ah.decode_bytes(
            ds_params["SCIENTIFIC_CALIB_EQUATION"].values[i, 0, :])
        coeff_c  = ah.decode_bytes(
            ds_params["SCIENTIFIC_CALIB_COEFFICIENT"].values[i, 0, :])
        comm_c   = ah.decode_bytes(
            ds_params["SCIENTIFIC_CALIB_COMMENT"].values[i, 0, :])
    except Exception as e:
        return html.Div(f"Could not extract calibration records: {e}",
                        style=CAPTION_STYLE)

    df = pd.DataFrame({
        "Parameter":   params_c,
        "Equation":    eq_c,
        "Coefficient": coeff_c,
        "Comment":     comm_c,
    })
    df = df[df["Parameter"].str.len() > 0].reset_index(drop=True)
    if df.empty:
        return html.Div("No calibration records for this cycle.",
                        style=CAPTION_STYLE)

    return dash_table.DataTable(
        data=df.to_dict("records"),
        columns=[{"name": c, "id": c} for c in df.columns],
        style_cell={
            "fontSize": "12px", "padding": "6px",
            "fontFamily": "system-ui, sans-serif",
            "textAlign": "left",
            "whiteSpace": "normal", "height": "auto",
        },
        style_header={"fontWeight": "600", "backgroundColor": "#f0f0f0"},
        style_table={"overflowX": "auto"},
        page_size=20,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Section 4: Subsurface velocity quality (callback, uses tech.nc)
# ══════════════════════════════════════════════════════════════════════════════
ID_REPOS_SLIDER  = "qc-repos-slider"
ID_REPOS_GRAPH   = "qc-repos-graph"
ID_REPOS_DIAG    = "qc-repos-diagnosis"


def _velocity_quality_section(tech_ds):
    """Static skeleton: intro markdown, slider, graph, diagnosis div.

    The slider's max needs to come from actual data (max reposition count
    in tech.nc). If tech.nc isn't available, render a message instead.
    """
    intro = html.Div([
        html.Div("Subsurface velocity quality",
                 style={"fontSize": "18px", "fontWeight": "600",
                        "marginTop": "8px", "marginBottom": "4px"}),
        dcc.Markdown(
            "Beyond profiles, subsurface ocean currents at parking depth "
            "(typically 1000 dbar) can be estimated from passive Argo float "
            "drift between consecutive surface fixes. This estimate is "
            "contaminated when the float actively repositions during park "
            "(its buoyancy pump fires to maintain depth, e.g. in regions of "
            "strong vertical motion or fronts). Argo's data does not include "
            "ocean currents nor an associated QC flag, so estimating the "
            "quality of u, v requires counting repositions in `tech.nc`."
        ),
    ])

    if tech_ds is None:
        return html.Div([
            intro,
            html.Div("tech.nc not available — reposition counts unknown.",
                     style={"color": "#888", "fontStyle": "italic",
                            "marginTop": "8px"}),
        ])

    # Determine slider max from real reposition counts
    df_tech = _tech_to_df_local(tech_ds)
    cyc_rep, rep_vals = ah.get_param(df_tech, "NUMBER_RepositionsDuringPark_COUNT")
    if len(cyc_rep) == 0:
        return html.Div([
            intro,
            html.Div("No reposition records found in tech.nc.",
                     style={"color": "#888", "fontStyle": "italic",
                            "marginTop": "8px"}),
        ])

    max_repos = int(np.nanmax(rep_vals)) if len(rep_vals) else 1
    slider_max = max(20, max_repos)

    # Sparse marks every ~5 (or every value if few)
    if slider_max <= 10:
        marks = {i: str(i) for i in range(1, slider_max + 1)}
    else:
        step = max(1, slider_max // 10)
        marks = {i: str(i) for i in range(1, slider_max + 1, step)}
        marks[slider_max] = str(slider_max)

    return html.Div([
        intro,
        html.Div(
            "User can adjust the reposition threshold. It will flag cycles "
            "with ≥ N repositions. Strict (1) flags any active control by "
            "the float; loose values (5+) flag only egregious cases.",
            style={**CAPTION_STYLE, "marginTop": "10px"},
        ),
        html.Div(
            dcc.Slider(
                id=ID_REPOS_SLIDER,
                min=1, max=slider_max, step=1, value=1,
                marks=marks,
                tooltip={"placement": "bottom", "always_visible": False},
            ),
            style={"marginTop": "8px", "marginBottom": "16px",
                   "padding": "0 10px"},
        ),
        dcc.Loading(
            type="default", color="#0077B6",
            children=[
                dcc.Graph(id=ID_REPOS_GRAPH, config={"responsive": True},
                          figure=go.Figure()),
                html.Div(id=ID_REPOS_DIAG, style={"marginTop": "10px"}),
            ],
        ),
    ])


def _tech_to_df_local(tech_ds):
    """Local copy of the tech.nc xr.Dataset → tidy DataFrame conversion.
    Same shape as tab_health._tech_to_df. Kept local so this tab doesn't
    have to import from tab_health (cross-tab imports are cleaner avoided)."""
    if tech_ds is None:
        return None
    names  = ah.decode_bytes(tech_ds["TECHNICAL_PARAMETER_NAME"].values)
    values = ah.decode_bytes(tech_ds["TECHNICAL_PARAMETER_VALUE"].values)
    cycles = tech_ds["CYCLE_NUMBER"].values.astype(int)
    rows = []
    for n, v, c in zip(names, values, cycles):
        try:
            fval = float(v)
        except (ValueError, TypeError):
            fval = np.nan
        rows.append({"cycle": c, "param": n, "value": fval, "raw": v})
    return pd.DataFrame(rows)


@callback(
    Output(ID_REPOS_GRAPH, "figure"),
    Output(ID_REPOS_DIAG, "children"),
    Input(ID_REPOS_SLIDER, "value"),
    Input("store-wmo", "data"),
    Input("store-data-dir", "data"),
)
def _update_velocity_quality(threshold, wmo, data_dir):
    if threshold is None or not wmo or not data_dir:
        return go.Figure(), ""
    from app import get_datasets
    datasets = get_datasets(data_dir, wmo)
    tech_ds = datasets.get("tech")
    if tech_ds is None:
        return go.Figure(), ""

    df_tech = _tech_to_df_local(tech_ds)
    cyc_rep, rep_vals = ah.get_param(df_tech, "NUMBER_RepositionsDuringPark_COUNT")
    if len(cyc_rep) == 0:
        return go.Figure(), ""

    max_repos = int(np.nanmax(rep_vals)) if len(rep_vals) else 1
    threshold = int(threshold)

    flagged_mask  = rep_vals >= threshold
    flagged_cycles = sorted(cyc_rep[flagged_mask].astype(int).tolist())
    n_flagged = len(flagged_cycles)
    nonzero = rep_vals[rep_vals > 0]
    median_when_nonzero = float(np.median(nonzero)) if len(nonzero) else 0.0

    # Bar chart
    fig = go.Figure()
    blue_mask = rep_vals < threshold
    red_mask  = rep_vals >= threshold
    if blue_mask.any():
        fig.add_trace(go.Bar(
            x=cyc_rep[blue_mask], y=rep_vals[blue_mask],
            marker_color=ah.C_BLUE,
            name="Below threshold",
            hovertemplate="Cycle %{x}<br>Repositions: %{y}<extra></extra>",
        ))
    if red_mask.any():
        fig.add_trace(go.Bar(
            x=cyc_rep[red_mask], y=rep_vals[red_mask],
            marker_color=ah.C_RED,
            name="Above threshold (velocity estimate flagged)",
            hovertemplate="Cycle %{x}<br>Repositions: %{y}<extra></extra>",
        ))
    fig.add_hline(
        y=threshold, line_dash="dot", line_color=ah.C_RED, line_width=1.5,
        annotation_text=f"threshold (≥{threshold})",
        annotation_position="top right",
        annotation_font_color=ah.C_RED,
    )
    fig.update_layout(
        xaxis_title="Cycle number",
        yaxis_title="Reposition count",
        height=340,
        showlegend=True,
        legend=dict(orientation="h", y=-0.22),
        margin=dict(t=30, b=80),
    )

    # Diagnosis bullets — comma-join cycles to avoid markdown bracket-as-link
    plural = "s" if threshold > 1 else ""
    shown_cycles = flagged_cycles[:20]
    cycles_str = ", ".join(str(c) for c in shown_cycles)
    if n_flagged > 20:
        cycles_str += ", …"
    if not cycles_str:
        cycles_str = "(none)"

    diag_md = (
        f"**Diagnosis for this float (threshold = ≥ {threshold} reposition{plural}):**\n"
        f"- {n_flagged} of {len(cyc_rep)} cycles flagged\n"
        f"- Cycles flagged: {cycles_str}\n"
        f"- Median repositions (when nonzero): {median_when_nonzero:.0f}\n"
        f"- Max repositions in a single cycle: {max_repos}"
    )

    return fig, dcc.Markdown(diag_md)


# ══════════════════════════════════════════════════════════════════════════════
# Tab assembly
# ══════════════════════════════════════════════════════════════════════════════
SECTION_TITLE = {
    "fontSize": "18px", "fontWeight": "600",
    "marginTop": "8px", "marginBottom": "8px", "color": "#222",
}


def build_tab_qc(prof, sprof, tech, wmo):
    """Build the QC tab body."""
    ds_params = _ds_params(prof, sprof)
    if ds_params is None:
        return html.Div(
            "No profile file found (prof.nc / Sprof.nc).",
            style={"color": "#a00", "padding": "30px"},
        )
    params = _params_in_ds(ds_params)
    if not params:
        return html.Div(
            "No supported parameters found in this file.",
            style={"color": "#888", "padding": "30px"},
        )

    return html.Div([
        # ── Section 1: Reference area ─────────────────────────────────────────
        _data_mode_reference(),
        html.Div(_data_mode_strips(ds_params),
                 style={"marginTop": "12px"}),
        html.Hr(style={"margin": "16px 0"}),
        _qc_grade_reference(),

        html.Hr(style={"margin": "24px 0"}),

        # ── Section 2: QC of every parameter ──────────────────────────────────
        html.Div("QC of every parameter", style=SECTION_TITLE),
        dcc.Loading(
            type="default", color="#0077B6",
            children=_qc_per_param_section(ds_params, params),
        ),

        html.Hr(style={"margin": "24px 0"}),

        # ── Section 3: Scientific calibration ─────────────────────────────────
        _calibration_section(ds_params),

        html.Hr(style={"margin": "24px 0"}),

        # ── Section 4: Subsurface velocity quality ────────────────────────────
        _velocity_quality_section(tech),
    ])
