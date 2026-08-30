"""
argo_storage.py — durable NetCDF cache backed by Amazon S3
============================================================
Sits between the Dash app and the GDAC mirrors.

Resolution order for every float file (prof/Sprof/meta/tech/Dtraj/Rtraj):

    1. local working directory   (ephemeral container disk, fastest)
    2. S3 bucket                 (durable, survives redeploys, same-region)
    3. GDAC mirror (IFREMER → US GODAE) → then written back to S3

If ARGO_S3_BUCKET is not set, S3 is skipped entirely and the app behaves
exactly like the original local-only version. That keeps `python app.py`
working on a laptop without AWS credentials.

Environment variables
---------------------
ARGO_S3_BUCKET      name of the cache bucket (unset → local-only mode)
ARGO_S3_PREFIX      key prefix inside the bucket (default "gdac-cache")
ARGO_CACHE_TTL_DAYS re-fetch from GDAC if the S3 copy is older than this
                    (default 7; set 0 to always trust the S3 copy)
AWS_REGION          picked up by boto3 (App Runner sets this automatically)
"""
from __future__ import annotations

import logging
import os
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

import argo_helpers as ah

log = logging.getLogger("argo_storage")

S3_BUCKET   = os.environ.get("ARGO_S3_BUCKET")
S3_PREFIX   = os.environ.get("ARGO_S3_PREFIX", "gdac-cache").strip("/")
TTL_DAYS    = int(os.environ.get("ARGO_CACHE_TTL_DAYS", "7"))

_s3 = None
if S3_BUCKET:
    try:
        import boto3
        _s3 = boto3.client("s3")
        log.info("S3 cache enabled: s3://%s/%s", S3_BUCKET, S3_PREFIX)
    except Exception as e:  # boto3 missing or no credentials
        log.warning("S3 cache disabled (%s); falling back to local-only", e)
        _s3 = None


def s3_enabled() -> bool:
    return _s3 is not None


def _key(wmo: str, fname: str) -> str:
    return f"{S3_PREFIX}/{wmo}/{fname}"


# ── S3 primitives ──────────────────────────────────────────────────────────────
def _s3_last_modified(key: str) -> datetime | None:
    """Return LastModified (UTC) if the object exists, else None."""
    try:
        head = _s3.head_object(Bucket=S3_BUCKET, Key=key)
        return head["LastModified"]
    except _s3.exceptions.ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            return None
        raise


def _s3_download(key: str, local: Path) -> None:
    local.parent.mkdir(parents=True, exist_ok=True)
    _s3.download_file(S3_BUCKET, key, str(local))


def _s3_upload(local: Path, key: str) -> None:
    _s3.upload_file(str(local), S3_BUCKET, key,
                    ExtraArgs={"ContentType": "application/x-netcdf"})


def _is_fresh(last_modified: datetime | None) -> bool:
    if last_modified is None:
        return False
    if TTL_DAYS <= 0:
        return True
    age = datetime.now(timezone.utc) - last_modified
    return age < timedelta(days=TTL_DAYS)


# ── GDAC primitive (reuses the mirror/404 logic from argo_helpers) ────────────
def _fetch_from_gdac(wmo: str, dac: str, fname: str, local: Path) -> str | None:
    """Download one file from the first mirror that has it.

    Returns None on success, or a short error tag ("(not found)", "(HTTP 503 ...)").
    """
    local.parent.mkdir(parents=True, exist_ok=True)
    last_err = None
    for mirror_label, mirror_root in ah.GDAC_MIRRORS:
        url = f"{mirror_root}/{dac}/{wmo}/{fname}"
        try:
            ah._download_one(url, str(local))
            return None
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return "(not found)"
            last_err = f"(HTTP {e.code} from {mirror_label})"
        except Exception as e:
            last_err = f"(error from {mirror_label}: {e})"
        if local.exists():
            try:
                local.unlink()
            except OSError:
                pass
    return last_err or "(unknown error)"


# ── Public API ────────────────────────────────────────────────────────────────
def fetch_float(wmo: str, dac: str, local_dir: str | Path,
                force_refresh: bool = False) -> list[str]:
    """Make all standard files for `wmo` available in `local_dir`.

    Returns a status list like the original download_float_files():
        ["5906551_prof.nc (local)", "5906551_meta.nc (s3)",
         "5906551_tech.nc", "5906551_Sprof.nc (not found)", ...]
    Raises RuntimeError if nothing could be obtained at all.
    """
    local_dir = Path(local_dir)
    local_dir.mkdir(parents=True, exist_ok=True)
    status: list[str] = []
    any_success = False

    for suffix in ah.FLOAT_FILES:
        fname = wmo + suffix
        local = local_dir / fname
        key = _key(wmo, fname)

        # 1. local working copy
        if local.exists() and not force_refresh:
            status.append(f"{fname} (local)")
            any_success = True
            continue

        # 2. S3 durable copy
        if s3_enabled() and not force_refresh:
            lm = _s3_last_modified(key)
            if _is_fresh(lm):
                try:
                    _s3_download(key, local)
                    status.append(f"{fname} (s3)")
                    any_success = True
                    continue
                except Exception as e:
                    log.warning("S3 download failed for %s: %s", key, e)

        # 3. GDAC
        err = _fetch_from_gdac(wmo, dac, fname, local)
        if err is None:
            status.append(fname)
            any_success = True
            if s3_enabled():
                try:
                    _s3_upload(local, key)
                except Exception as e:
                    log.warning("S3 upload failed for %s: %s", key, e)
        else:
            status.append(f"{fname} {err}")

    if not any_success:
        raise RuntimeError(
            f"Could not obtain any files for float {wmo} from local disk, "
            f"S3, or any GDAC mirror."
        )
    return status


def restore_from_s3(wmo: str, local_dir: str | Path) -> int:
    """Pull whatever S3 holds for `wmo` into `local_dir` (no GDAC calls).

    Used when a fresh container instance receives a request for a float that
    was downloaded by a previous instance. Returns number of files restored.
    """
    if not s3_enabled():
        return 0
    local_dir = Path(local_dir)
    n = 0
    for suffix in ah.FLOAT_FILES:
        fname = wmo + suffix
        local = local_dir / fname
        if local.exists():
            continue
        key = _key(wmo, fname)
        if _s3_last_modified(key) is not None:
            try:
                _s3_download(key, local)
                n += 1
            except Exception as e:
                log.warning("restore failed for %s: %s", key, e)
    return n


def list_cached_floats() -> list[str]:
    """WMO numbers that have at least one file in S3 (for a /floats endpoint)."""
    if not s3_enabled():
        return []
    wmos: set[str] = set()
    paginator = _s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=S3_PREFIX + "/",
                                   Delimiter="/"):
        for cp in page.get("CommonPrefixes", []):
            wmos.add(cp["Prefix"].rstrip("/").split("/")[-1])
    return sorted(wmos)
