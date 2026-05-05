"""
tabs/tab_health.py — Float Health tab for Argo Float Monitor (Dash port).

Mirrors the original Streamlit tab_health layout:
  - Top: "Health summary" — auto-diagnosed bullet list grouped by subsystem.
  - 8 collapsible panels (html.Details), each rendering one engineering
    subsystem's per-cycle telemetry plots:
        1. Pressure      — surface offset, internal vacuum, air bladder
        2. Buoyancy      — buoyancy pump run time + linear trend
        3. Battery       — voltage panel, current panel (3–4 series each)
        4. Communication — GPS fix time, internal clock drift
        5. Repositions   — park repositions, descent pressure samples
        6. Ice           — ice-detection flag (polar floats)
        7. Piston        — Now/Surface/Park positions, Surface−Park gap
        8. Status flags  — CTD status, float status (manufacturer-specific)

All telemetry comes from `tech.nc`. The xr.Dataset is converted to a tidy
long-format DataFrame in this module (we don't reach back to disk).
"""
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import dcc, html, dash_table
from scipy import stats

import argo_helpers as ah
from tabs.tab_metadata import _expander, CAPTION_STYLE


# ── Color shortcuts (re-exported from argo_helpers for readability) ───────────
C_BLUE = ah.C_BLUE
C_RED  = ah.C_RED
C_GRN  = ah.C_GRN
C_ORG  = ah.C_ORG
C_PUR  = ah.C_PUR


# ══════════════════════════════════════════════════════════════════════════════
# tech.nc xr.Dataset → tidy long DataFrame
# (Local conversion; mirrors argo_helpers.parse_tech but without disk re-read.)
# ══════════════════════════════════════════════════════════════════════════════
def _tech_to_df(tech_ds):
    """Convert tech.nc xr.Dataset → long DataFrame [cycle, param, value, raw].

    Returns None if tech_ds is None.
    """
    if tech_ds is None:
        return None
    names  = ah.decode_bytes(tech_ds["TECHNICAL_PARAMETER_NAME"].values)
    values = ah.decode_bytes(tech_ds["TECHNICAL_PARAMETER_VALUE"].values)
    cycles = tech_ds["CYCLE_NUMBER"].values.astype(int)

    rows = []
    for name, val, cyc in zip(names, values, cycles):
        try:
            fval = float(val)
        except (ValueError, TypeError):
            fval = np.nan
        rows.append({"cycle": cyc, "param": name, "value": fval, "raw": val})
    return pd.DataFrame(rows)


# ══════════════════════════════════════════════════════════════════════════════
# Signal extraction
# Bundle every per-cycle series + every derived statistic into one dict so
# that panel renderers and the summary builder share a single source of truth.
# ══════════════════════════════════════════════════════════════════════════════
def _extract_signals(df_tech):
    """Return a dict containing all extracted (cycles, values) pairs and
    derived signals used across the summary and panels."""
    sig = {}

    # ── Per-cycle series ──────────────────────────────────────────────────────
    sig["cyc_pump"],   sig["pump_time"] = ah.get_param(df_tech, "TIME_BuoyancyPumpOn_seconds")
    sig["cyc_volt"],   sig["voltage"]   = ah.get_param(df_tech, "VOLTAGE_BatteryPumpOn_volts")
    sig["cyc_bat"],    sig["current"]   = ah.get_param(df_tech, "CURRENT_BatteryPumpOn_mA")
    sig["cyc_vac"],    sig["vacuum"]    = ah.get_param(df_tech, "PRESSURE_InternalVacuum_inHg")
    sig["cyc_pres"],   sig["pres_off"]  = ah.get_param(df_tech, "PRES_SurfaceOffsetNotTruncated_dbar")
    sig["cyc_repos"],  sig["repos"]     = ah.get_param(df_tech, "NUMBER_RepositionsDuringPark_COUNT")
    sig["cyc_psurf"],  sig["p_surf"]    = ah.get_param(df_tech, "POSITION_PistonSurface_COUNT")
    sig["cyc_ppark"],  sig["p_park"]    = ah.get_param(df_tech, "POSITION_PistonPark_COUNT")
    sig["cyc_pnow"],   sig["p_now"]     = ah.get_param(df_tech, "POSITION_PistonNow_COUNT")
    sig["cyc_air"],    sig["air_blad"]  = ah.get_param(df_tech, "PRESSURE_AirBladder_COUNT")
    sig["cyc_descs"],  sig["descs"]     = ah.get_param(df_tech, "NUMBER_PRESSamplesDuringDescentToPark_COUNT")
    sig["cyc_gpst"],   sig["gps_t"]     = ah.get_param(df_tech, "TIME_IridiumGPSFix_seconds")
    sig["cyc_clk"],    sig["clock_dr"]  = ah.get_param(df_tech, "CLOCK_RealTimeDrift_seconds")

    # Battery voltage panel inputs (4 series)
    sig["cyc_vnl"],     sig["v_noload"]  = ah.get_param(df_tech, "VOLTAGE_BatteryNoLoad_volts")
    sig["cyc_vpiston"], sig["v_piston"]  = ah.get_param(df_tech, "VOLTAGE_BatteryPistonPumpOn_volts")
    sig["cyc_vsbe"],    sig["v_sbe"]     = ah.get_param(df_tech, "VOLTAGE_BatterySBEPump_volts")

    # Battery current panel inputs (3 series)
    sig["cyc_inl"],  sig["i_noload"] = ah.get_param(df_tech, "CURRENT_BatteryNoLoad_mA")
    sig["cyc_isbe"], sig["i_sbe"]    = ah.get_param(df_tech, "CURRENT_BatterySBEPump_mA")

    # ── Derived: pump-time linear trend ───────────────────────────────────────
    if len(sig["cyc_pump"]) > 5:
        slope_, intercept_, *_ = stats.linregress(sig["cyc_pump"], sig["pump_time"])
        sig["pump_slope"] = float(slope_)
        sig["pump_trend"] = slope_ * sig["cyc_pump"] + intercept_
    else:
        sig["pump_slope"] = np.nan
        sig["pump_trend"] = None

    # ── CTD status hex flag → integer (kept as DataFrame for table display) ──
    ctd_rows = df_tech[df_tech["param"] == "FLAG_CTDStatus_hex"].copy()
    ctd_rows["int_val"] = ctd_rows["raw"].apply(
        lambda x: int(x, 16) if isinstance(x, str) and x else 0)
    sig["ctd_rows"] = ctd_rows
    sig["ctd_int"]  = ctd_rows["int_val"].values
    sig["n_ctd_bad"] = int(np.sum(sig["ctd_int"] > 0))

    # ── Pressure summary signals ──────────────────────────────────────────────
    sig["mean_pres_off"] = (float(np.nanmean(sig["pres_off"]))
                            if len(sig["pres_off"]) else np.nan)
    sig["peak_pres_off"] = (float(np.nanmax(np.abs(sig["pres_off"])))
                            if len(sig["pres_off"]) else np.nan)
    sig["vacuum_change"] = (float(sig["vacuum"][-1] - sig["vacuum"][0])
                            if len(sig["vacuum"]) > 1 else np.nan)

    # Air bladder linear slope (cycles vs count)
    sig["air_slope"] = (float(stats.linregress(sig["cyc_air"], sig["air_blad"]).slope)
                       if len(sig["cyc_air"]) > 5 else None)

    # ── Battery aging — NoLoad-PumpOn voltage gap, first 10 vs last 10 cycles
    sig["bat_gap_early"] = sig["bat_gap_late"] = None
    if len(sig["cyc_vnl"]) > 0 and len(sig["cyc_volt"]) > 0:
        common_b = np.intersect1d(sig["cyc_vnl"], sig["cyc_volt"])
        if len(common_b) >= 20:
            vnl_dict = dict(zip(sig["cyc_vnl"], sig["v_noload"]))
            vbp_dict = dict(zip(sig["cyc_volt"], sig["voltage"]))
            early = common_b[:10]
            late  = common_b[-10:]
            sig["bat_gap_early"] = float(np.mean([vnl_dict[c] - vbp_dict[c] for c in early]))
            sig["bat_gap_late"]  = float(np.mean([vnl_dict[c] - vbp_dict[c] for c in late]))

    # Pump current slope (mechanical resistance proxy)
    sig["i_pump_slope"] = (float(stats.linregress(sig["cyc_bat"], sig["current"]).slope)
                           if len(sig["cyc_bat"]) > 5 else None)

    # ── Communication & timing ────────────────────────────────────────────────
    sig["gps_median"] = (float(np.median(sig["gps_t"]))
                         if len(sig["gps_t"]) else np.nan)
    sig["gps_slope"] = (float(stats.linregress(sig["cyc_gpst"], sig["gps_t"]).slope)
                        if len(sig["cyc_gpst"]) > 5 else None)

    sig["clk_max"] = (float(np.max(np.abs(sig["clock_dr"])))
                      if len(sig["clock_dr"]) else np.nan)
    sig["clk_sawtooth"] = False
    if len(sig["clock_dr"]) > 5:
        # Sawtooth pattern: drift goes negative between GPS syncs, resets toward 0.
        sig["clk_sawtooth"] = (bool(np.any(sig["clock_dr"] < -5))
                               and bool(np.any(sig["clock_dr"] > -1)))

    # ── Repositions ───────────────────────────────────────────────────────────
    sig["n_repos_cycles"] = (int(np.sum(sig["repos"] >= 1))
                             if len(sig["repos"]) else 0)

    # ── Descent samples ───────────────────────────────────────────────────────
    sig["descs_mean"] = float(np.mean(sig["descs"])) if len(sig["descs"]) else np.nan
    sig["descs_std"]  = float(np.std(sig["descs"]))  if len(sig["descs"]) else np.nan

    # ── Ice ───────────────────────────────────────────────────────────────────
    ice_rows = df_tech[df_tech["param"] == "FLAG_IceDetected_bit"].copy()
    if len(ice_rows) > 0:
        sig["ice_int"]     = ice_rows["value"].astype(float).fillna(0).astype(int).values
        sig["n_ice"]       = int(np.sum(sig["ice_int"] > 0))
        sig["n_ice_total"] = len(sig["ice_int"])
    else:
        sig["ice_int"]     = np.array([])
        sig["n_ice"]       = 0
        sig["n_ice_total"] = 0
    sig["ice_rows"] = ice_rows

    # ── Piston gap (Surface − Park) ───────────────────────────────────────────
    common_p = np.intersect1d(sig["cyc_psurf"], sig["cyc_ppark"])
    if len(common_p) > 5:
        s_dict = dict(zip(sig["cyc_psurf"], sig["p_surf"]))
        p_dict = dict(zip(sig["cyc_ppark"], sig["p_park"]))
        gap_arr = np.array([s_dict[c] - p_dict[c] for c in common_p])
        sig["piston_gap_mean"]  = float(np.mean(gap_arr))
        sig["piston_gap_slope"] = float(stats.linregress(common_p, gap_arr).slope)
        sig["piston_common"]    = common_p
        sig["piston_gap"]       = gap_arr
    else:
        sig["piston_gap_mean"]  = np.nan
        sig["piston_gap_slope"] = None
        sig["piston_common"]    = np.array([])
        sig["piston_gap"]       = np.array([])

    # ── Float status flag ─────────────────────────────────────────────────────
    fs_rows = df_tech[df_tech["param"] == "FLAG_FloatStatus_hex"].copy()
    if len(fs_rows) > 0:
        fs_rows["int_val"] = fs_rows["raw"].apply(
            lambda x: int(x, 16) if isinstance(x, str) and x else 0)
        sig["n_fs_bad"] = int(np.sum(fs_rows["int_val"].values > 0))
        sig["fs_total"] = len(fs_rows)
    else:
        sig["n_fs_bad"] = 0
        sig["fs_total"] = 0
    sig["fs_rows"] = fs_rows

    return sig


# ══════════════════════════════════════════════════════════════════════════════
# Health summary (top of tab) — bullet-list markdown grouped by subsystem.
# Every line is auto-generated based on extracted signals.
# ══════════════════════════════════════════════════════════════════════════════
def _build_summary_md(s):
    """Compose the summary bullet list as one Markdown string."""
    lines = []

    # ── Pressure ──────────────────────────────────────────────────────────────
    lines.append("**Pressure**")
    if not np.isnan(s["peak_pres_off"]):
        if s["peak_pres_off"] > 20:
            ph = f"peak ±{s['peak_pres_off']:.2f} dbar — exceeds DMQC threshold"
        elif abs(s["mean_pres_off"]) > 5:
            ph = (f"mean {s['mean_pres_off']:+.2f} dbar — review against ±20 dbar "
                  "threshold")
        else:
            ph = (f"mean {s['mean_pres_off']:+.2f} dbar, peak ±{s['peak_pres_off']:.2f} "
                  "dbar — well within DMQC tolerance")
        lines.append(f"- **Surface pressure offset** — {ph}")
    if not np.isnan(s["vacuum_change"]):
        ph = "possible slow leak, monitor" if s["vacuum_change"] < -2.0 else "stable"
        lines.append(f"- **Internal vacuum** — {s['vacuum_change']:+.2f} inHg over "
                     f"deployment — {ph}")
    if s["air_slope"] is not None:
        if s["air_slope"] < -0.3:
            ph = "declining, pump or bladder may be degrading"
        elif s["air_slope"] > 0.3:
            ph = "rising"
        else:
            ph = "stable"
        lines.append(f"- **Air bladder pressure** — trend {s['air_slope']:+.2f}/cycle "
                     f"— {ph}")

    # ── Buoyancy ──────────────────────────────────────────────────────────────
    lines.append("\n**Buoyancy**")
    if not np.isnan(s["pump_slope"]):
        ph = "early buoyancy degradation signal" if s["pump_slope"] > 1.0 else "normal"
        lines.append(f"- **Pump time trend** — {s['pump_slope']:+.2f} s/cycle — {ph}")

    # ── Battery ───────────────────────────────────────────────────────────────
    lines.append("\n**Battery**")
    if s["bat_gap_early"] is not None and s["bat_gap_late"] is not None:
        delta = s["bat_gap_late"] - s["bat_gap_early"]
        if abs(delta) > 0.3:
            ph = (f"NoLoad–PumpOn voltage gap widened "
                  f"{s['bat_gap_early']:.2f} → {s['bat_gap_late']:.2f} V "
                  f"({delta:+.2f}) — internal resistance increasing")
        else:
            ph = (f"NoLoad–PumpOn voltage gap stable "
                  f"({s['bat_gap_early']:.2f} → {s['bat_gap_late']:.2f} V)")
        lines.append(f"- **Battery aging** — {ph}")
    if s["i_pump_slope"] is not None:
        if abs(s["i_pump_slope"]) < 0.5:
            ph = f"current draw stable (slope {s['i_pump_slope']:+.2f} mA/cycle)"
        elif s["i_pump_slope"] > 0.5:
            ph = (f"current draw rising (slope {s['i_pump_slope']:+.2f} mA/cycle) — "
                  "mechanical resistance increasing")
        else:
            ph = f"current draw decreasing (slope {s['i_pump_slope']:+.2f} mA/cycle)"
        lines.append(f"- **Pump current** — {ph}")

    # ── Communication & timing ────────────────────────────────────────────────
    lines.append("\n**Communication & timing**")
    if not np.isnan(s["gps_median"]):
        extra = ""
        if s["gps_slope"] is not None:
            if s["gps_slope"] > 1:
                extra = (f", trend {s['gps_slope']:+.2f} s/cycle — antenna may be "
                         "degrading")
            else:
                extra = f", trend {s['gps_slope']:+.2f} s/cycle — stable"
        lines.append(f"- **GPS fix time** — median {s['gps_median']:.0f} s{extra}")
    if not np.isnan(s["clk_max"]):
        if s["clk_sawtooth"]:
            ph = (f"max |drift| {s['clk_max']:.0f} s, sawtooth pattern — normal "
                  "(resets at GPS sync)")
        elif s["clk_max"] > 60:
            ph = f"max |drift| {s['clk_max']:.0f} s — large drift, investigate"
        else:
            ph = f"max |drift| {s['clk_max']:.0f} s"
        lines.append(f"- **Clock drift** — {ph}")

    # ── Repositions & drift ───────────────────────────────────────────────────
    lines.append("\n**Repositions & drift**")
    if len(s["repos"]) > 0:
        if s["n_repos_cycles"] == 0:
            ph = "0 cycles — velocity estimates clean"
        else:
            pct = s["n_repos_cycles"] / len(s["repos"]) * 100
            ph = (f"{s['n_repos_cycles']} of {len(s['repos'])} cycles ({pct:.0f}%) — "
                  "velocity estimates contaminated")
        lines.append(f"- **Cycles with repositions** — {ph}")
    if not np.isnan(s["descs_mean"]) and s["descs_mean"] > 0:
        cv = (s["descs_std"] / s["descs_mean"] * 100) if s["descs_mean"] else 0
        if cv > 50:
            ph = (f"mean {s['descs_mean']:.1f} samples, highly variable (cv={cv:.0f}%) "
                  "— check for sensor or comm issues")
        else:
            ph = f"mean {s['descs_mean']:.1f} samples — stable"
        lines.append(f"- **Pressure samples during descent** — {ph}")

    # ── Ice ───────────────────────────────────────────────────────────────────
    lines.append("\n**Ice**")
    if s["n_ice_total"] == 0:
        lines.append("- **Ice detection** — no ice flag data in tech.nc")
    elif s["n_ice"] == 0:
        lines.append(f"- **Ice detection** — no ice events detected in "
                     f"{s['n_ice_total']} cycles")
    else:
        lines.append(f"- **Ice detection** — {s['n_ice']} cycles "
                     f"({s['n_ice']/s['n_ice_total']*100:.0f}%) had ice evasion "
                     "triggered")

    # ── Piston ────────────────────────────────────────────────────────────────
    lines.append("\n**Piston**")
    if not np.isnan(s["piston_gap_mean"]) and s["piston_gap_slope"] is not None:
        if s["piston_gap_slope"] < -0.3:
            ph = (f"mean gap {s['piston_gap_mean']:.0f} counts, narrowing trend "
                  f"({s['piston_gap_slope']:+.2f}/cycle) — piston may not fully extend")
        elif abs(s["piston_gap_slope"]) <= 0.3:
            ph = f"mean gap {s['piston_gap_mean']:.0f} counts, stable"
        else:
            ph = (f"mean gap {s['piston_gap_mean']:.0f} counts, widening "
                  f"({s['piston_gap_slope']:+.2f}/cycle)")
        lines.append(f"- **Piston travel range (Surface − Park)** — {ph}")

    # ── Status flags ──────────────────────────────────────────────────────────
    lines.append("\n**Status flags (manufacturer-specific)**")
    if s["n_ctd_bad"] == 0:
        lines.append("- **CTD status flag** — all cycles clean")
    else:
        lines.append(f"- **CTD status flag anomalies** — {s['n_ctd_bad']} cycles — "
                     "cross-check against profile QC grades")
    if s["fs_total"] > 0:
        if s["n_fs_bad"] == 0:
            lines.append("- **Float status flag** — all cycles clean")
        else:
            pct = s["n_fs_bad"] / s["fs_total"] * 100
            lines.append(f"- **Float status flag** — non-zero on {s['n_fs_bad']} of "
                         f"{s['fs_total']} cycles ({pct:.0f}%) — manufacturer "
                         "documentation needed for interpretation")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# Small render helpers
# ══════════════════════════════════════════════════════════════════════════════
def _fig_with_caption(fig, caption=None):
    """Standard wrapper: a Plotly figure followed by an optional caption."""
    children = [dcc.Graph(figure=fig, config={"responsive": True})]
    if caption:
        children.append(html.Div(caption, style=CAPTION_STYLE))
    return html.Div(children, style={"marginBottom": "12px"})


def _line_fig(x, y, *, title, y_label, hover_unit, height=300, hline=None,
              hline_label=None):
    """Black markers+lines time series. Common shape used by ~6 panels."""
    fig = go.Figure(go.Scatter(
        x=x, y=y, mode="markers+lines",
        marker=dict(size=4, color="#000000"),
        line=dict(width=1, color="#000000"),
        hovertemplate=f"Cycle %{{x}}<br>%{{y:{hover_unit}}}<extra></extra>",
    ))
    fig.update_layout(
        title=title,
        xaxis_title="Cycle number",
        yaxis_title=y_label,
        height=height,
        margin=dict(t=50, b=50),
    )
    if hline is not None:
        # `hline` may be a single value or an iterable of (y, dash, color, label)
        if isinstance(hline, (int, float)):
            fig.add_hline(y=hline, line_dash="dot",
                          line_color=C_RED, line_width=1.5)
            if hline_label:
                fig.add_annotation(
                    xref="paper", x=0.99, y=hline, text=hline_label,
                    showarrow=False, font=dict(color=C_RED, size=10),
                    xanchor="right", yanchor="bottom",
                )
    return fig


# ══════════════════════════════════════════════════════════════════════════════
# Section 1: Pressure
# ══════════════════════════════════════════════════════════════════════════════
def panel_pressure(s):
    children = []

    # Surface pressure offset (with ±20 dbar threshold lines)
    if len(s["cyc_pres"]) > 0:
        fig = go.Figure(go.Scatter(
            x=s["cyc_pres"], y=s["pres_off"], mode="markers+lines",
            marker=dict(size=4, color="#000000"),
            line=dict(width=1, color="#000000"),
            hovertemplate="Cycle %{x}<br>%{y:+.2f} dbar<extra></extra>",
        ))
        for thr in (20, -20):
            fig.add_hline(y=thr, line_dash="dot", line_color=C_RED, line_width=1.5)
        fig.add_annotation(
            xref="paper", x=0.99, y=20, text="±20 dbar limit",
            showarrow=False, font=dict(color=C_RED, size=10),
            xanchor="right", yanchor="bottom",
        )
        fig.update_layout(
            title="Surface pressure offset",
            xaxis_title="Cycle number", yaxis_title="dbar",
            height=300, margin=dict(t=50, b=50),
            yaxis=dict(range=[-25, 25]),
        )
        children.append(_fig_with_caption(
            fig,
            "The pressure read by the CTD when the float is at the surface. "
            "Should be near 0; deviation >±20 dbar (red dotted lines) triggers "
            "DMQC pressure-bias adjustment. NetCDF variable: "
            "PRES_SurfaceOffsetNotTruncated_dbar."
        ))

    # Internal vacuum
    if len(s["cyc_vac"]) > 0:
        fig = _line_fig(
            s["cyc_vac"], s["vacuum"],
            title="Internal vacuum", y_label="inHg", hover_unit=".2f",
        )
        children.append(_fig_with_caption(
            fig,
            "Vacuum maintained inside the float's pressure hull. A steady "
            "decline indicates an O-ring slow leak — water is gradually entering "
            "the hull. NetCDF variable: PRESSURE_InternalVacuum_inHg."
        ))

    # Air bladder
    if len(s["cyc_air"]) > 0:
        fig = _line_fig(
            s["cyc_air"], s["air_blad"],
            title="Air bladder pressure", y_label="count", hover_unit=".0f",
        )
        children.append(_fig_with_caption(
            fig,
            "Pressure in the external air bladder used to push the float to the "
            "surface. Falling values mean the pump or bladder is degrading; the "
            "float may struggle to surface. NetCDF variable: "
            "PRESSURE_AirBladder_COUNT."
        ))

    if not children:
        return html.Div("No pressure telemetry in tech.nc.",
                        style={"color": "#888", "fontStyle": "italic"})
    return html.Div(children)


# ══════════════════════════════════════════════════════════════════════════════
# Section 2: Buoyancy
# ══════════════════════════════════════════════════════════════════════════════
def panel_buoyancy(s):
    if len(s["cyc_pump"]) == 0:
        return html.Div("No buoyancy-pump telemetry in tech.nc.",
                        style={"color": "#888", "fontStyle": "italic"})

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=s["cyc_pump"], y=s["pump_time"], mode="markers+lines",
        marker=dict(size=4, color="#000000"),
        line=dict(width=1, color="#000000"),
        name="Pump time",
        hovertemplate="Cycle %{x}<br>%{y:.0f} s<extra></extra>",
    ))
    if s["pump_trend"] is not None:
        fig.add_trace(go.Scatter(
            x=s["cyc_pump"], y=s["pump_trend"], mode="lines",
            line=dict(width=2.5, color=C_RED, dash="dash"),
            name=f"Trend  {s['pump_slope']:+.2f} s/cycle",
        ))
    fig.update_layout(
        title="Buoyancy pump run time",
        xaxis_title="Cycle number", yaxis_title="seconds",
        height=320, margin=dict(t=50, b=50),
        legend=dict(x=0.01, y=0.99),
    )
    return _fig_with_caption(
        fig,
        "Seconds the buoyancy pump runs each cycle to push the float to the "
        "surface. A gradual upward trend (red dashed line) indicates the "
        "buoyancy system is working harder over time. NetCDF variable: "
        "TIME_BuoyancyPumpOn_seconds."
    )


# ══════════════════════════════════════════════════════════════════════════════
# Section 3: Battery
# ══════════════════════════════════════════════════════════════════════════════
def panel_battery(s):
    children = []

    # ── 4-voltage panel ──
    v_specs = [
        (s["cyc_vnl"],     s["v_noload"], "No load",          C_BLUE),
        (s["cyc_volt"],    s["voltage"],  "Buoyancy pump on", C_GRN),
        (s["cyc_vpiston"], s["v_piston"], "Piston pump on",   C_ORG),
        (s["cyc_vsbe"],    s["v_sbe"],    "SBE CTD pump on",  C_PUR),
    ]
    if any(len(c) > 0 for c, *_ in v_specs):
        fig_v = go.Figure()
        for cyc, vals, label, color in v_specs:
            if len(cyc) == 0:
                continue
            fig_v.add_trace(go.Scatter(
                x=cyc, y=vals, mode="markers+lines",
                marker=dict(size=3, color=color),
                line=dict(width=1.2, color=color),
                name=label,
                hovertemplate=f"{label}<br>Cycle %{{x}}<br>%{{y:.2f}} V<extra></extra>",
            ))
        fig_v.update_layout(
            title="Battery voltage under different loads",
            xaxis_title="Cycle number", yaxis_title="Volts",
            height=380, margin=dict(t=50, b=80),
            legend=dict(orientation="h", y=-0.18),
        )
        children.append(_fig_with_caption(
            fig_v,
            "Battery voltage measured during four different load conditions. "
            "The gap between NoLoad voltage and loaded voltage equals the "
            "battery's internal resistance — an increasing gap means the "
            "battery is aging."
        ))

    # ── 3-current panel ──
    i_specs = [
        (s["cyc_inl"],  s["i_noload"], "No load",          C_BLUE),
        (s["cyc_bat"],  s["current"],  "Buoyancy pump on", C_GRN),
        (s["cyc_isbe"], s["i_sbe"],    "SBE CTD pump on",  C_PUR),
    ]
    if any(len(c) > 0 for c, *_ in i_specs):
        fig_i = go.Figure()
        for cyc, vals, label, color in i_specs:
            if len(cyc) == 0:
                continue
            fig_i.add_trace(go.Scatter(
                x=cyc, y=vals, mode="markers+lines",
                marker=dict(size=3, color=color),
                line=dict(width=1.2, color=color),
                name=label,
                hovertemplate=f"{label}<br>Cycle %{{x}}<br>%{{y:.1f}} mA<extra></extra>",
            ))
        fig_i.update_layout(
            title="Battery current draw under different loads",
            xaxis_title="Cycle number", yaxis_title="mA",
            height=360, margin=dict(t=50, b=80),
            legend=dict(orientation="h", y=-0.18),
        )
        children.append(_fig_with_caption(
            fig_i,
            "Current drawn by the battery under three load conditions. Rising "
            "current under the same load indicates mechanical resistance is "
            "increasing (pump or motor wear)."
        ))

    if not children:
        return html.Div("No battery telemetry in tech.nc.",
                        style={"color": "#888", "fontStyle": "italic"})
    return html.Div(children)


# ══════════════════════════════════════════════════════════════════════════════
# Section 4: Communication & timing
# ══════════════════════════════════════════════════════════════════════════════
def panel_comm_timing(s):
    children = []

    if len(s["cyc_gpst"]) > 0:
        fig = _line_fig(
            s["cyc_gpst"], s["gps_t"],
            title="Time to acquire GPS fix",
            y_label="seconds", hover_unit=".0f",
        )
        children.append(_fig_with_caption(
            fig,
            "Seconds taken to acquire a GPS fix at the surface. An upward trend "
            "means the GPS antenna is degrading or satellite reception is harder "
            "over time. Single-cycle spikes are usually transient. NetCDF "
            "variable: TIME_IridiumGPSFix_seconds."
        ))

    if len(s["cyc_clk"]) > 0:
        fig = go.Figure(go.Scatter(
            x=s["cyc_clk"], y=s["clock_dr"], mode="markers+lines",
            marker=dict(size=4, color="#000000"),
            line=dict(width=1, color="#000000"),
            hovertemplate="Cycle %{x}<br>%{y:+.0f} s<extra></extra>",
        ))
        fig.update_layout(
            title="Internal clock drift",
            xaxis_title="Cycle number", yaxis_title="seconds",
            height=280, margin=dict(t=50, b=50),
        )
        children.append(_fig_with_caption(
            fig,
            "Drift of the float's internal clock relative to GPS-corrected time. "
            "Sawtooth pattern is normal: drift accumulates between GPS syncs, "
            "then resets to ~0 when the float gets a fix. Unbounded growth = "
            "clock failure. NetCDF variable: CLOCK_RealTimeDrift_seconds."
        ))

    if not children:
        return html.Div("No communication/timing telemetry in tech.nc.",
                        style={"color": "#888", "fontStyle": "italic"})
    return html.Div(children)


# ══════════════════════════════════════════════════════════════════════════════
# Section 5: Repositions & drift
# ══════════════════════════════════════════════════════════════════════════════
def panel_repositions(s):
    children = []

    if len(s["cyc_repos"]) > 0:
        bar_colors = [C_RED if r >= 1 else C_BLUE for r in s["repos"]]
        fig_rp = go.Figure(go.Bar(
            x=s["cyc_repos"], y=s["repos"],
            marker_color=bar_colors,
            hovertemplate="Cycle %{x}<br>Repositions: %{y}<extra></extra>",
        ))
        fig_rp.add_hline(
            y=1, line_dash="dot", line_color=C_RED, line_width=1.5,
            annotation_text="velocity-QC threshold (≥1)",
            annotation_position="top right",
            annotation_font_color=C_RED,
        )
        fig_rp.update_layout(
            title="Repositions during park drift",
            xaxis_title="Cycle number", yaxis_title="count",
            height=320, margin=dict(t=50, b=50),
        )
        children.append(_fig_with_caption(
            fig_rp,
            "Number of times the float repositioned during its parking drift. "
            "Argo derives parking-depth velocity from passive drift between "
            "surface fixes; if the float repositioned (≥1, red bars), the "
            "velocity estimate for that cycle is contaminated. NetCDF "
            "variable: NUMBER_RepositionsDuringPark_COUNT."
        ))

        # Bad-cycles list (capped)
        # Note: we join into a plain comma-separated string rather than letting
        # Python format the list with brackets. dcc.Markdown interprets
        # `[...]` as a reference-style link and renders the contents as a
        # (broken) clickable anchor; joining sidesteps that entirely.
        n_bad = int(np.sum(s["repos"] >= 1))
        if n_bad > 0:
            bad_cycles = sorted(s["cyc_repos"][s["repos"] >= 1].astype(int).tolist())
            shown = bad_cycles[:20]
            cycles_str = ", ".join(str(c) for c in shown)
            ellipsis = " …" if n_bad > 20 else ""
            children.append(dcc.Markdown(
                f"**Cycles {cycles_str}**{ellipsis} — velocity estimates should "
                "be flagged for these cycles."
            ))

    if len(s["cyc_descs"]) > 0:
        fig_ds = _line_fig(
            s["cyc_descs"], s["descs"],
            title="Pressure samples during descent",
            y_label="count", hover_unit=".0f", height=280,
        )
        children.append(_fig_with_caption(
            fig_ds,
            "Number of pressure samples taken during descent to park depth. "
            "Anomalously low values suggest a sensor or comm issue during "
            "descent; constant values (typical) mean healthy descent. NetCDF "
            "variable: NUMBER_PRESSamplesDuringDescentToPark_COUNT."
        ))

    if not children:
        return html.Div("No reposition/drift telemetry in tech.nc.",
                        style={"color": "#888", "fontStyle": "italic"})
    return html.Div(children)


# ══════════════════════════════════════════════════════════════════════════════
# Section 6: Ice
# ══════════════════════════════════════════════════════════════════════════════
def panel_ice(s):
    if s["n_ice_total"] == 0:
        return html.Div([
            dcc.Markdown("FLAG_IceDetected_bit not present in tech.nc."),
            html.Div(
                "NetCDF variable: FLAG_IceDetected_bit. Mainly relevant for "
                "floats in polar regions.",
                style=CAPTION_STYLE,
            ),
        ])

    children = []
    if s["n_ice"] == 0:
        children.append(dcc.Markdown(
            f"No ice events detected in {s['n_ice_total']} cycles."
        ))
    else:
        children.append(dcc.Markdown(
            f"**{s['n_ice']} cycles** had ice evasion triggered."
        ))
        fig = go.Figure(go.Bar(
            x=s["ice_rows"]["cycle"],
            y=s["ice_int"],
            marker_color=[C_RED if v > 0 else C_BLUE for v in s["ice_int"]],
        ))
        fig.update_layout(
            title="Ice detection",
            xaxis_title="Cycle number", yaxis_title="bit",
            height=240, margin=dict(t=50, b=50),
        )
        children.append(dcc.Graph(figure=fig, config={"responsive": True}))

    children.append(html.Div(
        "Bitmask representing ice detection in the last 8 profiles. "
        "Non-zero = ice was detected and the profile may have been aborted at "
        "depth to avoid being crushed. Mainly relevant for floats in polar "
        "regions. NetCDF variable: FLAG_IceDetected_bit.",
        style=CAPTION_STYLE,
    ))
    return html.Div(children)


# ══════════════════════════════════════════════════════════════════════════════
# Section 7: Piston
# ══════════════════════════════════════════════════════════════════════════════
def panel_piston(s):
    children = []

    if (len(s["cyc_psurf"]) > 0
            or len(s["cyc_ppark"]) > 0
            or len(s["cyc_pnow"]) > 0):
        fig = go.Figure()
        if len(s["cyc_pnow"]) > 0:
            fig.add_trace(go.Scatter(
                x=s["cyc_pnow"], y=s["p_now"], mode="markers+lines",
                marker=dict(size=4, color=C_BLUE),
                line=dict(width=1, color=C_BLUE),
                name="Now (current cycle)",
                hovertemplate="Cycle %{x}<br>Now: %{y:.0f}<extra></extra>",
            ))
        if len(s["cyc_psurf"]) > 0:
            fig.add_trace(go.Scatter(
                x=s["cyc_psurf"], y=s["p_surf"], mode="markers+lines",
                marker=dict(size=4, color=C_GRN),
                line=dict(width=1, color=C_GRN),
                name="At surface",
                hovertemplate="Cycle %{x}<br>Surface: %{y:.0f}<extra></extra>",
            ))
        if len(s["cyc_ppark"]) > 0:
            fig.add_trace(go.Scatter(
                x=s["cyc_ppark"], y=s["p_park"], mode="markers+lines",
                marker=dict(size=4, color=C_ORG),
                line=dict(width=1, color=C_ORG),
                name="At park",
                hovertemplate="Cycle %{x}<br>Park: %{y:.0f}<extra></extra>",
            ))
        fig.update_layout(
            title="Piston positions",
            xaxis_title="Cycle number", yaxis_title="Stepper count",
            height=380, margin=dict(t=50, b=50),
            legend=dict(orientation="h", y=-0.15),
        )
        children.append(_fig_with_caption(
            fig,
            "Three piston states per cycle: current position (Now), position at "
            "surface, position at park. The Surface − Park gap should remain "
            "wide; if it narrows, the piston cannot fully extend and the float "
            "may fail to surface."
        ))

    # Surface − Park gap
    if len(s["piston_common"]) > 0:
        fig_gap = _line_fig(
            s["piston_common"], s["piston_gap"],
            title="Piston travel range (Surface − Park)",
            y_label="Stepper count gap", hover_unit=".0f",
        )
        children.append(_fig_with_caption(
            fig_gap,
            "Surface position minus Park position, expressed as a single value. "
            "A narrowing trend means the piston cannot fully extend → float "
            "may fail to surface."
        ))

    if not children:
        return html.Div("No piston telemetry in tech.nc.",
                        style={"color": "#888", "fontStyle": "italic"})
    return html.Div(children)


# ══════════════════════════════════════════════════════════════════════════════
# Section 8: Status flags (manufacturer-specific)
# ══════════════════════════════════════════════════════════════════════════════
def _flag_table(rows_df):
    """Render a hex-flag DataFrame as a Dash DataTable."""
    df_show = rows_df[["cycle", "raw", "int_val"]].rename(columns={
        "cycle":   "Cycle",
        "raw":     "Hex flag",
        "int_val": "Integer value",
    }).reset_index(drop=True)
    return dash_table.DataTable(
        data=df_show.to_dict("records"),
        columns=[{"name": c, "id": c} for c in df_show.columns],
        style_cell={"fontSize": "12px", "padding": "6px",
                    "fontFamily": "system-ui, sans-serif",
                    "textAlign": "left"},
        style_header={"fontWeight": "600", "backgroundColor": "#f0f0f0"},
        style_table={"overflowX": "auto", "marginTop": "6px",
                     "marginBottom": "10px"},
        page_size=15,
    )


def panel_status_flags(s):
    children = [html.Div(
        "Hex-encoded flags reported by the float firmware. Exact bit meaning "
        "is manufacturer-specific and not publicly documented; this section "
        "flags anomalous cycles for further investigation. When working a real "
        "fleet-monitoring case, cross-reference these against manufacturer "
        "documentation to interpret individual bits.",
        style=CAPTION_STYLE,
    )]

    # ── CTD status flag ──
    children.append(html.Div("CTD status flag",
                             style={"fontWeight": "600", "marginTop": "8px"}))
    if s["n_ctd_bad"] > 0:
        children.append(dcc.Markdown(f"{s['n_ctd_bad']} non-zero cycles:"))
        children.append(_flag_table(s["ctd_rows"][s["ctd_rows"]["int_val"] > 0]))
        children.append(html.Div(
            "Non-zero CTD status = error condition from CTD firmware. "
            "Cross-check against profile-level QC grades in prof.nc. NetCDF "
            "variable: FLAG_CTDStatus_hex.",
            style=CAPTION_STYLE,
        ))
    else:
        children.append(dcc.Markdown(
            "All cycles are 0 (no CTD errors reported)."
        ))
        children.append(html.Div(
            "NetCDF variable: FLAG_CTDStatus_hex.",
            style=CAPTION_STYLE,
        ))

    # ── Float status flag ──
    children.append(html.Div("Float status flag",
                             style={"fontWeight": "600", "marginTop": "12px"}))
    if s["fs_total"] == 0:
        children.append(dcc.Markdown(
            "FLAG_FloatStatus_hex not present in tech.nc."
        ))
        return html.Div(children)

    fs_rows = s["fs_rows"]
    children.append(dcc.Markdown(
        f"{s['n_fs_bad']} non-zero / {s['fs_total']} cycles"
    ))

    fig_fs = go.Figure(go.Bar(
        x=fs_rows["cycle"], y=fs_rows["int_val"],
        marker_color=[C_RED if v > 0 else C_GRN for v in fs_rows["int_val"]],
        hovertemplate="Cycle %{x}<br>flag: %{y}<extra></extra>",
    ))
    fig_fs.update_layout(
        title="Float status flag (per cycle)",
        xaxis_title="Cycle number", yaxis_title="Integer value of flag",
        height=280, margin=dict(t=50, b=50),
    )
    children.append(dcc.Graph(figure=fig_fs, config={"responsive": True}))
    children.append(html.Div(
        "Hex-encoded firmware status flag per cycle. Non-zero = firmware "
        "reported some condition. Exact bit meaning is manufacturer-specific. "
        "NetCDF variable: FLAG_FloatStatus_hex.",
        style=CAPTION_STYLE,
    ))

    if s["n_fs_bad"] > 0:
        children.append(_flag_table(fs_rows[fs_rows["int_val"] > 0]))

    return html.Div(children)


# ══════════════════════════════════════════════════════════════════════════════
# Tab assembly
# ══════════════════════════════════════════════════════════════════════════════
def build_tab_health(tech, wmo):
    """Build the Float Health tab body.

    Args:
        tech: xarray Dataset loaded from <wmo>_tech.nc, or None.
        wmo:  WMO number (unused at present, kept for signature consistency
              with the other build_tab_* functions).
    """
    df_tech = _tech_to_df(tech)
    if df_tech is None:
        return html.Div(
            "tech.nc not found — engineering telemetry unavailable.",
            style={"color": "#a00", "padding": "30px"},
        )

    s = _extract_signals(df_tech)

    return html.Div([
        # ── Top: auto-diagnosed health summary ────────────────────────────────
        html.H3("Health summary", style={"marginBottom": "8px"}),
        dcc.Markdown(_build_summary_md(s)),

        html.Hr(style={"margin": "20px 0"}),

        # ── 8 collapsible panels ──────────────────────────────────────────────
        _expander("Pressure",                panel_pressure(s)),
        _expander("Buoyancy",                panel_buoyancy(s)),
        _expander("Battery",                 panel_battery(s)),
        _expander("Communication & timing",  panel_comm_timing(s)),
        _expander("Repositions & drift",     panel_repositions(s)),
        _expander("Ice",                     panel_ice(s)),
        _expander("Piston",                  panel_piston(s)),
        _expander("Status flags (manufacturer-specific)",
                  panel_status_flags(s)),
    ])
