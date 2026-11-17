"""
Earth Engine connection for GeoClosure Explorer.

Uses the SAME secrets format as the ECDS app, so one Streamlit secrets block works for both:

    EE_PROJECT = "your-google-cloud-project-id"
    EE_SERVICE_ACCOUNT_JSON = '''{ ...entire service-account JSON key... }'''
    # or, instead of the JSON:
    # EE_SERVICE_ACCOUNT = "name@project.iam.gserviceaccount.com"
    # EE_PRIVATE_KEY = "-----BEGIN PRIVATE KEY-----\\n...\\n-----END PRIVATE KEY-----\\n"

Credential sources, tried in this order:
  1. Streamlit secrets, ECDS format (EE_SERVICE_ACCOUNT_JSON, or EE_SERVICE_ACCOUNT + EE_PRIVATE_KEY)
  2. Streamlit secrets, [gee_service_account] table (earlier GeoClosure format, still accepted)
  3. Service-account key file: env var GEE_SERVICE_ACCOUNT_FILE (or GOOGLE_APPLICATION_CREDENTIALS)
  4. Your own login (`earthengine authenticate`), with the project from EE_PROJECT (secrets or env)
     or GEE_PROJECT (env)

Error text shown to users is redacted (keys, tokens, URLs removed). Full detail goes to the server
log with a short diagnostic ID, as in ECDS.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field

TEST_POINT = (20.2050, 67.8380)  # lon, lat near Kiruna
TEST_BOX = (20.18, 67.82, 20.23, 67.85)

_LOG = logging.getLogger("geoclosure.earth_engine")
if not _LOG.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    _LOG.addHandler(_h)
_LOG.setLevel(logging.INFO)
_LOG.propagate = False


def safe_error(exc: Exception) -> str:
    """Remove private keys, tokens, credentials and URLs from an exception message."""
    m = str(exc).replace("\n", " ").replace("\r", " ")
    m = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", "[REDACTED KEY]", m, flags=re.I | re.S)
    m = re.sub(r"(?i)(authorization\s*[:=]\s*)?bearer\s+[^\s,;]+", "Authorization: Bearer [REDACTED]", m)
    m = re.sub(r"(?i)(access_token|refresh_token|id_token|api[_-]?key|private[_-]?key|authorization|credential|secret|password)"
               r"\s*[:=]\s*[^,\s}]+", r"\1=[REDACTED]", m)
    m = re.sub(r"https?://[^\s]+", "[URL REDACTED]", m)
    return m[:300]


def _log_failure(stage: str, exc: Exception) -> str:
    """Log full (redacted) detail server-side; return a short user-facing line with an ID."""
    incident = uuid.uuid4().hex[:10]
    _LOG.error("event=failed id=%s stage=%s exception=%s detail=%s", incident, stage, type(exc).__name__, safe_error(exc))
    return f"{type(exc).__name__} (diagnostic ID {incident}; see app logs)"


@dataclass
class GEEStatus:
    ok: bool
    method: str = "none"
    project: str | None = None
    message: str = ""
    attempts: list[str] = field(default_factory=list)


def _read_secrets() -> dict:
    try:
        import streamlit as st
        return {k: st.secrets[k] for k in st.secrets}
    except Exception:
        return {}


def _key_file():
    path = os.environ.get("GEE_SERVICE_ACCOUNT_FILE") or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not path:
        return None, None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh), path
    except Exception as e:
        return None, f"{os.path.basename(path)} ({type(e).__name__})"


def init_ee(project: str | None = None, secrets: dict | None = None) -> GEEStatus:
    """Initialise Earth Engine from the first credential source that works."""
    try:
        import ee
    except ImportError:
        return GEEStatus(False, message="The earthengine-api package is not installed.")

    s = _read_secrets() if secrets is None else dict(secrets)
    project = project or s.get("EE_PROJECT") or os.environ.get("EE_PROJECT") or os.environ.get("GEE_PROJECT")
    attempts: list[str] = []

    def _connect(info: dict, label: str) -> GEEStatus:
        missing = [k for k in ("client_email", "private_key") if not info.get(k)]
        if missing:
            raise ValueError(f"missing field(s): {', '.join(missing)}")
        proj = project or info.get("project") or info.get("project_id")
        creds = ee.ServiceAccountCredentials(info["client_email"], key_data=json.dumps(info))
        ee.Initialize(creds, project=proj)
        _LOG.info("event=initialized method=%s project=%s", label, proj)
        return GEEStatus(True, label, proj, f"Connected as {info['client_email']}", attempts)

    # 1. ECDS-format secrets
    if s.get("EE_SERVICE_ACCOUNT_JSON"):
        try:
            return _connect(json.loads(s["EE_SERVICE_ACCOUNT_JSON"]), "secrets (EE_SERVICE_ACCOUNT_JSON)")
        except json.JSONDecodeError:
            attempts.append("EE_SERVICE_ACCOUNT_JSON: not valid JSON — paste the key file verbatim between ''' quotes")
        except Exception as e:
            attempts.append("EE_SERVICE_ACCOUNT_JSON: " + _log_failure("init secrets json", e))
    elif s.get("EE_SERVICE_ACCOUNT") and s.get("EE_PRIVATE_KEY"):
        info = {"type": "service_account", "client_email": s["EE_SERVICE_ACCOUNT"],
                "private_key": s["EE_PRIVATE_KEY"].replace("\\n", "\n"),
                "token_uri": "https://oauth2.googleapis.com/token"}
        try:
            return _connect(info, "secrets (EE_SERVICE_ACCOUNT)")
        except Exception as e:
            attempts.append("EE_SERVICE_ACCOUNT + EE_PRIVATE_KEY: " + _log_failure("init secrets key", e))
    else:
        attempts.append("Secrets: no EE_SERVICE_ACCOUNT_JSON or EE_SERVICE_ACCOUNT/EE_PRIVATE_KEY")

    # 2. Earlier GeoClosure table format
    table = s.get("gee_service_account")
    if table:
        try:
            return _connect(dict(table), "secrets ([gee_service_account])")
        except Exception as e:
            attempts.append("[gee_service_account]: " + _log_failure("init secrets table", e))

    # 3. Key file
    info, path = _key_file()
    if info is None and path is None:
        attempts.append("Key file: GEE_SERVICE_ACCOUNT_FILE not set")
    elif info is None:
        attempts.append(f"Key file: couldn't read {path}")
    else:
        try:
            return _connect(info, "key file")
        except Exception as e:
            attempts.append("Key file: " + _log_failure("init key file", e))

    # 4. Own login
    if not project:
        attempts.append("Own login: no project set (EE_PROJECT in secrets, or GEE_PROJECT env var)")
    else:
        try:
            ee.Initialize(project=project)
            _LOG.info("event=initialized method=user project=%s", project)
            return GEEStatus(True, "own login", project, f"Connected with your own login to {project}", attempts)
        except Exception as e:
            attempts.append("Own login: " + _log_failure("init user", e))

    return GEEStatus(False, "none", project, "Couldn't connect to Earth Engine.", attempts)


def connection_test() -> dict:
    """One small real request per dataset the app uses. Errors are redacted."""
    import ee

    point = ee.Geometry.Point(*TEST_POINT)
    box = ee.Geometry.Rectangle(list(TEST_BOX))
    checks = {
        "AW3D30 elevation at test point (m)": lambda: ee.ImageCollection("JAXA/ALOS/AW3D30/V3_2")
            .select("DSM").mosaic().reduceRegion(ee.Reducer.first(), point, 30).get("DSM"),
        "Sentinel-2 SR scenes, summer 2024": lambda: ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(box).filterDate("2024-06-01", "2024-09-01").size(),
        "Cloud Score+ scenes, summer 2024": lambda: ee.ImageCollection("GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED")
            .filterBounds(box).filterDate("2024-06-01", "2024-09-01").size(),
        "Sentinel-1 GRD scenes, 2024": lambda: ee.ImageCollection("COPERNICUS/S1_GRD")
            .filterBounds(box).filterDate("2024-01-01", "2025-01-01").size(),
        "ERA5-Land precipitation, 2024 total (mm)": lambda: ee.ImageCollection("ECMWF/ERA5_LAND/DAILY_AGGR")
            .filterDate("2024-01-01", "2025-01-01").select("total_precipitation_sum").sum()
            .multiply(1000).reduceRegion(ee.Reducer.first(), point, 11132).get("total_precipitation_sum"),
    }
    results = {}
    for name, build in checks.items():
        t0 = time.time()
        try:
            value = build().getInfo()
            results[name] = {"ok": value is not None, "value": value, "seconds": round(time.time() - t0, 1),
                             "error": "" if value is not None else "no value returned"}
        except Exception as e:
            results[name] = {"ok": False, "value": None, "seconds": round(time.time() - t0, 1),
                             "error": _log_failure(f"check {name}", e)}
    return results
