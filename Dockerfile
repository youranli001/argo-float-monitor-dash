# ── Argo Float Monitor (Plotly Dash) — container image for AWS App Runner ─────
FROM python:3.12-slim

# netCDF4/xarray wheels are self-contained; only curl is needed for the
# container-level health check.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first so this layer is cached between code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Ephemeral working copy of NetCDF files; durable copy lives in S3.
ENV ARGO_DATA_DIR=/tmp/argo_data \
    PORT=8080 \
    PYTHONUNBUFFERED=1

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD curl -fsS http://localhost:8080/healthz || exit 1

# --timeout 600: a first-time GDAC download can take 1–3 minutes and runs
# inside a single Dash callback. --threads lets one worker serve other users'
# callbacks while a download is in flight.
CMD ["gunicorn", "app:server", \
     "--bind", "0.0.0.0:8080", \
     "--workers", "2", "--threads", "4", \
     "--timeout", "600", "--access-logfile", "-"]
