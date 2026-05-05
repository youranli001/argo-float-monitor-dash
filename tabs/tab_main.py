"""
tabs/tab_main.py — Main Information tab for Argo Float Monitor (Dash port).

Layout mirrors the original Streamlit version:
    Row 1 (full width):  global trajectory map
    Row 2 (2 columns):   Main Information  |  Tracking Lifecycle
    Row 3 (2 columns):   Deployment        |  Cycle Activity
"""
from collections import defaultdict

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import dcc, html

import argo_helpers as ah


# ── Shared styles ─────────────────────────────────────────────────────────────
GRID_2COL = {
    "display": "grid",
    "gridTemplateColumns": "1fr 1fr",
    "gap": "30px",
    "marginTop": "16px",
}
CARD_TITLE = {"fontSize": "16px", "fontWeight": "600",
              "marginBottom": "8px", "color": "#222"}


# ══════════════════════════════════════════════════════════════════════════════
# Map figure  (lifted from the `with tab_main:` block in argo_monitor.py)
# ══════════════════════════════════════════════════════════════════════════════
def build_map_figure(prof, wmo):
    lat = ah.mask_fill(prof["LATITUDE"].values)
    lon = ah.mask_fill(prof["LONGITUDE"].values)
    cycles_m = prof["CYCLE_NUMBER"].values.astype(int)
    dm_arr = ah.decode_bytes(prof["DATA_MODE"].values)
    dates_m = ah.juld_to_dates(prof["JULD"].values)
    dates_s_m = [str(d)[:10] if pd.notna(d) else "—" for d in dates_m]
    valid = ~np.isnan(lat) & ~np.isnan(lon)

    hover_text = [
        f"Cycle {c}<br>{ds}<br>({la:.3f}°, {lo:.3f}°)  [{md_}]"
        for c, ds, la, lo, md_ in zip(
            cycles_m[valid], np.array(dates_s_m)[valid],
            lat[valid], lon[valid], dm_arr[valid]
        )
    ]

    fig = go.Figure()
    fig.add_trace(go.Scattergeo(
        lon=lon[valid], lat=lat[valid], mode="lines",
        line=dict(width=1, color="rgba(0,119,182,0.4)"),
        showlegend=False, hoverinfo="skip",
    ))
    fig.add_trace(go.Scattergeo(
        lon=lon[valid], lat=lat[valid], mode="markers",
        marker=dict(
            size=5, color=cycles_m[valid],
            colorscale="Plasma", showscale=True,
            colorbar=dict(title="Cycle", len=0.6, thickness=10,
                          x=1.02, xanchor="left"),
        ),
        text=hover_text,
        hovertemplate="%{text}<extra></extra>",
        name="Profiles",
    ))

    v_idx = np.where(valid)[0]
    if len(v_idx) > 0:
        for idx, label_, sym, clr in [
            (v_idx[0],  "Start", "triangle-up",   "blue"),
            (v_idx[-1], "End",   "triangle-down", "red"),
        ]:
            fig.add_trace(go.Scattergeo(
                lon=[lon[idx]], lat=[lat[idx]],
                mode="markers+text",
                marker=dict(size=11, symbol=sym, color=clr),
                text=[label_], textposition="top right",
                textfont=dict(size=10),
                name=f"{label_} ({dates_s_m[idx]})",
            ))

    fig.update_layout(
        geo=dict(
            projection_type="natural earth",
            showland=True,  landcolor="#f5f0eb",
            showocean=True, oceancolor="#cce5f5",
            showcoastlines=True, coastlinecolor="#999",
            showcountries=True, countrycolor="#bbb",
            domain=dict(x=[0, 1], y=[0, 1]),
        ),
        title=dict(
            text=f"Float {wmo} — {valid.sum()} position fixes  "
                 f"<span style='font-size:11px;color:#666'>"
                 f"(scroll/box-zoom to explore)</span>",
            font=dict(size=13),
        ),
        height=520, autosize=True,
        margin=dict(l=0, r=10, t=40, b=0),
        legend=dict(orientation="h", yanchor="bottom", y=-0.05,
                    xanchor="center", x=0.5, font=dict(size=10)),
    )
    return fig


# ══════════════════════════════════════════════════════════════════════════════
# Card builders  (translated from render_* functions in argo_monitor.py)
# ══════════════════════════════════════════════════════════════════════════════
def card_main_information(meta, prof):
    if meta is None or prof is None:
        return html.Div("meta.nc or prof.nc not loaded.",
                        style={"color": "#a00"})

    wmo     = ah.d(meta["PLATFORM_NUMBER"].values)
    dac     = ah.d(meta["DATA_CENTRE"].values)
    country = ah.derive_country(dac)
    model   = ah.d(meta["PLATFORM_TYPE"].values)
    family  = ah.d(meta["PLATFORM_FAMILY"].values)
    trans   = ah.d(meta["TRANS_SYSTEM"].values[0])
    ptt     = ah.d(meta["PTT"].values)
    ship    = ah.d(meta["DEPLOYMENT_PLATFORM"].values)
    nets    = ah.derive_networks(meta)
    status  = ah.derive_status(meta, prof)

    ptt_part = f"   PTT: {ptt}" if ptt and ptt.lower() != "n/a" else ""
    md = f"""
- **Reference**: `{wmo}`
- **WMO ID**: `{wmo}`
- **WIGOS ID**: `{ah.wigos_id(wmo)}`
- **Status**: {status}
- **Country**: {country} ({dac})
- **Model**: {model} ({family.lower()})
- **Telecom**: {trans}{ptt_part}
- **Networks**: {", ".join(nets)}
- **Ship**: {ship}
"""
    return dcc.Markdown(md)


def card_tracking_lifecycle(meta, prof):
    if meta is None or prof is None:
        return html.Div("meta.nc or prof.nc not loaded.",
                        style={"color": "#a00"})

    launch_dt  = ah.parse_argo_date(meta["LAUNCH_DATE"].values)
    launch_lat = float(meta["LAUNCH_LATITUDE"].values)
    launch_lon = float(meta["LAUNCH_LONGITUDE"].values)
    n          = prof.sizes["N_PROF"]
    last_dt    = ah.juld_to_dt(float(prof["JULD"].values[-1]))
    last_lat   = float(prof["LATITUDE"].values[-1])
    last_lon   = float(prof["LONGITUDE"].values[-1])
    last_cyc   = int(prof["CYCLE_NUMBER"].values[-1])

    md = f"""
**Deployed**
- Latitude: `{launch_lat:.4f}`
- Longitude: `{launch_lon:.4f}`
- Date: {ah.fmt_date(launch_dt)}

**Latest observation**  ({n} profiles, latest = Cycle #{last_cyc})
- Latitude: `{last_lat:.4f}`
- Longitude: `{last_lon:.4f}`
- Date: {ah.fmt_date(last_dt, with_ago=True)}
"""
    return dcc.Markdown(md)


def card_deployment(meta):
    if meta is None:
        return html.Div("meta.nc not loaded.", style={"color": "#a00"})

    launch_dt  = ah.parse_argo_date(meta["LAUNCH_DATE"].values)
    startup_dt = ah.parse_argo_date(meta["STARTUP_DATE"].values)
    start_dt   = ah.parse_argo_date(meta["START_DATE"].values)
    launch_lat = float(meta["LAUNCH_LATITUDE"].values)
    launch_lon = float(meta["LAUNCH_LONGITUDE"].values)
    launch_qc  = ah.d(meta["LAUNCH_QC"].values)
    ship       = ah.d(meta["DEPLOYMENT_PLATFORM"].values)
    cruise     = ah.d(meta["DEPLOYMENT_CRUISE_ID"].values)
    ref_st     = ah.d(meta["DEPLOYMENT_REFERENCE_STATION_ID"].values)
    project    = ah.d(meta["PROJECT_NAME"].values)
    pi         = ah.d(meta["PI_NAME"].values)

    md = f"""
- **Launched**: {ah.fmt_date(launch_dt, with_ago=True)}
- **Startup date**: {ah.fmt_date(startup_dt)}
- **First dive**: {ah.fmt_date(start_dt)}
- **Latitude**: `{launch_lat:.4f}`
- **Longitude**: `{launch_lon:.4f}`
- **Launch QC**: {launch_qc}
- **Ship**: {ship}
- **Cruise**: {cruise if cruise.lower() != "n/a" else "n/a"}
- **Reference station**: {ref_st if ref_st.lower() != "n/a" else "n/a"}
- **Project**: {project}
- **PI**: {pi}
"""
    return dcc.Markdown(md)


def card_cycle_activity(meta, prof, sprof):
    if meta is None or prof is None:
        return html.Div("meta.nc or prof.nc not loaded.",
                        style={"color": "#a00"})

    status    = ah.derive_status(meta, prof)
    launch_dt = ah.parse_argo_date(meta["LAUNCH_DATE"].values)
    last_dt   = ah.juld_to_dt(float(prof["JULD"].values[-1]))
    last_cyc  = int(prof["CYCLE_NUMBER"].values[-1])
    n_profs   = prof.sizes["N_PROF"]

    age_str = "n/a"
    if launch_dt is not None and last_dt is not None:
        age_days = (last_dt - launch_dt).days
        age_str = f"{age_days/365.25:.2f} years  ({age_days} days)"

    # Data modes by parameter group
    BGC_DISPLAY_NAMES = {
        "PH_IN_SITU_TOTAL": "PH",
        "BBP700": "Backscatter",
    }
    HIDE_PARAMS = {"CHLA_FLUORESCENCE"}

    ds_for_modes = sprof if sprof is not None else prof
    dm_lines = []
    if "STATION_PARAMETERS" in ds_for_modes:
        stat_params = ah.decode_bytes(
            ds_for_modes["STATION_PARAMETERS"].values[0, :])
        param_counts = {}
        for p in stat_params:
            if not p or p in HIDE_PARAMS:
                continue
            modes_p = ah.get_data_mode_per_param(ds_for_modes, p)
            r = int(np.sum(modes_p == "R"))
            a = int(np.sum(modes_p == "A"))
            d = int(np.sum(modes_p == "D"))
            param_counts[p] = (r, a, d)

        sig_groups = defaultdict(list)
        for p, sig in param_counts.items():
            sig_groups[sig].append(p)

        CTD_CORE = {"PRES", "TEMP", "PSAL"}
        def _grp_key(item):
            sig, ps = item
            r, a, d = sig
            is_core = bool(CTD_CORE & set(ps))
            return (0 if is_core else 1, -d, ",".join(sorted(ps)))

        groups_sorted = sorted(sig_groups.items(), key=_grp_key)
        for sig, ps in groups_sorted:
            r, a, d = sig
            display_names = sorted(BGC_DISPLAY_NAMES.get(p, p) for p in ps)
            ps_str = ", ".join(display_names)
            dm_lines.append(f"  - {ps_str} — D={d}, A={a}, R={r}")
    else:
        dm_arr = ah.decode_bytes(prof["DATA_MODE"].values)
        n_R = int((dm_arr == "R").sum())
        n_A = int((dm_arr == "A").sum())
        n_D = int((dm_arr == "D").sum())
        dm_lines.append(f"  - All parameters — D={n_D}, A={n_A}, R={n_R}")

    # Dive depth
    designed_depth = ah.get_config(meta, "CONFIG_ProfilePressure_dbar")
    pres_arr = (prof["PRES_ADJUSTED"].values if "PRES_ADJUSTED" in prof
                else prof["PRES"].values)
    pres_clean = np.where(pres_arr > 99990, np.nan, pres_arr)
    max_per_cycle = np.nanmax(pres_clean, axis=1)
    valid_max = max_per_cycle[~np.isnan(max_per_cycle)]
    if len(valid_max) > 0:
        min_d = float(np.min(valid_max))
        max_d = float(np.max(valid_max))
        latest_d = float(valid_max[-1])
    else:
        min_d = max_d = latest_d = None

    surf, bot = ah.last_cycle_surface_bottom(
        sprof if sprof is not None else prof)

    md = f"""
- **Status**: {status}
- **Age**: {age_str}
- **Last profile**: {ah.fmt_date(last_dt, with_ago=True)}
- **Latest cycle**: #{last_cyc}  ({n_profs} profiles total)
- **Data modes**:
"""
    md += "\n".join(dm_lines) + "\n"

    md += "- **Dive depth**:\n"
    if designed_depth is not None:
        md += f"  - Designed: {designed_depth:.0f} dbar\n"
    if min_d is not None:
        md += f"  - Min: {min_d:.0f} dbar\n"
        md += f"  - Max: {max_d:.0f} dbar\n"
        md += f"  - Latest: {latest_d:.0f} dbar\n"

    if surf is not None:
        md += (f"- **Last surface data**: {surf['P']:.2f} dbar, "
               f"{surf['T']:.3f} °C, {surf['S']:.3f} PSU\n")
    if bot is not None:
        md += (f"- **Last bottom data**: {bot['P']:.2f} dbar, "
               f"{bot['T']:.3f} °C, {bot['S']:.3f} PSU\n")

    return dcc.Markdown(md)


# ══════════════════════════════════════════════════════════════════════════════
# Tab assembly
# ══════════════════════════════════════════════════════════════════════════════
def build_tab_main(meta, prof, sprof, wmo):
    if prof is None or meta is None:
        return html.Div(
            "prof.nc or meta.nc not loaded.",
            style={"color": "#a00", "padding": "30px"},
        )

    return html.Div([
        # Map
        dcc.Graph(
            figure=build_map_figure(prof, wmo),
            config={"responsive": True},
            style={"width": "100%"},
        ),

        # Row 1: Main Information | Tracking Lifecycle
        html.Div([
            html.Div([
                html.Div("Main Information", style=CARD_TITLE),
                card_main_information(meta, prof),
            ]),
            html.Div([
                html.Div("Tracking Lifecycle", style=CARD_TITLE),
                card_tracking_lifecycle(meta, prof),
            ]),
        ], style=GRID_2COL),

        html.Hr(style={"margin": "24px 0"}),

        # Row 2: Deployment | Cycle Activity
        html.Div([
            html.Div([
                html.Div("Deployment", style=CARD_TITLE),
                card_deployment(meta),
            ]),
            html.Div([
                html.Div("Cycle Activity", style=CARD_TITLE),
                card_cycle_activity(meta, prof, sprof),
            ]),
        ], style=GRID_2COL),
    ])
