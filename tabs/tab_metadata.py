"""
tabs/tab_metadata.py — Float Metadata tab for Argo Float Monitor (Dash port).

Mirrors the original Streamlit tab_meta layout:
  6 collapsible sections, each rendering a piece of meta.nc content.
    1. Argo project information   — float identity + project info
    2. Platform information       — controller board + transmission
    3. Deployment information     — same content as Main Information tab's
                                    Deployment card; lifted to a helper.
    4. Sensors                    — sensor + parameter table (DataTable)
    5. Factory calibration        — per-parameter equation/coefficients/comments
    6. Configuration parameters   — launch config table + mission diff
"""
import numpy as np
import pandas as pd
from dash import dcc, html, dash_table

import argo_helpers as ah
from tabs.tab_main import card_deployment   # reuse


# ── Shared styles ─────────────────────────────────────────────────────────────
CAPTION_STYLE = {"fontSize": "12px", "color": "#666",
                 "marginTop": "-4px", "marginBottom": "10px"}

DETAILS_STYLE = {
    "border": "1px solid #e5e5e5",
    "borderRadius": "6px",
    "padding": "12px 16px",
    "marginBottom": "10px",
    "backgroundColor": "#fafafa",
}

SUMMARY_STYLE = {
    "fontSize": "14px", "fontWeight": "600",
    "cursor": "pointer", "padding": "4px 0",
}

CODE_BLOCK_STYLE = {
    "backgroundColor": "#f0f0f0", "padding": "8px 12px",
    "borderRadius": "4px", "fontFamily": "monospace",
    "fontSize": "12px", "whiteSpace": "pre-wrap",
    "overflowX": "auto", "marginTop": "4px",
}


def _expander(title: str, body, caption: str = None, open_default: bool = False):
    """Wrap children in a <details> element — Dash analog of st.expander."""
    summary_children = [html.Span(title, style={"marginLeft": "4px"})]
    summary = html.Summary(summary_children, style=SUMMARY_STYLE)
    inner = []
    if caption:
        inner.append(html.Div(caption, style=CAPTION_STYLE))
    if isinstance(body, list):
        inner.extend(body)
    else:
        inner.append(body)
    return html.Details(
        [summary, html.Div(inner, style={"marginTop": "8px"})],
        open=open_default,
        style=DETAILS_STYLE,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Section 1: Argo project information
# ══════════════════════════════════════════════════════════════════════════════
def section_argo_project_info(meta, prof):
    if meta is None or prof is None:
        return html.Div("meta.nc or prof.nc not loaded.", style={"color": "#a00"})

    wmo     = ah.d(meta["PLATFORM_NUMBER"].values)
    dac     = ah.d(meta["DATA_CENTRE"].values)
    country = ah.derive_country(dac)
    model   = ah.d(meta["PLATFORM_TYPE"].values)
    family  = ah.d(meta["PLATFORM_FAMILY"].values)
    trans   = ah.d(meta["TRANS_SYSTEM"].values[0])
    ship    = ah.d(meta["DEPLOYMENT_PLATFORM"].values)
    nets    = ah.derive_networks(meta)
    status  = ah.derive_status(meta, prof)
    dac_full = ah.DAC_INFO.get(dac, (country, dac))[1]

    md = f"""
**Float identity**

- **Reference / WMO ID**: `{wmo}`
- **WIGOS ID**: `{ah.wigos_id(wmo)}`
- **Status**: {status}
- **Country**: {country} ({dac})
- **Model**: {model} ({family.lower()})
- **Telecom**: {trans}
- **Networks**: {", ".join(nets)}
- **Ship**: {ship}

**Project**

- **Project name**: {ah.d(meta["PROJECT_NAME"].values)}
- **PI**: {ah.d(meta["PI_NAME"].values)}
- **Float owner**: {ah.d(meta["FLOAT_OWNER"].values)}
- **Operating institution**: {ah.d(meta["OPERATING_INSTITUTION"].values)}
- **Data centre**: {dac_full} ({dac})
"""
    return dcc.Markdown(md)


# ══════════════════════════════════════════════════════════════════════════════
# Section 2: Platform information (controller + transmission)
# ══════════════════════════════════════════════════════════════════════════════
def section_platform_info(meta):
    if meta is None:
        return html.Div("meta.nc not loaded.", style={"color": "#a00"})

    cb_pri_type   = ah.d(meta["CONTROLLER_BOARD_TYPE_PRIMARY"].values)
    cb_pri_serial = ah.d(meta["CONTROLLER_BOARD_SERIAL_NO_PRIMARY"].values)
    cb_sec_type   = ah.d(meta["CONTROLLER_BOARD_TYPE_SECONDARY"].values)
    cb_sec_serial = ah.d(meta["CONTROLLER_BOARD_SERIAL_NO_SECONDARY"].values)
    manual_ver    = ah.d(meta["MANUAL_VERSION"].values)

    def _join_array(var):
        if var not in meta:
            return "n/a"
        arr = meta[var].values
        if arr.ndim == 0:
            return ah.d(arr) or "n/a"
        items = [ah.d(x) for x in arr.flat if ah.d(x)]
        return ", ".join(items) if items else "n/a"

    md = f"""
**Controller board**

- **Primary type**: {cb_pri_type or 'n/a'}
- **Primary serial**: {cb_pri_serial or 'n/a'}
- **Secondary type**: {cb_sec_type or 'n/a'}
- **Secondary serial**: {cb_sec_serial or 'n/a'}
- **Manual version**: {manual_ver or 'n/a'}

**Transmission & positioning**

- **Transmission system**: {_join_array('TRANS_SYSTEM')}
- **System ID**: {_join_array('TRANS_SYSTEM_ID')}
- **Frequency**: {_join_array('TRANS_FREQUENCY')}
- **PTT**: {ah.d(meta['PTT'].values) or 'n/a'}
- **Positioning system**: {_join_array('POSITIONING_SYSTEM')}

**Anomaly / customisation / special features**

- **Anomaly**: {ah.d(meta['ANOMALY'].values) or '(none reported)'}
- **Special features**: {ah.d(meta['SPECIAL_FEATURES'].values) or '(none)'}
- **Customisation**: {ah.d(meta['CUSTOMISATION'].values) or '(none)'}
"""
    return dcc.Markdown(md)


# ══════════════════════════════════════════════════════════════════════════════
# Section 4: Sensors / parameter table
# ══════════════════════════════════════════════════════════════════════════════
PARAM_ORDER = [
    "PRES", "TEMP", "PSAL",
    "DOXY", "NITRATE", "PH_IN_SITU_TOTAL", "CHLA", "BBP700",
    "TEMP_DOXY", "PHASE_DELAY_DOXY", "TEMP_VOLTAGE_DOXY",
]


def section_sensors_table(meta):
    if meta is None or "PARAMETER" not in meta:
        return html.Div("PARAMETER variable not in meta.nc.",
                        style={"color": "#a00"})

    # Build sensor lookup
    sensor_lookup = {}
    n_sensor = meta.sizes.get("N_SENSOR", 0)
    for i in range(n_sensor):
        sname = ah.d(meta["SENSOR"].values[i])
        sensor_lookup[sname] = {
            "Maker":      ah.d(meta["SENSOR_MAKER"].values[i]),
            "Model":      ah.d(meta["SENSOR_MODEL"].values[i]),
            "Serial No.": ah.d(meta["SENSOR_SERIAL_NO"].values[i]),
        }

    n_param = meta.sizes["N_PARAM"]
    rows = []
    for i in range(n_param):
        param  = ah.d(meta["PARAMETER"].values[i])
        sensor = ah.d(meta["PARAMETER_SENSOR"].values[i])
        sinfo  = sensor_lookup.get(sensor,
                    {"Maker": "n/a", "Model": "n/a", "Serial No.": "n/a"})
        rows.append({
            "Parameter":  param,
            "Sensor":     sensor,
            "Units":      ah.d(meta["PARAMETER_UNITS"].values[i]),
            "Maker":      sinfo["Maker"],
            "Model":      sinfo["Model"],
            "Serial No.": sinfo["Serial No."],
            "Accuracy":   ah.d(meta["PARAMETER_ACCURACY"].values[i]),
            "Resolution": ah.d(meta["PARAMETER_RESOLUTION"].values[i]),
        })

    def sort_key(row):
        p = row["Parameter"]
        return (PARAM_ORDER.index(p) if p in PARAM_ORDER else 999, p)
    rows.sort(key=sort_key)

    df = pd.DataFrame(rows).replace("", "n/a")
    df = df[["Parameter", "Sensor", "Units", "Maker", "Model",
             "Serial No.", "Accuracy", "Resolution"]]

    return dash_table.DataTable(
        data=df.to_dict("records"),
        columns=[{"name": c, "id": c} for c in df.columns],
        style_cell={"fontSize": "12px", "padding": "6px",
                    "fontFamily": "system-ui, sans-serif"},
        style_header={"fontWeight": "600", "backgroundColor": "#f0f0f0"},
        style_table={"overflowX": "auto"},
        page_size=20,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Section 5: Factory (predeployment) calibration
# ══════════════════════════════════════════════════════════════════════════════
def section_factory_calibration(meta):
    if meta is None or "PARAMETER" not in meta:
        return html.Div("PARAMETER variable not in meta.nc.",
                        style={"color": "#a00"})

    n_param = meta.sizes["N_PARAM"]
    indices = list(range(n_param))

    def sort_key(i):
        p = ah.d(meta["PARAMETER"].values[i])
        return (PARAM_ORDER.index(p) if p in PARAM_ORDER else 999, p)
    indices.sort(key=sort_key)

    children = []
    for i in indices:
        param = ah.d(meta["PARAMETER"].values[i])
        eq    = ah.d(meta["PREDEPLOYMENT_CALIB_EQUATION"].values[i])
        coef  = ah.d(meta["PREDEPLOYMENT_CALIB_COEFFICIENT"].values[i])
        cmt   = ah.d(meta["PREDEPLOYMENT_CALIB_COMMENT"].values[i])

        body = []
        if eq and eq.upper() != "N/A":
            body.append(html.Div("Equation",
                                 style={"fontWeight": "600",
                                        "marginTop": "4px"}))
            body.append(html.Pre(eq, style=CODE_BLOCK_STYLE))
        else:
            body.append(html.Div(
                "No equation provided (CTD parameters typically have no equation).",
                style={**CAPTION_STYLE, "marginTop": "0"},
            ))

        if coef and coef.upper() not in ("N/A", "NA;"):
            body.append(html.Div("Coefficients",
                                 style={"fontWeight": "600",
                                        "marginTop": "8px"}))
            body.append(html.Pre(coef, style=CODE_BLOCK_STYLE))

        if cmt and cmt.upper() != "N/A":
            body.append(html.Div("Comment",
                                 style={"fontWeight": "600",
                                        "marginTop": "8px"}))
            body.append(dcc.Markdown(cmt))

        children.append(_expander(param, body))

    return html.Div(children)


# ══════════════════════════════════════════════════════════════════════════════
# Section 6: Configuration parameters (launch + mission)
# ══════════════════════════════════════════════════════════════════════════════
def section_configuration(meta):
    if meta is None:
        return html.Div("meta.nc not loaded.", style={"color": "#a00"})

    children = []

    # Launch configuration
    children.append(html.Div("Launch configuration",
                             style={"fontWeight": "600", "marginTop": "4px"}))
    children.append(html.Div(
        "Parameters set at deployment and immutable thereafter.",
        style=CAPTION_STYLE,
    ))

    if "LAUNCH_CONFIG_PARAMETER_NAME" in meta:
        names  = meta["LAUNCH_CONFIG_PARAMETER_NAME"].values
        values = meta["LAUNCH_CONFIG_PARAMETER_VALUE"].values
        rows = []
        for n, v in zip(names, values):
            param = ah.d(n)
            val = float(v) if v < 99999 else None
            rows.append({
                "Parameter": param,
                "Value":     "" if val is None else f"{val:g}",
                "Meaning":   ah._config_meaning(param),
            })
        df_launch = pd.DataFrame(rows)

        children.append(dash_table.DataTable(
            data=df_launch.to_dict("records"),
            columns=[{"name": c, "id": c} for c in df_launch.columns],
            style_cell={"fontSize": "12px", "padding": "6px",
                        "fontFamily": "system-ui, sans-serif",
                        "textAlign": "left"},
            style_header={"fontWeight": "600", "backgroundColor": "#f0f0f0"},
            style_table={"overflowX": "auto", "marginBottom": "16px"},
            page_size=30,
        ))
    else:
        children.append(html.Div(
            "LAUNCH_CONFIG_PARAMETER_* not present in meta.nc.",
            style={"color": "#888", "fontStyle": "italic",
                   "marginBottom": "12px"},
        ))

    # Mission configuration
    children.append(html.Div("Mission configuration",
                             style={"fontWeight": "600", "marginTop": "12px"}))

    if "CONFIG_PARAMETER_NAME" not in meta:
        children.append(html.Div(
            "CONFIG_PARAMETER_* not present in meta.nc.",
            style={"color": "#888", "fontStyle": "italic"},
        ))
        return html.Div(children)

    cfg_names    = meta["CONFIG_PARAMETER_NAME"].values
    cfg_values   = meta["CONFIG_PARAMETER_VALUE"].values
    mission_nums = meta["CONFIG_MISSION_NUMBER"].values
    n_missions   = len(mission_nums)

    cfg_table = pd.DataFrame(
        cfg_values.T,
        index=[ah.d(n) for n in cfg_names],
        columns=[f"Mission {int(m)}" for m in mission_nums],
    )
    cfg_table = cfg_table.where(cfg_table < 99999, np.nan)

    all_identical = bool((cfg_table.nunique(axis=1, dropna=True) <= 1).all())

    if all_identical:
        children.append(dcc.Markdown(
            f"This float has never been re-configured: all {n_missions} cycles "
            "use the same mission parameters (identical to launch configuration "
            "above)."
        ))
    else:
        changed_rows = cfg_table.nunique(axis=1, dropna=True) > 1
        n_changed = int(changed_rows.sum())
        children.append(dcc.Markdown(
            f"Float has been re-configured. **{n_changed} parameters** differ "
            f"across the {n_missions} mission records."
        ))

        # Wide table — show as DataTable
        df_cfg = cfg_table.reset_index().rename(columns={"index": "Parameter"})
        # Format numeric columns to fewer decimals; replace NaN with empty string
        for col in df_cfg.columns:
            if col == "Parameter":
                continue
            df_cfg[col] = df_cfg[col].apply(
                lambda v: "" if pd.isna(v) else f"{v:g}"
            )

        children.append(dash_table.DataTable(
            data=df_cfg.to_dict("records"),
            columns=[{"name": c, "id": c} for c in df_cfg.columns],
            style_cell={"fontSize": "12px", "padding": "6px",
                        "fontFamily": "system-ui, sans-serif"},
            style_header={"fontWeight": "600", "backgroundColor": "#f0f0f0"},
            style_table={"overflowX": "auto", "marginTop": "8px"},
            page_size=30,
        ))

        if changed_rows.any():
            children.append(html.Div("Parameters that changed:",
                                     style={"fontWeight": "600",
                                            "marginTop": "12px"}))
            change_lines = []
            for param in cfg_table.index[changed_rows]:
                vals = cfg_table.loc[param].dropna().unique()
                meaning = ah._config_meaning(param)
                m_part = f"  ({meaning})" if meaning else ""
                change_lines.append(
                    f"- `{param}`{m_part}: " +
                    " → ".join(f"{v:g}" for v in vals)
                )
            children.append(dcc.Markdown("\n".join(change_lines)))

    return html.Div(children)


# ══════════════════════════════════════════════════════════════════════════════
# Tab assembly
# ══════════════════════════════════════════════════════════════════════════════
def build_tab_metadata(meta, prof, sprof, wmo):
    if meta is None:
        return html.Div("meta.nc not loaded.",
                        style={"color": "#a00", "padding": "30px"})

    return html.Div([
        _expander(
            "Argo project information",
            section_argo_project_info(meta, prof),
            caption="Float identity, project, PI, and operating institution.",
        ),
        _expander(
            "Platform information",
            section_platform_info(meta),
            caption="Hardware identity: battery, controller board, firmware, "
                    "transmission.",
        ),
        _expander(
            "Deployment information",
            card_deployment(meta),
            caption="When, where, and from what platform the float was deployed.",
        ),
        _expander(
            "Sensors",
            section_sensors_table(meta),
            caption="Per-parameter sensor maker, model, accuracy, and resolution.",
        ),
        _expander(
            "Factory calibration",
            section_factory_calibration(meta),
            caption="Calibration applied at the factory, before deployment. "
                    "These coefficients convert raw sensor output into physical "
                    "units in the real-time pipeline.",
        ),
        _expander(
            "Configuration parameters",
            section_configuration(meta),
            caption="Launch configuration is set once at deployment; mission "
                    "configuration is the active mission and can in principle "
                    "be changed remotely.",
        ),
    ])
