"""
argo_monitor.py — Argo Float Monitoring Dashboard
==================================================
Streamlit dashboard for float health and data delivery monitoring.
Built as a demonstration of PMEL-style fleet monitoring capability.

Usage:
    pip install streamlit xarray netcdf4 plotly scipy pandas numpy
    streamlit run argo_monitor.py

Author: Youran Li  |  youranli001 @ github

Tab structure (Batches A + B complete):
    1. Main Information       — map + 5 text cards
    2. Technical Details      — sensors + 6-panel engineering telemetry
    3. Profiles & Sections    — section plots + overlay grid + single-cycle explorer
    4. QC & Processing        — per-parameter QC section + QC-colored overlay
    5. Data Delivery          — variables table + delay scatter + DM eligibility + velocity QC
    6. BGC Time Series        — trajectory time series + per-cycle BGC profiles
"""

import os
import warnings
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
import xarray as xr
# (streamlit import removed — argo_helpers is a pure-function module)
import plotly.graph_objects as go
import plotly.express as px
import plotly.colors as pc
from plotly.subplots import make_subplots
from scipy import stats
from scipy.interpolate import interp1d

warnings.filterwarnings("ignore")


# (Streamlit page_config and custom CSS removed)



# ── GDAC download helpers (HTTPS, multi-mirror) ───────────────────────────────
# We use HTTPS instead of the historical FTP endpoint because:
#   1. Cloud platforms (Render, Heroku, etc.) heavily restrict outbound FTP —
#      port 21 is sometimes allowed, but FTP passive-mode data transfer needs
#      a range of dynamic high ports that egress firewalls almost always block.
#   2. The IFREMER GDAC HTTPS server (data-argo.ifremer.fr) serves the exact
#      same directory structure as the FTP server. argopy (the official Argo
#      Python library) defaults to HTTPS for the same reason.
#
# Both Argo GDAC mirrors are tried in order — if IFREMER (France) is unreachable
# from the deployment region, US GODAE serves the same data.
import ssl
import urllib.request
import urllib.error

# On Windows + Anaconda Python, the default urllib SSL context cannot find a
# trusted-CA bundle and HTTPS verification fails with "unable to get local
# issuer certificate". The certifi package ships Mozilla's CA bundle and
# fixes this. On Linux/Mac with system Python this isn't strictly necessary,
# but using certifi is harmless and gives consistent behavior across platforms.
try:
    import certifi
    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    # Fall back to default if certifi isn't installed (e.g. older deploys).
    _SSL_CONTEXT = ssl.create_default_context()


GDAC_MIRRORS = [
    # (label, root URL where /dac/<dac>/<wmo>/<file>.nc lives)
    ("IFREMER",  "https://data-argo.ifremer.fr/dac"),
    ("US GODAE", "https://usgodae.org/ftp/outgoing/argo/dac"),
]
DAC_ORDER  = ["aoml", "coriolis", "pmel", "meds", "nmdis",
               "incois", "kordi", "bodc", "csio", "kma", "jma"]
FLOAT_FILES = ["_prof.nc", "_Sprof.nc", "_meta.nc", "_tech.nc",
               "_Dtraj.nc", "_Rtraj.nc"]
CACHE_DIR  = os.path.join(os.path.expanduser("~"), ".argo_cache")

# Browser-like User-Agent — some servers reject default urllib UA.
_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; argo-float-monitor-dash/1.0; "
        "+https://github.com/youranli001/argo-float-monitor-dash)"
    )
}


def _http_url_exists(url: str, timeout: int = 10) -> bool:
    """HEAD probe: returns True iff the URL is reachable (HTTP 200)."""
    req = urllib.request.Request(url, method="HEAD", headers=_HTTP_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout,
                                    context=_SSL_CONTEXT) as resp:
            return resp.status == 200
    except urllib.error.HTTPError:
        return False
    except Exception:
        return False


# (removed @st.cache_data decorator)
def find_dac_ftp(wmo: str) -> str | None:
    """Probe each DAC's HTTPS directory to locate the float.

    The function name is kept for backwards compatibility; the implementation
    no longer uses FTP. Tries IFREMER first, then US GODAE if IFREMER is
    unreachable. Returns the DAC code (e.g. 'aoml') or None.
    """
    for mirror_label, mirror_root in GDAC_MIRRORS:
        for dac in DAC_ORDER:
            # Probe by HEAD-requesting the meta file specifically — this is
            # the smallest standard file and is guaranteed to exist for every
            # active float.
            url = f"{mirror_root}/{dac}/{wmo}/{wmo}_meta.nc"
            if _http_url_exists(url):
                return dac
    return None


def _download_one(url: str, local_path: str, timeout: int = 120) -> None:
    """Download a single file via HTTPS, streaming to disk in chunks.

    Raises urllib.error.HTTPError or other exceptions on failure.
    """
    req = urllib.request.Request(url, headers=_HTTP_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout,
                                context=_SSL_CONTEXT) as resp, \
         open(local_path, "wb") as f:
        while True:
            chunk = resp.read(256 * 1024)  # 256 KB chunks
            if not chunk:
                break
            f.write(chunk)


def download_float_files(wmo: str, dac: str, dest: str,
                         progress_callback=None) -> list[str]:
    """Download all standard NetCDF files for a float via HTTPS.

    Tries each GDAC mirror in turn for each file. progress_callback is an
    optional callable(fraction, text) for progress updates.

    Per-file failures (404, etc.) are recorded in the returned list, not
    raised. RuntimeError is raised only if every file fails on every mirror.
    """
    os.makedirs(dest, exist_ok=True)
    saved = []
    n = len(FLOAT_FILES)
    any_success = False

    for i, suffix in enumerate(FLOAT_FILES):
        fname = wmo + suffix
        local = os.path.join(dest, fname)

        if progress_callback is not None:
            progress_callback((i + 1) / n, fname)

        if os.path.exists(local):
            saved.append(fname + " (cached)")
            any_success = True
            continue

        # Try each mirror until one succeeds.
        last_err = None
        downloaded = False
        for mirror_label, mirror_root in GDAC_MIRRORS:
            url = f"{mirror_root}/{dac}/{wmo}/{fname}"
            try:
                _download_one(url, local)
                saved.append(fname)
                any_success = True
                downloaded = True
                break
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    # File genuinely doesn't exist for this float (e.g. a Core
                    # float won't have _Sprof.nc). No point trying other
                    # mirrors — they have the same content.
                    last_err = "(not found)"
                    break
                last_err = f"(HTTP {e.code} from {mirror_label})"
                # Continue to next mirror
            except Exception as e:
                last_err = f"(error from {mirror_label}: {e})"
                # Continue to next mirror

            # Clean up partial download before trying the next mirror
            if os.path.exists(local):
                try:
                    os.remove(local)
                except Exception:
                    pass

        if not downloaded:
            saved.append(f"{fname} {last_err}")

    if not any_success:
        raise RuntimeError(
            f"Could not download any files for float {wmo} from any GDAC "
            f"mirror. Both IFREMER and US GODAE may be unreachable from this "
            f"environment."
        )
    return saved


# (Streamlit sidebar and GDAC fetch trigger removed —
#  Dash app handles UI input via dcc.Input + callbacks instead.)



# ── Constants & helpers ────────────────────────────────────────────────────────
FILL   = 99999.0
JREF   = pd.Timestamp("1950-01-01")
C_BLUE = "#0077B6"
C_RED  = "#e63946"
C_GRN  = "#2dc653"
C_ORG  = "#f77f00"
C_PUR  = "#6a0dad"


def decode_bytes(arr: np.ndarray) -> np.ndarray:
    """Decode 1-D or 2-D bytes/str array → 1-D numpy str array."""
    if arr.ndim == 2:
        return np.array(
            ["".join(c.decode() if isinstance(c, bytes) else c for c in row).strip()
             for row in arr]
        )
    return np.array(
        [x.decode().strip() if isinstance(x, bytes) else str(x).strip()
         for x in arr]
    )


def mask_fill(arr) -> np.ndarray:
    """Replace fill values (≥ 99999) with NaN."""
    a = np.array(arr, dtype=float)
    a[a >= FILL] = np.nan
    return a


def juld_to_dates(arr) -> list:
    """Julian days (days since 1950-01-01) → list of pd.Timestamp / pd.NaT."""
    arr = np.array(arr, dtype=float)
    out = []
    for j in arr:
        if np.isnan(j) or j >= FILL:
            out.append(pd.NaT)
        else:
            out.append(JREF + pd.Timedelta(days=float(j)))
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# ── BATCH A: NEW HELPERS ─────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

def d(x):
    """Decode single bytes/str/scalar to clean string. Robust to 0-d arrays."""
    if isinstance(x, np.ndarray) and x.ndim == 0:
        x = x.item()
    if isinstance(x, bytes):
        return x.decode().strip()
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return ''
    return str(x).strip()


def parse_argo_date(s):
    """'20221220234800' (YYYYMMDDHHMMSS) → datetime, '' → None."""
    s = d(s)
    if not s or s in ('n/a', 'nan'):
        return None
    try:
        return datetime.strptime(s[:14], '%Y%m%d%H%M%S')
    except ValueError:
        return None


def juld_to_dt(juld_val, ref=datetime(1950, 1, 1)):
    """Single JULD float → datetime. Returns None on FillValue."""
    if np.isnan(juld_val) or juld_val > 999990:
        return None
    return ref + timedelta(days=float(juld_val))


def decode_qc(qc_arr):
    """QC bytes → int array. Empty/missing entries become 9."""
    out = np.full(qc_arr.shape, 9, dtype=np.int8)
    for idx in np.ndindex(qc_arr.shape):
        v = qc_arr[idx]
        s = v.decode().strip() if isinstance(v, bytes) else str(v).strip()
        if s.isdigit():
            out[idx] = int(s)
    return out


def get_best(ds, param):
    """Return PARAM_ADJUSTED, falling back to PARAM where adjusted is missing."""
    adj_name = f'{param}_ADJUSTED'
    if adj_name in ds:
        adj = mask_fill(ds[adj_name].values)
        if param in ds:
            raw = mask_fill(ds[param].values)
            adj = np.where(np.isnan(adj), raw, adj)
        return adj
    if param in ds:
        return mask_fill(ds[param].values)
    return None


def get_qc(ds, param):
    """QC array (N_PROF, N_LEVELS) as int. Prefers ADJUSTED_QC."""
    for name in (f'{param}_ADJUSTED_QC', f'{param}_QC'):
        if name in ds:
            return decode_qc(ds[name].values)
    return None


def get_data_mode_per_param(ds, param):
    """Per-parameter data-mode array (length N_PROF)."""
    if 'PARAMETER_DATA_MODE' in ds and 'STATION_PARAMETERS' in ds:
        # Use first profile's STATION_PARAMETERS to find the param index
        sp_row = ds['STATION_PARAMETERS'].values[0]
        sp_str = []
        for s in sp_row:
            if isinstance(s, bytes):
                sp_str.append(s.decode().strip())
            elif isinstance(s, np.ndarray):
                try:
                    sp_str.append(b''.join(s.tolist()).decode().strip())
                except Exception:
                    sp_str.append(str(s).strip())
            else:
                sp_str.append(str(s).strip())
        try:
            idx = sp_str.index(param)
        except ValueError:
            return np.array([' '] * ds.sizes['N_PROF'])

        pdm = ds['PARAMETER_DATA_MODE'].values  # (N_PROF, N_PARAM)
        modes = np.array([
            (m.decode().strip() if isinstance(m, bytes) else str(m).strip())[:1] or ' '
            for m in pdm[:, idx]
        ])
        return modes

    if 'DATA_MODE' in ds:
        return decode_bytes(ds['DATA_MODE'].values)

    return np.array([' '] * ds.sizes['N_PROF'])


def add_colorbar_legend(fig, items, x_left, y_top, total_height,
                        title=None, block_width=0.025, label_offset=0.012):
    """Manual colorbar-style vertical legend at fixed paper x-position.

    items: top-to-bottom list of (label, color) tuples.
    x_left: paper-x of the LEFT edge of the color block column.
    y_top:  paper-y of the TOP edge.
    total_height: paper height of the entire color column.
    """
    n = len(items)
    block_h = total_height / n

    if title:
        fig.add_annotation(
            xref='paper', yref='paper',
            x=x_left + block_width / 2, y=y_top + 0.012,
            text=title, showarrow=False,
            xanchor='center', yanchor='bottom',
            font=dict(size=11, color='black'),
        )

    for i, (label, color) in enumerate(items):
        y0 = y_top - (i + 1) * block_h
        y1 = y_top - i * block_h
        fig.add_shape(
            type='rect', xref='paper', yref='paper',
            x0=x_left, x1=x_left + block_width,
            y0=y0, y1=y1,
            fillcolor=color,
            line=dict(width=0),
        )
        fig.add_annotation(
            xref='paper', yref='paper',
            x=x_left + block_width + label_offset,
            y=(y0 + y1) / 2,
            text=label, showarrow=False,
            xanchor='left', yanchor='middle',
            font=dict(size=11),
        )

    # Outer border for clean frame
    fig.add_shape(
        type='rect', xref='paper', yref='paper',
        x0=x_left, x1=x_left + block_width,
        y0=y_top - total_height, y1=y_top,
        fillcolor='rgba(0,0,0,0)',
        line=dict(width=0.6, color='#888'),
    )


def interp_to_grid(pres_2d, vals_2d, pres_grid):
    """Interpolate each profile onto a regular pressure grid.
       Returns (n_profiles, n_pres_grid) array, NaN outside profile range."""
    n_prof = pres_2d.shape[0]
    out = np.full((n_prof, len(pres_grid)), np.nan)
    for i in range(n_prof):
        p, v = pres_2d[i], vals_2d[i]
        m = ~np.isnan(p) & ~np.isnan(v)
        if m.sum() < 2:
            continue
        pv, vv = p[m], v[m]
        order = np.argsort(pv)
        pv, vv = pv[order], vv[order]
        _, ui = np.unique(pv, return_index=True)
        pv, vv = pv[ui], vv[ui]
        f = interp1d(pv, vv, bounds_error=False, fill_value=np.nan)
        out[i, :] = f(pres_grid)
    return out


# DAC code → (country, agency) — Argo Reference Table 4
DAC_INFO = {
    'AO': ('UNITED STATES',  'AOML'),
    'BO': ('UNITED KINGDOM', 'BODC'),
    'CS': ('AUSTRALIA',      'CSIRO'),
    'IF': ('FRANCE',         'Coriolis (Ifremer)'),
    'IN': ('INDIA',          'INCOIS'),
    'JA': ('JAPAN',          'JMA'),
    'KM': ('SOUTH KOREA',    'KMA'),
    'KO': ('SOUTH KOREA',    'KORDI'),
    'ME': ('CANADA',         'MEDS'),
    'NM': ('CHINA',          'NMDIS'),
}


def derive_country(dac_code):
    return DAC_INFO.get(dac_code, (dac_code, '?'))[0]


def derive_networks(meta):
    """Infer Argo programs from project + parameter list."""
    project = d(meta['PROJECT_NAME'].values).upper()
    params_list = [d(p) for p in meta['PARAMETER'].values]
    bgc_set = {'DOXY', 'CHLA', 'BBP700', 'NITRATE', 'PH_IN_SITU_TOTAL', 'CDOM'}
    is_bgc = bool(set(params_list) & bgc_set)
    nets = ['Argo', 'Global Core mission']
    nets.append('Argo BGC' if is_bgc else 'Argo Core')
    if 'GO-BGC' in project:
        nets.append('GO-BGC')
    if 'SOCCOM' in project:
        nets.append('SOCCOM')
    if 'EUROARGO' in project or 'EURO-ARGO' in project:
        nets.append('Euro-Argo')
    return nets


def derive_status(meta, prof):
    """3-tier: ACTIVE (≤30d) / INACTIVE (≤365d) / PRESUMED DEAD (>365d).
       If END_MISSION_DATE is set, status is CLOSED."""
    end_dt = parse_argo_date(meta['END_MISSION_DATE'].values)
    end_status = d(meta['END_MISSION_STATUS'].values)
    if end_dt is not None:
        if end_status == 'T':
            return 'CLOSED (no transmissions)'
        if end_status == 'R':
            return 'CLOSED (retrieved)'
        return 'CLOSED'
    last_dt = juld_to_dt(float(prof['JULD'].values[-1]))
    if last_dt is None:
        return 'UNKNOWN'
    days = (datetime.utcnow() - last_dt).days
    if days <= 30:
        return f'ACTIVE  (last profile {days} d ago)'
    if days <= 365:
        return f'INACTIVE  (last profile {days} d ago — may resume)'
    return f'PRESUMED DEAD  (silent for {days} d)'


def wigos_id(wmo):
    return f'0-22000-0-{wmo}'


def fmt_date(dt, with_ago=False):
    if dt is None:
        return 'n/a'
    s = dt.strftime('%Y-%m-%d %H:%M:%S')
    if with_ago:
        days = (datetime.utcnow() - dt).days
        if days >= 0:
            s += f'  ({days} days ago)'
    return s


def get_config(meta, key):
    """Look up a launch-config parameter by name. Returns float or None."""
    for n, v in zip(meta['LAUNCH_CONFIG_PARAMETER_NAME'].values,
                    meta['LAUNCH_CONFIG_PARAMETER_VALUE'].values):
        if d(n) == key:
            return float(v) if v < 99999 else None
    return None


def measured_cycle_time(prof):
    """Median spacing between consecutive profile timestamps, in hours."""
    juld = prof['JULD'].values
    juld = juld[~np.isnan(juld) & (juld < 999990)]
    if len(juld) < 2:
        return None
    diffs_hours = np.diff(np.sort(juld)) * 24
    return float(np.median(diffs_hours))


def cycle_time_line(meta, prof):
    cfg      = get_config(meta, 'CONFIG_DownTime_hours')
    measured = measured_cycle_time(prof)
    parts = []
    if cfg is not None:
        parts.append(f'configured = {cfg:.1f} h ({cfg/24:.1f} d)')
    if measured is not None:
        parts.append(f'measured ≈ {measured:.1f} h ({measured/24:.2f} d)')
    return ' | '.join(parts) if parts else 'n/a'


def last_cycle_surface_bottom(ds):
    """Shallowest & deepest valid (P, T, S) of the last cycle."""
    if ds is None:
        return None, None
    i = ds.sizes['N_PROF'] - 1
    pres_name = 'PRES_ADJUSTED' if 'PRES_ADJUSTED' in ds else 'PRES'
    temp_name = 'TEMP_ADJUSTED' if 'TEMP_ADJUSTED' in ds else 'TEMP'
    psal_name = 'PSAL_ADJUSTED' if 'PSAL_ADJUSTED' in ds else 'PSAL'
    pres = ds[pres_name].values[i, :]
    temp = ds[temp_name].values[i, :]
    psal = ds[psal_name].values[i, :]
    valid = (pres < 9999) & (temp < 9999) & (psal < 9999) & (pres > -9999)
    if not valid.any():
        return None, None
    p, t, s = pres[valid], temp[valid], psal[valid]
    surf = {'P': float(p[np.argmin(p)]), 'T': float(t[np.argmin(p)]),
            'S': float(s[np.argmin(p)])}
    bot  = {'P': float(p[np.argmax(p)]), 'T': float(t[np.argmax(p)]),
            'S': float(s[np.argmax(p)])}
    return surf, bot


# Color schemes
QC_COLOR_MAP = {
    1: '#2ca02c', 2: '#ffdd00', 3: '#ff8c00', 4: '#d62728',
    5: '#90ee90', 8: '#ff69b4', 9: '#bbbbbb', 0: '#dddddd',
}
QC_LEVELS = [0, 1, 2, 3, 4, 5, 8, 9]
DM_COLORS = {'D': '#1f77b4', 'A': '#ff7f0e', 'R': '#d62728', ' ': '#cccccc'}

# ── BATCH B: shared BGC parameter colors (used in Tab 3 single-cycle profiles
#    AND Tab 6 trajectory time series + per-cycle BGC profiles).
#    Keeping these consistent means "green = O₂" everywhere in the dashboard.
BGC_COLORS = {
    'DOXY':              C_GRN,       # green
    'CHLA':              '#2a9d8f',   # teal
    'BBP700':            C_PUR,       # purple
    'NITRATE':           C_ORG,       # orange
    'PH_IN_SITU_TOTAL':  C_RED,       # red
    'PPOX_DOXY':         C_GRN,       # green (paired with DOXY)
}
BGC_UNITS = {
    'DOXY':             'µmol/kg',
    'CHLA':             'mg/m³',
    'BBP700':           'm⁻¹',
    'NITRATE':          'µmol/kg',
    'PH_IN_SITU_TOTAL': '',
    'PPOX_DOXY':        'mbar',
}
BGC_LABELS = {
    'DOXY':             'O₂',
    'CHLA':             'Chl-a',
    'BBP700':           'BBP700',
    'NITRATE':          'NO₃⁻',
    'PH_IN_SITU_TOTAL': 'pH',
    'PPOX_DOXY':        'pO₂ (in-air)',
}

# Parameter catalog: (name, label, units, plotly-cmap, contour-step)
ALL_PARAMS = [
    ('TEMP',             'Temperature',     '°C',       'RdYlBu_r', 2.0),
    ('PSAL',             'Salinity',        'PSU',      'Viridis',  0.05),
    ('DOXY',             'Dissolved O₂',    'µmol/kg',  'Turbo',    None),
    ('CHLA',             'Chlorophyll-a',   'mg/m³',    'YlGn',     None),
    ('NITRATE',          'Nitrate',         'µmol/kg',  'Plasma',   None),
    ('PH_IN_SITU_TOTAL', 'pH',              '',         'RdYlBu_r', None),
    ('BBP700',           'BBP at 700 nm',   'm⁻¹',      'Magma',    None),
]


# ═══════════════════════════════════════════════════════════════════════════════
# ── BATCH A: TEXT-CARD RENDERERS ─────────────────────────────────────────────
# Each returns nothing, calls st.markdown / st.dataframe directly.
# ═══════════════════════════════════════════════════════════════════════════════


# ─────────────────────────────────────────────────────────────────────────────
# Streamlit render_* helpers (text-card builders) removed.
# Their logic is reimplemented in tabs/tab_*.py modules.
# ─────────────────────────────────────────────────────────────────────────────

def _config_meaning(param_name):
    """Look up plain-English meaning, with prefix-match fallback."""
    if param_name in CONFIG_MEANINGS:
        return CONFIG_MEANINGS[param_name]
    # Fallback: strip trailing units suffix and try again
    for suffix in ('_dbar', '_hours', '_degC', '_NUMBER', '_COUNT', '_seconds'):
        if param_name.endswith(suffix):
            base = param_name[:-len(suffix)]
            for k, v in CONFIG_MEANINGS.items():
                if k.startswith(base):
                    return v
    return ''

# ═══════════════════════════════════════════════════════════════════════════════
# ── BATCH A: VISUALIZATION BUILDERS (plotly) ─────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

def make_section_plot(ds, param, label, units, cmap, contour_step):
    """Plotly section plot: depth–time heatmap + (optional) contours
       + DATA_MODE strip below + cycle-number ticks on top axis.
       Returns a plotly Figure or None if the parameter is missing."""

    vals = get_best(ds, param)
    if vals is None:
        return None

    qc = get_qc(ds, param)
    if qc is not None:
        keep = np.isin(qc, [1, 2, 5, 8])
        vals = np.where(keep, vals, np.nan)

    pres_2d = mask_fill(ds['PRES_ADJUSTED'].values if 'PRES_ADJUSTED' in ds
                        else ds['PRES'].values)
    if not np.isfinite(np.nanmax(pres_2d)):
        return None
    pres_max  = float(np.nanmax(pres_2d))
    pres_grid = np.arange(0, np.ceil(pres_max / 10) * 10 + 1, 5.0)

    grid = interp_to_grid(pres_2d, vals, pres_grid)  # (N_PROF, N_PRES)
    z = grid.T  # (N_PRES, N_PROF) for plotly: y=pres, x=date

    dates  = juld_to_dates(ds['JULD'].values)
    cycles = ds['CYCLE_NUMBER'].values.astype(int)
    modes  = get_data_mode_per_param(ds, param)

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.92, 0.08],
        vertical_spacing=0.04,
    )

    # Heatmap
    units_part = f' ({units})' if units else ''
    fig.add_trace(go.Heatmap(
        x=dates, y=pres_grid, z=z,
        colorscale=cmap,
        colorbar=dict(title=f'{label}<br>{units_part}',
                      len=0.85, y=0.55, yanchor='middle', thickness=12),
        hovertemplate=('Date: %{x|%Y-%m-%d}<br>'
                       'Pressure: %{y:.0f} dbar<br>'
                       f'{label}: %{{z:.3f}}{units_part}<extra></extra>'),
        zsmooth=False,
    ), row=1, col=1)

    # Contour overlay (TEMP / PSAL only)
    if contour_step is not None:
        with np.errstate(invalid='ignore'):
            zmin = np.nanmin(z); zmax = np.nanmax(z)
        if np.isfinite(zmin) and np.isfinite(zmax) and zmax > zmin:
            fig.add_trace(go.Contour(
                x=dates, y=pres_grid, z=z,
                contours=dict(
                    coloring='lines',
                    showlines=True,
                    showlabels=False,
                    start=float(np.floor(zmin / contour_step) * contour_step),
                    end=float(zmax),
                    size=float(contour_step),
                ),
                colorscale=[[0, 'rgba(0,0,0,1)'], [1, 'rgba(0,0,0,1)']],
                line=dict(width=0.5),
                showscale=False,
                hoverinfo='skip',
            ), row=1, col=1)

    # DATA_MODE strip
    mode_colors = [DM_COLORS.get(m, '#cccccc') for m in modes]
    mode_text = [f'Cycle {c} — DATA_MODE: {m}' for c, m in zip(cycles, modes)]
    fig.add_trace(go.Scatter(
        x=dates, y=[0] * len(dates),
        mode='markers',
        marker=dict(color=mode_colors, size=14, symbol='square'),
        text=mode_text,
        hovertemplate='%{text}<extra></extra>',
        showlegend=False,
    ), row=2, col=1)

    fig.update_yaxes(title_text='Pressure (dbar)', autorange='reversed',
                     row=1, col=1)
    fig.update_yaxes(visible=False, range=[-0.5, 0.5], row=2, col=1)

    # Top x-axis: cycle numbers (sparse)
    n_show = min(6, len(cycles))
    if n_show > 1:
        tick_idx = np.linspace(0, len(cycles) - 1, n_show, dtype=int)
        fig.update_xaxes(
            side='top',
            tickmode='array',
            tickvals=[dates[i] for i in tick_idx],
            ticktext=[str(cycles[i]) for i in tick_idx],
            title_text='Cycle number',
            row=1, col=1,
        )
    fig.update_xaxes(title_text='Date', row=2, col=1)

    fig.update_layout(
        title=f'{label}{units_part} — Section',
        height=540,
        margin=dict(t=70, b=50, l=70, r=140),
    )
    return fig


def make_overlay_grid(ds, params, ncols=3):
    """Plotly N-column grid of overlaid profiles (T-S diagram + parameters),
       lines colored by cycle number with a shared colorbar.
       ── MOD 2: ncols configurable (2 / 3 / 4)."""

    n_panels = len(params) + 1
    nrows = int(np.ceil(n_panels / ncols))

    titles = ['T–S Diagram'] + [f'Overlaid {p[0]}' for p in params]
    titles += [''] * (nrows * ncols - len(titles))

    fig = make_subplots(
        rows=nrows, cols=ncols,
        subplot_titles=titles,
        # ── BATCH A FIX: more spacing so titles don't collide with axis labels
        horizontal_spacing=0.12,
        vertical_spacing=0.18,
    )

    n_prof  = ds.sizes['N_PROF']
    cycles  = ds['CYCLE_NUMBER'].values.astype(int)
    cmin    = int(cycles.min()); cmax = int(cycles.max())
    pres_2d = mask_fill(ds['PRES_ADJUSTED'].values if 'PRES_ADJUSTED' in ds
                        else ds['PRES'].values)

    colors = pc.sample_colorscale('RdYlBu_r', np.linspace(0, 1, n_prof))

    # Panel (1,1): T-S diagram
    temp = get_best(ds, 'TEMP')
    psal = get_best(ds, 'PSAL')
    if temp is not None and psal is not None:
        for i in range(n_prof):
            m = ~np.isnan(temp[i]) & ~np.isnan(psal[i])
            if m.any():
                fig.add_trace(go.Scatter(
                    x=psal[i][m], y=temp[i][m],
                    mode='lines',
                    line=dict(width=0.7, color=colors[i]),
                    opacity=0.4,
                    showlegend=False,
                    hovertemplate=(f'Cycle {cycles[i]}<br>'
                                   'S=%{x:.3f}<br>T=%{y:.2f}°C<extra></extra>'),
                ), row=1, col=1)
    fig.update_xaxes(title_text='Salinity (PSU)', row=1, col=1)
    fig.update_yaxes(title_text='Temperature (°C)', row=1, col=1)

    # Other panels
    for k, (name, label, units, _, _) in enumerate(params, start=1):
        row = k // ncols + 1
        col = k % ncols + 1

        vals = get_best(ds, name)
        if vals is None:
            continue
        for i in range(n_prof):
            p = pres_2d[i]; v = vals[i]
            m = ~np.isnan(p) & ~np.isnan(v)
            if m.any():
                fig.add_trace(go.Scatter(
                    x=v[m], y=p[m],
                    mode='lines',
                    line=dict(width=0.7, color=colors[i]),
                    opacity=0.5,
                    showlegend=False,
                    hovertemplate=(f'Cycle {cycles[i]}<br>'
                                   f'{name}=%{{x:.4g}}<br>'
                                   'P=%{y:.0f} dbar<extra></extra>'),
                ), row=row, col=col)

        xlabel = f'{label} ({units})' if units else label
        fig.update_xaxes(title_text=xlabel, row=row, col=col)
        fig.update_yaxes(title_text='Pressure (dbar)', autorange='reversed',
                         row=row, col=col)

    # Phantom trace for cycle-number colorbar
    fig.add_trace(go.Scatter(
        x=[None], y=[None],
        mode='markers',
        marker=dict(
            color=[cmin, cmax],
            colorscale='RdYlBu_r',
            cmin=cmin, cmax=cmax,
            colorbar=dict(title='Cycle',
                          x=1.02, len=0.6, thickness=12),
            showscale=True,
        ),
        showlegend=False,
        hoverinfo='skip',
    ), row=1, col=1)

    fig.update_layout(
        # ── BATCH A FIX: taller rows so titles + axis labels both fit
        height=460 * nrows,
        margin=dict(t=40, r=170, b=60, l=70),
    )
    return fig


# ── Data loaders ───────────────────────────────────────────────────────────────
# (removed @st.cache_data decorator)
def load_datasets(data_dir: str, wmo: str) -> dict:
    D = data_dir.rstrip(r"\/") + os.sep
    suffixes = {
        "prof":  "_prof.nc",
        "sprof": "_Sprof.nc",
        "meta":  "_meta.nc",   # ── BATCH A: load meta for text cards
        "tech":  "_tech.nc",
        "dtraj": "_Dtraj.nc",
        "rtraj": "_Rtraj.nc",
    }
    ds = {}
    for key, suffix in suffixes.items():
        path = D + wmo + suffix
        ds[key] = xr.open_dataset(path, decode_times=False) if os.path.exists(path) else None
    return ds


# (removed @st.cache_data decorator)
def parse_tech(data_dir: str, wmo: str) -> pd.DataFrame | None:
    """Parse tech.nc long format → tidy DataFrame with columns [cycle, param, value]."""
    path = data_dir.rstrip(r"\/") + os.sep + wmo + "_tech.nc"
    if not os.path.exists(path):
        return None
    tech = xr.open_dataset(path, decode_times=False)
    names  = decode_bytes(tech["TECHNICAL_PARAMETER_NAME"].values)
    values = decode_bytes(tech["TECHNICAL_PARAMETER_VALUE"].values)
    cycles = tech["CYCLE_NUMBER"].values.astype(int)

    rows = []
    for name, val, cyc in zip(names, values, cycles):
        try:
            fval = float(val)
        except (ValueError, TypeError):
            fval = np.nan
        rows.append({"cycle": cyc, "param": name, "value": fval, "raw": val})
    return pd.DataFrame(rows)


def get_param(df: pd.DataFrame, param: str):
    """Extract one tech parameter → (cycles_sorted, values_sorted)."""
    sub = df[df["param"] == param].dropna(subset=["value"]).sort_values("cycle")
    return sub["cycle"].values, sub["value"].values

# ─────────────────────────────────────────────────────────────────────────────
# Streamlit app body (data loading + tab rendering) removed.
# Data loading happens in app.py via load_datasets(data_dir, wmo).
# Tab content is built in tabs/tab_*.py modules.
# ─────────────────────────────────────────────────────────────────────────────
