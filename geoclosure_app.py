"""
GeoClosure Explorer — Quantum Geomatics  (UI v3, matches Technical Specification v2.0)

Layout
  Sidebar : 1 Site (+ elevation source / LiDAR) → 2 Natural analogue → 3 Parameters [Run] → 4 Display
  Top     : engineered-vs-analogue key-number strip (incl. design-check flags)
  Centre  : split map (satellite | active analysis layer) + legend / inspector
  Tabs    : Morphometrics · Erosion & hotspots · Design criteria · Monitoring · Technical brief

Earth Engine
  Monitoring (Sentinel-2 NDVI / bare-soil fraction, Sentinel-1 VH / RVI) runs live when a
  service account is configured in .streamlit/secrets.toml under [gee_service_account].
  Without credentials the app shows clearly labelled demo series.
  Other backend hooks are marked  # >>> GEE
"""

import hashlib
import io
import json
import os
import tempfile
import xml.etree.ElementTree as ET
from datetime import date

import folium
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from folium.plugins import Draw, SideBySideLayers
from streamlit_folium import st_folium

# -----------------------------------------------------------------------------
# 0. CONSTANTS
# -----------------------------------------------------------------------------
APP_NAME = "GeoClosure Explorer"
ORG = "Quantum Geomatics"
ENG_COLOR = "#0F172A"   # engineered landform: solid dark outline
ANA_COLOR = "#2563EB"   # natural analogue: dashed blue outline

CASE_STUDIES = {
    "Kiruna area waste rock (Sweden)":  {"center": [67.8380, 20.2050], "zoom": 13},
    "Malmberget dump north (Sweden)":   {"center": [67.1800, 20.6600], "zoom": 13},
    "Aitik area (Sweden)":              {"center": [67.0725, 20.9550], "zoom": 12},
    "Pilbara closure cell (Australia)": {"center": [-23.3600, 119.6700], "zoom": 12},
    "Sudbury tailings basin (Canada)":  {"center": [46.4900, -81.0000], "zoom": 12},
}

COVER_K = {  # SI units t·ha·h / (ha·MJ·mm)
    "Glacial till and moraine mix": 0.035,
    "Crushed waste rock armour":     0.010,
    "Organic topsoil and peat":      0.020,
    "Engineered clay cap":           0.045,
}

ELEVATION_SOURCES = [
    "ALOS AW3D30 (30 m, global)",
    "LiDAR / drone DTM (upload)",
    "Proposed design surface (upload)",
]

DISPLAY_LAYERS = {
    "Slope gradient (°)": {"type": "classes", "classes": [
        ("0–5°", "#F1F5F9"), ("5–10°", "#CBD5E1"), ("10–18°", "#94A3B8"),
        ("18–25°", "#F59E0B"), (">25°", "#B91C1C")]},
    "Relative erosion susceptibility": {"type": "classes", "classes": [
        ("Low", "#FEF3C7"), ("Moderate", "#FDBA74"), ("High", "#EA580C"), ("Very high", "#9A3412")],
        "note": "Relative RUSLE index, not calibrated soil loss."},
    "Flow accumulation": {"type": "ramp", "ramp": ["#EFF6FF", "#60A5FA", "#1E3A8A"],
                          "range": "contributing area, low → high (m²)"},
    "Channel network (from DTM)": {"type": "ramp", "ramp": ["#BFDBFE", "#1D4ED8"],
                                   "range": "stream order 1 → 4"},
    "Hillshade": {"type": "ramp", "ramp": ["#0F172A", "#F8FAFC"], "range": "shaded → lit"},
    "NDVI (Sentinel-2, season median)": {"type": "ramp", "ramp": ["#A16207", "#FDE68A", "#15803D"],
                                         "range": "0 → 0.8"},
    "Sentinel-1 VH backscatter": {"type": "ramp", "ramp": ["#111827", "#9CA3AF", "#F9FAFB"],
                                  "range": "−25 → −10 dB"},
    "DEM of difference (needs 2 surveys)": {"type": "ramp", "ramp": ["#B91C1C", "#F8FAFC", "#1D4ED8"],
                                            "range": "erosion ← 0 → deposition (m)"},
}

SLOPE_BINS = ["0–5°", "5–10°", "10–18°", "18–25°", ">25°"]


# -----------------------------------------------------------------------------
# 1. PAGE CONFIG & STYLING
# -----------------------------------------------------------------------------
st.set_page_config(page_title=f"{APP_NAME} · {ORG}", page_icon="⛰️",
                   layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
    .stApp { background-color: #F4F6F9; }
    .block-container { padding-top: 3.6rem; }
    [data-testid="stSidebar"] { background-color: #12171D; border-right: 1px solid #26303D; }
    [data-testid="stSidebar"] * { color: #E2E8F0; }
    [data-testid="stSidebar"] input, [data-testid="stSidebar"] [data-baseweb="select"] * { color: #0F172A; }
    [data-testid="stSidebar"] [data-testid="stFileUploader"] section *,
    [data-testid="stSidebar"] [data-testid="stFileUploaderFile"] * { color: #0F172A !important; }
    .step-title { font-weight: 700; font-size: 0.82rem; letter-spacing: 0.06em; text-transform: uppercase;
                  color: #FFFFFF; margin: 0.9rem 0 0.2rem; }
    .step-num { display:inline-block; width:20px; height:20px; border-radius:50%; background:#2563EB;
                color:#fff; text-align:center; line-height:20px; font-size:0.72rem; margin-right:6px; }
    .ui-card { background:#FFFFFF; border:1px solid #E2E8F0; border-radius:8px; padding:0.8rem 1rem;
               margin-bottom:0.75rem; }
    .card-title { font-weight:700; font-size:0.86rem; color:#0F172A; margin-bottom:6px; }
    .legend-row { display:flex; align-items:center; gap:8px; font-size:0.8rem; color:#334155; margin-bottom:3px; }
    .swatch { width:12px; height:12px; border-radius:2px; display:inline-block; border:1px solid #CBD5E1; }
    .muted { font-size:0.74rem; color:#64748B; }
    .kv { font-size:0.8rem; line-height:1.7; color:#334155; }
    .pill { display:inline-block; padding:2px 8px; border-radius:999px; font-size:0.72rem; font-weight:600; }
    .pill-live { background:#ECFDF5; color:#047857; border:1px solid #A7F3D0; }
    .pill-demo { background:#FFFBEB; color:#92400E; border:1px solid #FDE68A; }
    div[data-testid="stMetric"] { background:#FFFFFF; border:1px solid #E2E8F0; border-radius:8px;
                                  padding:0.6rem 0.9rem; }
    div[data-testid="stMetricLabel"] p { font-size:0.78rem; color:#475569; }
</style>
""", unsafe_allow_html=True)


# -----------------------------------------------------------------------------
# 2. EARTH ENGINE (live monitoring) — falls back to demo data without credentials
# -----------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def ee_ready() -> bool:
    try:
        import ee
        sa = dict(st.secrets["gee_service_account"])
        creds = ee.ServiceAccountCredentials(sa["client_email"], key_data=json.dumps(sa))
        ee.Initialize(creds)
        return True
    except Exception:
        return False


def _monthly_stats(col, fc, start, end, scale):
    """Monthly median composite → mean per AOI feature. Returns a pandas DataFrame."""
    import ee
    start_d = ee.Date(start)
    n = ee.Date(end).difference(start_d, "month").round()

    def per_month(i):
        d0 = start_d.advance(i, "month")
        m = col.filterDate(d0, d0.advance(1, "month"))
        stats = m.median().reduceRegions(collection=fc, reducer=ee.Reducer.mean(), scale=scale)
        return stats.map(lambda f: f.set({"date": d0.format("YYYY-MM"), "n_scenes": m.size()}))

    out = ee.FeatureCollection(ee.List.sequence(0, n.subtract(1)).map(per_month)).flatten()
    feats = out.filter(ee.Filter.gt("n_scenes", 0)).getInfo()["features"]
    return pd.DataFrame([f["properties"] for f in feats])


@st.cache_data(show_spinner="Computing Sentinel-2 monitoring series…", ttl=24 * 3600)
def s2_monitoring(geoms_json: str, start: str, end: str, season=(6, 9)) -> pd.DataFrame:
    """NDVI and bare-soil fraction per month (growing season) for each AOI."""
    import ee
    geoms = json.loads(geoms_json)
    fc = ee.FeatureCollection([ee.Feature(ee.Geometry(g), {"aoi": k}) for k, g in geoms.items()])
    cs = ee.ImageCollection("GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED")

    def prep(img):
        clear = img.select("cs_cdf").gte(0.6).And(img.select("SCL").neq(11))  # cloud + snow mask
        img = img.updateMask(clear)
        ndvi = img.normalizedDifference(["B8", "B4"]).rename("ndvi")
        bsi = img.expression("((S + R) - (N + B)) / ((S + R) + (N + B))",
                             {"S": img.select("B11"), "R": img.select("B4"),
                              "N": img.select("B8"), "B": img.select("B2")}).rename("bsi")
        bare = ndvi.lt(0.2).And(bsi.gt(0)).rename("bare_fraction")
        return ndvi.addBands(bare).copyProperties(img, ["system:time_start"])

    col = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
           .filterBounds(fc.geometry()).filterDate(start, end)
           .filter(ee.Filter.calendarRange(season[0], season[1], "month"))
           .linkCollection(cs, ["cs_cdf"]).map(prep))
    return _monthly_stats(col, fc, start, end, scale=10)


@st.cache_data(show_spinner="Computing Sentinel-1 monitoring series…", ttl=24 * 3600)
def s1_monitoring(geoms_json: str, start: str, end: str) -> pd.DataFrame:
    """VH (dB) and Radar Vegetation Index per month, single relative orbit, speckle-filtered.
    >>> GEE: add radiometric terrain flattening (gamma0) with the LiDAR DTM for steep dump faces."""
    import ee
    geoms = json.loads(geoms_json)
    fc = ee.FeatureCollection([ee.Feature(ee.Geometry(g), {"aoi": k}) for k, g in geoms.items()])
    col = (ee.ImageCollection("COPERNICUS/S1_GRD")
           .filterBounds(fc.geometry()).filterDate(start, end)
           .filter(ee.Filter.eq("instrumentMode", "IW"))
           .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
           .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH")))
    orbit = col.aggregate_array("relativeOrbitNumber_start").reduce(ee.Reducer.mode())
    col = col.filter(ee.Filter.eq("relativeOrbitNumber_start", orbit))  # never mix geometries

    def prep(img):
        lin = ee.Image(10).pow(img.select(["VV", "VH"]).divide(10)).focalMedian(30, "circle", "meters")
        rvi = lin.expression("4 * VH / (VV + VH)", {"VH": lin.select("VH"), "VV": lin.select("VV")}).rename("rvi")
        vh_db = lin.select("VH").log10().multiply(10).rename("vh_db")
        return vh_db.addBands(rvi).copyProperties(img, ["system:time_start"])

    return _monthly_stats(col.map(prep), fc, start, end, scale=10)


def demo_monitoring(start_year: int, end_year: int):
    """Synthetic but plausible series, used only when Earth Engine is not configured."""
    rng = np.random.default_rng(7)
    s2_rows, s1_rows = [], []
    for y in range(start_year, end_year + 1):
        t = y - start_year
        for mth in range(1, 13):
            d = f"{y}-{mth:02d}"
            if 6 <= mth <= 9:
                peak = 1 - abs(mth - 7.5) / 3
                s2_rows += [
                    {"aoi": "engineered", "date": d,
                     "ndvi": 0.12 + 0.05 * t + 0.10 * peak + rng.normal(0, 0.015),
                     "bare_fraction": max(0.05, 0.74 - 0.065 * t - 0.10 * peak + rng.normal(0, 0.02))},
                    {"aoi": "analogue", "date": d,
                     "ndvi": 0.48 + 0.18 * peak + rng.normal(0, 0.015),
                     "bare_fraction": max(0.01, 0.06 - 0.02 * peak + rng.normal(0, 0.008))},
                ]
            snow = mth in (11, 12, 1, 2, 3, 4)
            s1_rows += [
                {"aoi": "engineered", "date": d,
                 "vh_db": -21.5 + 0.5 * t + (-2.5 if snow else 0) + rng.normal(0, 0.3),
                 "rvi": 0.22 + 0.04 * t + rng.normal(0, 0.02)},
                {"aoi": "analogue", "date": d,
                 "vh_db": -16.5 + (-3.0 if snow else 0) + rng.normal(0, 0.3),
                 "rvi": 0.55 + rng.normal(0, 0.02)},
            ]
    return pd.DataFrame(s2_rows), pd.DataFrame(s1_rows)


# -----------------------------------------------------------------------------
# 2b. EROSION RATES — repeat-survey DoD and field measurements
# -----------------------------------------------------------------------------
FIELD_TEMPLATE = pd.DataFrame([
    {"date": "2025-06-20", "aoi": "engineered", "method": "trap", "value": 41.0, "unit": "kg",
     "contributing_area_ha": 0.02, "period_days": 45},
    {"date": "2025-06-20", "aoi": "analogue", "method": "trap", "value": 0.06, "unit": "kg",
     "contributing_area_ha": 0.02, "period_days": 45},
    {"date": "2025-09-15", "aoi": "engineered", "method": "pin", "value": 3.5, "unit": "mm",
     "contributing_area_ha": "", "period_days": 87},
    {"date": "2025-09-15", "aoi": "engineered", "method": "outlet", "value": 26.0, "unit": "t",
     "contributing_area_ha": 20.5, "period_days": 87},
])
METHOD_LABEL = {"trap": "Sediment traps", "pin": "Erosion pins", "outlet": "Outlet sediment yield",
                "dod": "Repeat survey (DoD)"}


def dod_rates(dem1: bytes, dem2: bytes, geoms: dict, years: float, sigma: float, rho: float,
              t: float = 1.96) -> dict:
    """Erosion rates from two DTM surveys (GeoTIFF bytes). Survey 2 is resampled onto survey 1.
    Change below the level of detection LoD = t·√2·σ is ignored. Returns one dict per AOI."""
    from rasterio.features import geometry_mask
    from rasterio.io import MemoryFile
    from rasterio.warp import Resampling, reproject, transform_geom

    with MemoryFile(dem1) as m1, MemoryFile(dem2) as m2, m1.open() as d1, m2.open() as d2:
        z1 = d1.read(1, masked=True).astype("float64").filled(np.nan)
        z2 = np.full(z1.shape, np.nan)
        reproject(d2.read(1, masked=True).astype("float64").filled(np.nan), z2,
                  src_transform=d2.transform, src_crs=d2.crs, dst_transform=d1.transform,
                  dst_crs=d1.crs, src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.bilinear)
        cell = abs(d1.transform.a * d1.transform.e)
        crs, transform = d1.crs, d1.transform

    dz = z2 - z1
    sigma_dod = np.sqrt(2) * sigma
    lod = t * sigma_dod
    out = {}
    for name, g in geoms.items():
        inside = ~geometry_mask([transform_geom("EPSG:4326", crs, g)], out_shape=dz.shape, transform=transform)
        valid = inside & np.isfinite(dz)
        if valid.sum() == 0:
            out[name] = None  # no survey coverage of this AOI
            continue
        ero, dep = valid & (dz < -lod), valid & (dz > lod)
        area = valid.sum() * cell
        ve, vd = float(-dz[ero].sum() * cell), float(dz[dep].sum() * cell)
        detected_area = (ero.sum() + dep.sum()) * cell
        lowering = 1000 * (ve - vd) / (area * years)
        lowering_unc = 1000 * sigma_dod * detected_area / (area * years)
        out[name] = {
            "area_ha": area / 1e4, "erosion_m3": ve, "deposition_m3": vd, "net_m3": ve - vd,
            "lowering_mm_yr": lowering, "lowering_unc": lowering_unc,
            "rate_t_ha_yr": lowering * rho * 10, "rate_unc": lowering_unc * rho * 10,
            "detected_share": detected_area / area, "lod_m": lod,
            "below_detection": detected_area / area < 0.005,
        }
    return out


def demo_dod(years: float, sigma: float, rho: float) -> dict:
    lod = 1.96 * np.sqrt(2) * sigma
    area, ve, vd, det = 20.5e4, 2400.0, 450.0, 0.07
    low = 1000 * (ve - vd) / (area * years)
    unc = 1000 * np.sqrt(2) * sigma * det * area / (area * years)
    return {
        "engineered": {"area_ha": area / 1e4, "erosion_m3": ve, "deposition_m3": vd, "net_m3": ve - vd,
                       "lowering_mm_yr": low, "lowering_unc": unc, "rate_t_ha_yr": low * rho * 10,
                       "rate_unc": unc * rho * 10, "detected_share": det, "lod_m": lod,
                       "below_detection": False},
        "analogue": {"area_ha": 31.0, "erosion_m3": 0.0, "deposition_m3": 0.0, "net_m3": 0.0,
                     "lowering_mm_yr": 0.0, "lowering_unc": 0.0, "rate_t_ha_yr": 0.0, "rate_unc": 0.0,
                     "detected_share": 0.001, "lod_m": lod, "below_detection": True},
    }


def demo_field(years=(2023, 2025)) -> pd.DataFrame:
    rng = np.random.default_rng(11)
    rows = []
    for y in range(years[0], years[1] + 1):
        for end, days, eng_kg, ana_kg in [("06-20", 45, 55, 0.9), ("09-15", 87, 30, 0.7), ("10-30", 45, 6, 0.2)]:
            for i in range(3):
                rows.append({"date": f"{y}-{end}", "aoi": "engineered", "method": "trap",
                             "value": round(eng_kg * rng.lognormal(0, 0.35) * (1 - 0.12 * (y - years[0])), 2),
                             "unit": "kg", "contributing_area_ha": 0.02, "period_days": days})
                rows.append({"date": f"{y}-{end}", "aoi": "analogue", "method": "trap",
                             "value": round(ana_kg * rng.lognormal(0, 0.35), 3),
                             "unit": "kg", "contributing_area_ha": 0.02, "period_days": days})
        rows.append({"date": f"{y}-09-15", "aoi": "engineered", "method": "pin",
                     "value": round(4.5 * rng.lognormal(0, 0.2), 1), "unit": "mm",
                     "contributing_area_ha": np.nan, "period_days": 365})
        for aoi, load, area in [("engineered", 600, 20.5), ("analogue", 9, 31.0)]:
            rows.append({"date": f"{y}-10-30", "aoi": aoi, "method": "outlet",
                         "value": round(load * rng.lognormal(0, 0.15), 1), "unit": "t",
                         "contributing_area_ha": area, "period_days": 180})
    return pd.DataFrame(rows)


def field_rates(df: pd.DataFrame, rho: float) -> pd.DataFrame:
    """Convert field records to t/ha/yr. trap: kg over area; pin: mm lowering; outlet: t over area."""
    df = df.copy()
    df["years"] = pd.to_numeric(df["period_days"], errors="coerce") / 365.25
    area = pd.to_numeric(df["contributing_area_ha"], errors="coerce")
    val = pd.to_numeric(df["value"], errors="coerce")
    df["rate_t_ha_yr"] = np.select(
        [df["method"].eq("trap"), df["method"].eq("pin"), df["method"].eq("outlet")],
        [val / 1000 / area / df["years"], val / df["years"] * rho * 10, val / area / df["years"]],
        default=np.nan)
    df["issue"] = np.where(df["rate_t_ha_yr"].isna(), "missing area, period or unknown method", "")
    month = pd.to_datetime(df["date"], errors="coerce").dt.month
    df["season"] = np.select([month.between(5, 6), month.between(7, 9)],
                             ["Spring melt (May–Jun)", "Summer storms (Jul–Sep)"], "Autumn / other")
    return df


def rate_summary(dod: dict, field: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for aoi in ("engineered", "analogue"):
        r = (dod or {}).get(aoi)
        if r:
            rows.append({"aoi": aoi, "method": "dod",
                         "rate_t_ha_yr": np.nan if r["below_detection"] else r["rate_t_ha_yr"],
                         "spread": r["rate_unc"], "n": 1,
                         "note": f"below detection (LoD {r['lod_m']:.2f} m)" if r["below_detection"]
                         else f"±{r['rate_unc']:.0f} (1σ), {r['detected_share']:.0%} area"})
    ok = field[field["issue"] == ""]
    for (aoi, method), g in ok.groupby(["aoi", "method"]):
        q = g["rate_t_ha_yr"].quantile([0.25, 0.5, 0.75])
        rows.append({"aoi": aoi, "method": method, "rate_t_ha_yr": q[0.5], "spread": (q[0.75] - q[0.25]) / 2,
                     "n": len(g), "note": f"IQR {q[0.25]:.2g}–{q[0.75]:.2g}"})
    return pd.DataFrame(rows)


# -----------------------------------------------------------------------------
# 2c. AREA OF INTEREST — upload (GeoJSON / KML / zipped shapefile), draw, validate
# -----------------------------------------------------------------------------
AOI_MODES = ["Case-study outline", "Upload file", "Draw on map"]
AOI_FILE_TYPES = ["geojson", "json", "kml", "zip"]
AOI_MAX_HA = {"coarse": 5000.0, "hires": 1500.0}   # processing caps (30 m vs ≤2 m DEM)
AOI_MIN_HA = 0.5


def _kml_polygons(data: bytes):
    """Dependency-free KML polygon reader (fallback when the GDAL KML driver is missing)."""
    from shapely.geometry import Polygon
    root = ET.fromstring(data)
    polys = []
    for pg in root.iter():
        if not pg.tag.endswith("Polygon"):
            continue
        rings = {"outer": None, "inner": []}
        for el in pg.iter():
            if el.tag.endswith("coordinates") and el.text:
                pts = [tuple(map(float, c.split(",")[:2])) for c in el.text.split()]
                rings["outer" if rings["outer"] is None else "inner"] = pts if rings["outer"] is None \
                    else rings["inner"] + [pts]
        if rings["outer"] and len(rings["outer"]) >= 3:
            polys.append(Polygon(rings["outer"], rings["inner"]))
    return polys


@st.cache_data(show_spinner=False)
def read_aoi_file(data: bytes, name: str):
    """Return (GeoJSON geometry in EPSG:4326, note) or (None, error). All polygon features are merged."""
    import geopandas as gpd
    import shapely
    from shapely.geometry import mapping, shape

    name = name.lower()
    try:
        if name.endswith((".geojson", ".json")):
            gj = json.loads(data)
            if gj.get("type") in ("Polygon", "MultiPolygon"):          # bare geometry
                geoms, crs = [shape(gj)], "EPSG:4326"
            else:
                gdf = gpd.read_file(io.BytesIO(data))
                geoms, crs = list(gdf.geometry), gdf.crs or "EPSG:4326"  # RFC 7946: WGS84
        elif name.endswith(".kml"):
            try:
                gdf = gpd.read_file(io.BytesIO(data), driver="KML")
                geoms = list(gdf.geometry)
            except Exception:
                geoms = _kml_polygons(data)
            crs = "EPSG:4326"                                           # KML is always WGS84
        elif name.endswith(".zip"):
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "aoi.zip")
                with open(path, "wb") as fh:
                    fh.write(data)
                gdf = gpd.read_file(f"zip://{path}")
            if gdf.crs is None:
                return None, "The shapefile has no .prj file, so its coordinate system is unknown."
            geoms, crs = list(gdf.geometry), gdf.crs
        else:
            return None, "Unsupported file type. Use GeoJSON, KML or a zipped shapefile."
    except Exception as e:
        return None, f"Couldn't read the file ({type(e).__name__})."

    gs = gpd.GeoSeries([g for g in geoms if g is not None], crs=crs).to_crs("EPSG:4326")
    gs = gs[gs.geom_type.isin(["Polygon", "MultiPolygon"])]
    if gs.empty:
        return None, "No polygon found. Points and lines can't define an analysis area."
    merged = shapely.make_valid(shapely.force_2d(shapely.union_all(list(gs))))
    if merged.geom_type == "GeometryCollection":
        merged = shapely.union_all([g for g in merged.geoms if g.geom_type in ("Polygon", "MultiPolygon")])
    note = f"{len(gs)} polygon feature{'s' if len(gs) > 1 else ''}" + (" merged" if len(gs) > 1 else "")
    return mapping(merged), note


def aoi_area_ha(geom) -> float:
    from pyproj import Geod
    from shapely.geometry import shape
    return abs(Geod(ellps="WGS84").geometry_area_perimeter(shape(geom))[0]) / 1e4


@st.cache_data(show_spinner=False)
def raster_footprint(data: bytes):
    """Bounding box of an uploaded DTM in EPSG:4326, for coverage checks."""
    from rasterio.io import MemoryFile
    from rasterio.warp import transform_bounds
    from shapely.geometry import box, mapping
    with MemoryFile(data) as mf, mf.open() as ds:
        return mapping(box(*transform_bounds(ds.crs, "EPSG:4326", *ds.bounds)))


def aoi_checks(eng, ana, hires: bool, footprint=None):
    """Return list of (level, message); level 'error' blocks the run."""
    from shapely.geometry import shape
    out, cap = [], AOI_MAX_HA["hires" if hires else "coarse"]
    shp = {k: shape(g) for k, g in (("Engineered landform", eng), ("Natural analogue", ana)) if g}
    for k, g in shp.items():
        ha = aoi_area_ha(g.__geo_interface__)
        if ha > cap:
            out.append(("error", f"{k} is {ha:,.0f} ha, above the {cap:,.0f} ha processing limit for this "
                                 "elevation source. Split it or use a smaller area."))
        elif ha < AOI_MIN_HA:
            out.append(("warning", f"{k} is only {ha:.2f} ha; statistics will be unreliable."))
        if footprint:
            cov = g.intersection(shape(footprint)).area / g.area if g.area else 0
            if cov < 0.999:
                out.append(("warning", f"{k}: {cov:.0%} covered by the uploaded DTM. Outside it, AW3D30 is used, "
                                       "so engineered and analogue may not be compared on the same source."))
    if len(shp) == 2:
        e, a = shp["Engineered landform"], shp["Natural analogue"]
        if e.intersects(a) and e.intersection(a).area > 0.01 * min(e.area, a.area):
            out.append(("warning", "The engineered landform and the analogue overlap. The analogue should be "
                                   "undisturbed terrain outside the mine footprint."))
    return out


def geom_key(g) -> str:
    return hashlib.md5(json.dumps(g, sort_keys=True).encode()).hexdigest()[:10] if g else "none"


# -----------------------------------------------------------------------------
# 3. PLACEHOLDER TERRAIN RESULTS (>>> GEE + pysheds/WhiteboxTools: replace)
# -----------------------------------------------------------------------------
def placeholder_results(params: dict) -> dict:
    x = np.linspace(0, 1, 50)
    hyp_eng, hyp_ana = 1 - x ** 1.80, 1 - x ** 0.95

    def hi(y):
        return float(np.sum((y[1:] + y[:-1]) / 2 * np.diff(x)))

    slope_eng, slope_ana = [8, 14, 30, 33, 15], [14, 27, 40, 15, 4]
    thr = params["instability_thresh"]
    above_eng = sum(slope_eng[3:]) if thr <= 18 else slope_eng[4] + slope_eng[3] // 2
    above_ana = sum(slope_ana[3:]) if thr <= 18 else slope_ana[4] + slope_ana[3] // 2

    # Design criteria: analogue distribution (P10, median, P90) vs engineered value
    criteria = [
        ("Drainage density", "km/km²", 1.6, 2.1, 2.7, 1.4),
        ("Ridge-to-channel-head distance", "m", 45, 70, 110, 140),
        ("Channel gradient, 1st order", "%", 4.0, 7.5, 12.0, 9.0),
        ("Channel gradient, 2nd order", "%", 1.5, 3.2, 5.5, 4.9),
        ("Hillslope gradient", "°", 4.0, 9.5, 17.0, 16.0),
        ("Hillslope length", "m", 35, 65, 120, 150),
        ("Concave share of hillslope area", "%", 38, 52, 64, 21),
        ("Hypsometric integral", "–", 0.42, 0.48, 0.54, round(hi(hyp_eng), 2)),
        ("Relief ratio", "–", 0.04, 0.06, 0.09, 0.08),
    ]
    df = pd.DataFrame(criteria, columns=["Parameter", "Unit", "Analogue P10", "Analogue median",
                                         "Analogue P90", "Engineered"])
    df["Design check"] = np.where((df["Engineered"] >= df["Analogue P10"]) &
                                  (df["Engineered"] <= df["Analogue P90"]),
                                  "Within analogue range", "Outside range")

    return {
        "hyp_x": x, "hyp_eng": hyp_eng, "hyp_ana": hyp_ana,
        "hi_eng": hi(hyp_eng), "hi_ana": hi(hyp_ana),
        "slope_eng": slope_eng, "slope_ana": slope_ana,
        "mean_slope_eng": 17.8, "mean_slope_ana": 11.2,
        "above_thr_eng": above_eng, "above_thr_ana": above_ana,
        "dd_eng": 1.4, "dd_ana": 2.1,
        "rel_soil_loss_eng": "High", "rel_soil_loss_ana": "Low",
        "erosion_class_area_eng": [18, 29, 34, 19],
        "erosion_class_area_ana": [52, 31, 13, 4],
        "criteria": df,
    }


# -----------------------------------------------------------------------------
# 4. SIDEBAR — NUMBERED WORKFLOW
# -----------------------------------------------------------------------------
def step(n, title):
    st.markdown(f'<div class="step-title"><span class="step-num">{n}</span>{title}</div>',
                unsafe_allow_html=True)


with st.sidebar:
    st.markdown(f"### ⛰️ {APP_NAME}")
    st.caption(f"{ORG} · Geomorphic landform assessment and design support")

    step(1, "Engineered landform")
    site = st.selectbox("Site (sets map location)", list(CASE_STUDIES.keys()))
    eng_mode = st.radio("Boundary", AOI_MODES, key="eng_mode", horizontal=False,
                        help="Use the case-study outline, upload your own boundary, or draw it on the map.")
    eng_upload = None
    if eng_mode == "Upload file":
        eng_upload = st.file_uploader("GeoJSON, KML or zipped shapefile", type=AOI_FILE_TYPES, key="eng_upload",
                                      help="Any CRS (reprojected to WGS84). All polygon features are merged.")
    elif eng_mode == "Draw on map":
        st.caption("Select *Engineered landform* above the map, then use the polygon tool.")
    dem_source = st.radio("Elevation source", ELEVATION_SOURCES,
                          help="30 m data cannot resolve dump benches and batters. Upload a bare-earth "
                               "LiDAR or drone DTM (e.g. Lantmäteriet for Swedish sites) for bench-scale "
                               "analysis, or a proposed design surface exported from CAD.")
    dem_file = dem_file_2 = None
    if dem_source != ELEVATION_SOURCES[0]:
        dem_file = st.file_uploader("DTM GeoTIFF (SWEREF 99 TM / RH 2000 for Swedish sites)",
                                    type=["tif", "tiff"])
        if dem_source.startswith("LiDAR"):
            dem_file_2 = st.file_uploader("Optional: second survey for change detection",
                                          type=["tif", "tiff"])
        st.caption("LiDAR is used inside its footprint; AW3D30 fills context outside it. "
                   "Engineered and analogue are always compared on the same source.")

    step(2, "Natural analogue")
    ana_mode = st.radio("Boundary", AOI_MODES, key="ana_mode",
                        help="An undisturbed natural catchment near the mine, used as the design reference.")
    ana_upload = None
    if ana_mode == "Upload file":
        ana_upload = st.file_uploader("GeoJSON, KML or zipped shapefile", type=AOI_FILE_TYPES, key="ana_upload",
                                      help="Any CRS (reprojected to WGS84). All polygon features are merged.")
    elif ana_mode == "Draw on map":
        st.caption("Select *Natural analogue* above the map, then use the polygon tool.")

    lat0 = CASE_STUDIES[site]["center"][0]
    r_source = "CHIRPS" if abs(lat0) <= 50 else "ERA5-Land"

    with st.form("analysis_form", border=False):
        step(3, "Parameters")
        r_factor = st.number_input(f"Rainfall erosivity R (MJ·mm/ha·h·yr) — source: {r_source}",
                                   0.0, 20000.0, 450.0 if r_source == "ERA5-Land" else 900.0, 10.0,
                                   help="CHIRPS covers 50°S–50°N only; northern sites use ERA5-Land "
                                        "(underestimates intensity) or SMHI station data.")
        cover_type = st.selectbox("Cover material", list(COVER_K.keys()))
        k_factor = st.slider("Soil erodibility K (t·ha·h/ha·MJ·mm)", 0.005, 0.080,
                             COVER_K[cover_type], 0.001, format="%.3f")
        slope_len = st.slider("Max. slope length L (m)", 5.0, 150.0, 40.0, 0.5)
        target_ndvi = st.slider("Target revegetation NDVI (→ C-factor)", 0.0, 1.0, 0.65, 0.01)
        instability_thresh = st.slider("Instability threshold (°)", 10, 35, 18, 1)
        channel_thresh = st.number_input("Channel-initiation area (m²)", 100, 50000, 2500, 100,
                                         help="Calibrate against channel heads visible in LiDAR hillshade.")
        mon_years = st.slider("Monitoring period", 2017, date.today().year, (2019, date.today().year - 1))
        bulk_density = st.number_input("Material dry bulk density ρb (t/m³)", 1.0, 2.6, 1.8, 0.05,
                                       help="Converts surface lowering (mm/yr) to t/ha/yr. Use site test values.")
        survey_sigma = st.number_input("Survey vertical uncertainty σ (m)", 0.01, 0.50, 0.05, 0.01,
                                       help="Per survey. Level of detection = 1.96·√2·σ.")
        survey_years = st.number_input("Years between surveys", 0.25, 20.0, 2.0, 0.25)
        run = st.form_submit_button("Run analysis", type="primary", width="stretch")

    step(4, "Display")
    active_layer = st.radio("Analysis layer", list(DISPLAY_LAYERS.keys()), label_visibility="collapsed")
    layer_opacity = st.slider("Layer opacity", 0.0, 1.0, 0.75, 0.05)

    st.divider()
    st.markdown(f"**{ORG}**  \n<span style='font-size:0.75rem;color:#94A3B8;'>"
                "Portfolio demonstration</span>", unsafe_allow_html=True)


# -----------------------------------------------------------------------------
# 5. GEOMETRIES (uploaded or placeholder) & RUN
# -----------------------------------------------------------------------------
lat0, lon0 = CASE_STUDIES[site]["center"]
d = 0.006
eng_ring = [[lon0 - 3*d, lat0 + d], [lon0 - d, lat0 + 1.8*d], [lon0 + 0.5*d, lat0 + 0.3*d],
            [lon0 - 0.7*d, lat0 - 1.2*d], [lon0 - 3.3*d, lat0 - 0.7*d], [lon0 - 3*d, lat0 + d]]
ana_ring = [[lon0 + d, lat0 + 2*d], [lon0 + 5*d, lat0 + 2.6*d], [lon0 + 6*d, lat0],
            [lon0 + 3*d, lat0 - 1.7*d], [lon0 + 0.3*d, lat0 - 0.4*d], [lon0 + d, lat0 + 2*d]]
st.session_state.setdefault("drawn", {"engineered": None, "analogue": None})


def resolve_aoi(target, mode, upload, ring):
    """-> (geometry or None, source label, note)"""
    if mode == "Case-study outline":
        return {"type": "Polygon", "coordinates": [ring]}, "case study", "demo outline"
    if mode == "Upload file":
        if upload is None:
            return None, "upload", "waiting for a file"
        g, note = read_aoi_file(upload.getvalue(), upload.name)
        return g, "uploaded", note if g else note
    g = st.session_state.drawn.get(target)
    return g, "drawn", "" if g else "waiting for a polygon on the map"


eng_geom, eng_src, eng_note = resolve_aoi("engineered", eng_mode, eng_upload, eng_ring)
ana_geom, ana_src, ana_note = resolve_aoi("analogue", ana_mode, ana_upload, ana_ring)
hires = dem_source != ELEVATION_SOURCES[0]
footprint = None
if dem_file is not None:
    try:
        footprint = raster_footprint(dem_file.getvalue())
    except Exception:
        st.warning("Couldn't read the uploaded DTM's extent.")
checks = aoi_checks(eng_geom, ana_geom, hires, footprint)
missing = [n for n, g in (("engineered landform", eng_geom), ("natural analogue", ana_geom)) if g is None]
blocking = [m for lvl, m in checks if lvl == "error"]
if run and (missing or blocking):
    st.error("Can't run the analysis yet: " + "; ".join(
        [f"define the {n}" for n in missing] + blocking))
    run = False

params = dict(site=site, dem_source=dem_source, cover_type=cover_type, r_factor=r_factor,
              r_source=r_source, k_factor=k_factor, slope_len=slope_len, target_ndvi=target_ndvi,
              instability_thresh=instability_thresh, channel_thresh=channel_thresh,
              mon_years=mon_years, bulk_density=bulk_density, survey_sigma=survey_sigma,
              survey_years=survey_years, has_dod=dem_file_2 is not None,
              eng_key=geom_key(eng_geom), ana_key=geom_key(ana_geom), eng_src=eng_src, ana_src=ana_src,
              eng_ha=aoi_area_ha(eng_geom) if eng_geom else 0.0,
              ana_ha=aoi_area_ha(ana_geom) if ana_geom else 0.0)

if run or ("results" not in st.session_state and not missing and not blocking):
    st.session_state.params_run = params
    st.session_state.results = placeholder_results(params)  # >>> GEE: run_terrain_analysis(...)
    start, end = f"{mon_years[0]}-01-01", f"{mon_years[1] + 1}-01-01"
    live = ee_ready()
    if live:
        try:
            gj = json.dumps({"engineered": eng_geom, "analogue": ana_geom})
            s2, s1 = s2_monitoring(gj, start, end), s1_monitoring(gj, start, end)
        except Exception as e:
            live = False
            st.toast(f"Earth Engine request failed, showing demo series ({type(e).__name__}).")
    if not live:
        s2, s1 = demo_monitoring(*mon_years)
    st.session_state.monitoring = {"s2": s2, "s1": s1, "live": live}

    dod, dod_src = None, "demo"
    if dem_file is not None and dem_file_2 is not None:
        try:
            dod = dod_rates(dem_file.getvalue(), dem_file_2.getvalue(),
                            {"engineered": eng_geom, "analogue": ana_geom},
                            survey_years, survey_sigma, bulk_density)
            dod_src = "surveys"
        except Exception as e:
            st.toast(f"Couldn't compute DoD from the uploaded surveys ({type(e).__name__}); showing demo.")
    if dod is None:
        dod = demo_dod(survey_years, survey_sigma, bulk_density)
    st.session_state.erosion_dod = {"dod": dod, "src": dod_src}

if "results" not in st.session_state:
    st.info("Define both areas of interest in the sidebar, then press **Run analysis**.")
    st.stop()
res, p_run, mon = st.session_state.results, st.session_state.params_run, st.session_state.monitoring
stale = {k: v for k, v in params.items() if k != "has_dod"} != \
        {k: v for k, v in p_run.items() if k != "has_dod"}
n_flags = int((res["criteria"]["Design check"] == "Outside range").sum())

field_upload = st.session_state.get("field_csv")
field_src = "demo"
field_raw = demo_field()
if field_upload is not None:
    try:
        field_upload.seek(0)
        field_raw, field_src = pd.read_csv(field_upload), "uploaded"
    except Exception:
        st.warning("Couldn't read the field data CSV; showing demo records.")
field = field_rates(field_raw, p_run["bulk_density"])
ero = st.session_state.erosion_dod
summary = rate_summary(ero["dod"], field)


def _rate(aoi, mth):
    r = summary[(summary["aoi"] == aoi) & (summary["method"] == mth)]["rate_t_ha_yr"]
    return r.iloc[0] if len(r) and np.isfinite(r.iloc[0]) else np.nan


# Headline: first method measured on BOTH sites (like-for-like); otherwise best available per site
eng_rate = ana_rate = np.nan
for _m in ("dod", "trap", "outlet", "pin"):
    if np.isfinite(_rate("engineered", _m)) and np.isfinite(_rate("analogue", _m)):
        eng_rate, ana_rate, eng_m = _rate("engineered", _m), _rate("analogue", _m), METHOD_LABEL[_m]
        ana_m = eng_m
        break
else:
    eng_m = ana_m = "no data"
    for aoi in ("engineered", "analogue"):
        for _m in ("dod", "trap", "outlet", "pin"):
            if np.isfinite(_rate(aoi, _m)):
                if aoi == "engineered":
                    eng_rate, eng_m = _rate(aoi, _m), METHOD_LABEL[_m]
                else:
                    ana_rate, ana_m = _rate(aoi, _m), METHOD_LABEL[_m]
                break


# -----------------------------------------------------------------------------
# 6. HEADER + KEY-NUMBER STRIP
# -----------------------------------------------------------------------------
h1, h2 = st.columns([3, 1.2])
with h1:
    st.markdown(f"#### {p_run['site']}")
    st.caption(f"Engineered landform ({p_run['eng_src']}, {p_run['eng_ha']:,.1f} ha) vs "
               f"natural analogue ({p_run['ana_src']}, {p_run['ana_ha']:,.1f} ha)")
with h2:
    pill = ('<span class="pill pill-live">Earth Engine connected</span>' if mon["live"]
            else '<span class="pill pill-demo">Demo data · backend not connected</span>')
    st.markdown(f"<div style='text-align:right;padding-top:0.45rem;'>{pill}</div>", unsafe_allow_html=True)
if stale:
    st.info("Inputs or area boundaries changed since the last run. Press **Run analysis** to update results.")

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Hypsometric integral", f"{res['hi_eng']:.2f}",
          f"{res['hi_eng'] - res['hi_ana']:+.2f} vs analogue {res['hi_ana']:.2f}", delta_color="off")
k2.metric(f"Area > {p_run['instability_thresh']}°", f"{res['above_thr_eng']}%",
          f"{res['above_thr_eng'] - res['above_thr_ana']:+d} pts vs {res['above_thr_ana']}%",
          delta_color="inverse")
k3.metric("Drainage density (km/km²)", f"{res['dd_eng']:.1f}",
          f"{res['dd_eng'] - res['dd_ana']:+.1f} vs {res['dd_ana']:.1f}", delta_color="off")
k4.metric("Erosion rate (t/ha/yr)", f"{eng_rate:.3g} vs {ana_rate:.2g}",
          help=f"Engineered vs analogue, measured by {eng_m}" + ("" if eng_m == ana_m else f" / {ana_m}")
               + ". All methods in Erosion → Measured rates.")
k5.metric("Design-check flags", f"{n_flags} / {len(res['criteria'])}",
          help="Design parameters outside the analogue P10–P90 envelope (see Design criteria tab).")


# -----------------------------------------------------------------------------
# 7. SPLIT MAP + LEGEND / INSPECTOR
# -----------------------------------------------------------------------------
map_col, info_col = st.columns([3.4, 1.0], gap="medium")

with map_col:
    draw_targets = [lbl for lbl, mode in (("Engineered landform", eng_mode), ("Natural analogue", ana_mode))
                    if mode == "Draw on map"]
    draw_target = None
    if draw_targets:
        t1, t2, t3 = st.columns([2.2, 2.6, 1])
        draw_target = t1.radio("Drawing", draw_targets, horizontal=True, key="draw_target")
        t2.caption("Use the polygon tool (top right of the map). The last polygon you finish is used; "
                   "draw again to replace it.")
        tkey = "engineered" if draw_target.startswith("Engineered") else "analogue"
        if t3.button("Clear", width="stretch", help=f"Remove the drawn {draw_target.lower()}"):
            st.session_state.drawn[tkey] = None
            st.rerun()

    m = folium.Map(location=[lat0, lon0], zoom_start=CASE_STUDIES[site]["zoom"], tiles=None,
                   control_scale=True)
    satellite = folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri World Imagery", name="Satellite", overlay=False)
    # >>> GEE: url = ee_image.getMapId(vis)["tile_fetcher"].url_format  (LiDAR via ee.Image.loadGeoTIFF)
    analysis = folium.TileLayer(
        tiles="https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
        attr="OpenTopoMap (placeholder for Earth Engine layer)", name=active_layer,
        opacity=layer_opacity, overlay=False)
    satellite.add_to(m)
    analysis.add_to(m)
    SideBySideLayers(layer_left=satellite, layer_right=analysis).add_to(m)

    if eng_geom:
        folium.GeoJson(eng_geom, name="Engineered landform", tooltip="Engineered landform",
                       style_function=lambda _: {"color": ENG_COLOR, "weight": 3, "fill": False}).add_to(m)
    if ana_geom:
        folium.GeoJson(ana_geom, name="Natural analogue", tooltip="Natural analogue",
                       style_function=lambda _: {"color": ANA_COLOR, "weight": 3, "dashArray": "8 6",
                                                 "fill": False}).add_to(m)
    if footprint:
        folium.GeoJson(footprint, name="DTM footprint", tooltip="Uploaded DTM extent",
                       style_function=lambda _: {"color": "#16A34A", "weight": 1.5, "dashArray": "3 4",
                                                 "fill": False}).add_to(m)
    if (eng_src == "uploaded" and eng_geom) or (ana_src == "uploaded" and ana_geom):
        from shapely.geometry import shape
        from shapely.ops import unary_union
        b = unary_union([shape(g) for g in (eng_geom, ana_geom) if g]).bounds
        m.fit_bounds([[b[1], b[0]], [b[3], b[2]]], padding=(20, 20))
    if draw_targets:
        Draw(draw_options={"polyline": False, "rectangle": True, "circle": False,
                           "circlemarker": False, "marker": False, "polygon": True},
             edit_options={"edit": False}).add_to(m)

    map_state = st_folium(m, height=470, use_container_width=True,
                          returned_objects=["last_clicked", "last_active_drawing"])
    st.caption("Drag the divider: satellite (left) | active analysis layer (right). Click to inspect a point.")

with info_col:
    def _aoi_row(label, color, dash, g, src, note):
        line = f'border-top:3px {"dashed" if dash else "solid"} {color};'
        if g:
            val = f"{aoi_area_ha(g):,.1f} ha · {src}"
            sub = f'<div class="muted">{note}</div>'
        else:
            val = '<span style="color:#B45309;">not defined</span>'
            sub = f'<div class="muted">{note}</div>'
        return (f'<div class="legend-row" style="margin-top:4px;"><span style="width:18px;{line}"></span>'
                f'<b>{label}</b></div><div class="kv" style="margin-left:26px;">{val}</div>'
                f'<div style="margin-left:26px;">{sub}</div>')
    msgs = "".join(
        f'<div class="muted" style="margin-top:6px;color:{"#B91C1C" if lvl == "error" else "#B45309"};">'
        f'{"⛔" if lvl == "error" else "⚠"} {msg}</div>' for lvl, msg in checks)
    st.markdown(f'<div class="ui-card"><div class="card-title">Areas of interest</div>'
                f'{_aoi_row("Engineered landform", ENG_COLOR, False, eng_geom, eng_src, eng_note)}'
                f'{_aoi_row("Natural analogue", ANA_COLOR, True, ana_geom, ana_src, ana_note)}'
                f'{msgs}</div>', unsafe_allow_html=True)

    spec = DISPLAY_LAYERS[active_layer]
    if spec["type"] == "classes":
        rows = "".join(f'<div class="legend-row"><span class="swatch" style="background:{c};"></span>{lbl}</div>'
                       for lbl, c in spec["classes"])
    else:
        rows = (f'<div style="height:10px;border-radius:3px;background:linear-gradient(90deg,'
                f'{",".join(spec["ramp"])});"></div><div class="muted" style="margin-top:3px;">{spec["range"]}</div>')
    note = f'<div class="muted" style="margin-top:6px;">{spec["note"]}</div>' if "note" in spec else ""
    if active_layer.startswith("DEM of difference") and not p_run["has_dod"]:
        note += '<div class="muted" style="margin-top:6px;color:#B45309;">Upload a second survey to enable.</div>'
    src = "LiDAR / drone DTM" if p_run["dem_source"].startswith("LiDAR") else (
          "Design surface" if p_run["dem_source"].startswith("Proposed") else "AW3D30 30 m")
    st.markdown(f"""
    <div class="ui-card">
      <div class="card-title">{active_layer}</div>{rows}{note}
      <div class="muted" style="margin-top:6px;">Elevation source: {src}</div>
    </div>""", unsafe_allow_html=True)

    click = (map_state or {}).get("last_clicked")
    if click:
        # >>> GEE: ee.Image.cat([dem, slope, aspect, upstream]).reduceRegion(first, point, scale)
        body = (f"<div><b>Lat / lon:</b> {click['lat']:.4f}, {click['lng']:.4f}</div>"
                "<div><b>Elevation:</b> 485 m</div><div><b>Slope:</b> 22.3°</div>"
                "<div><b>Aspect:</b> 214° (SW)</div><div><b>Contributing area:</b> 12,500 m²</div>"
                "<div><b>Erosion susceptibility:</b> High</div>")
    else:
        body = "<div class='muted'>Click a point on the map.</div>"
    st.markdown(f'<div class="ui-card"><div class="card-title">Map inspector</div>'
                f'<div class="kv">{body}</div></div>', unsafe_allow_html=True)

    drawn = (map_state or {}).get("last_active_drawing")
    if drawn and draw_target and drawn.get("geometry", {}).get("type") == "Polygon":
        h = geom_key(drawn["geometry"])
        if h != st.session_state.get("last_drawing_key"):        # only new drawings, never re-applied
            st.session_state.last_drawing_key = h
            st.session_state.drawn["engineered" if draw_target.startswith("Engineered") else "analogue"] = \
                drawn["geometry"]
            st.rerun()


# -----------------------------------------------------------------------------
# 8. ANALYTICS TABS
# -----------------------------------------------------------------------------
def base_layout(fig, title, h=260):
    fig.update_layout(title=dict(text=title, font=dict(size=13)), height=h,
                      margin=dict(l=40, r=15, t=45, b=40), paper_bgcolor="white", plot_bgcolor="white",
                      legend=dict(orientation="h", y=1.14, x=0, font=dict(size=10)))
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor="#EEF2F7")
    return fig


def pair_lines(df, col, title, ytitle, yrange=None, h=250):
    fig = go.Figure()
    for aoi, color, dash, name in [("engineered", ENG_COLOR, None, "Engineered landform"),
                                   ("analogue", ANA_COLOR, "dash", "Natural analogue")]:
        sub = df[df["aoi"] == aoi].copy()
        sub["date"] = pd.to_datetime(sub["date"])
        sub = sub.set_index("date")[[col]].resample("MS").mean()  # empty months → gaps
        fig.add_trace(go.Scatter(x=sub.index, y=sub[col], connectgaps=False, mode="lines+markers",
                                 name=name, line=dict(color=color, width=2, dash=dash),
                                 marker=dict(size=4)))
    fig.update_yaxes(title=ytitle, range=yrange)
    return base_layout(fig, title, h)


tabs = st.tabs(["Morphometrics", "Erosion", "Design criteria", "Monitoring", "Technical brief"])

with tabs[0]:
    c1, c2, c3 = st.columns(3, gap="medium")
    with c1:
        fig = go.Figure([go.Bar(x=SLOPE_BINS, y=res["slope_eng"], name="Engineered landform", marker_color=ENG_COLOR),
                         go.Bar(x=SLOPE_BINS, y=res["slope_ana"], name="Natural analogue", marker_color=ANA_COLOR)])
        fig.update_layout(barmode="group")
        fig.update_yaxes(title="Area (%)", range=[0, 100])
        st.plotly_chart(base_layout(fig, "Slope distribution (area %, sums to 100)"), width="stretch")
    with c2:
        fig = go.Figure([
            go.Scatter(x=res["hyp_x"], y=res["hyp_eng"], mode="lines", name=f"Engineered (HI {res['hi_eng']:.2f})",
                       line=dict(color=ENG_COLOR, width=2.5)),
            go.Scatter(x=res["hyp_x"], y=res["hyp_ana"], mode="lines", name=f"Analogue (HI {res['hi_ana']:.2f})",
                       line=dict(color=ANA_COLOR, width=2.5, dash="dash"))])
        fig.update_xaxes(title="Relative area (a/A)", range=[0, 1])
        fig.update_yaxes(title="Relative height (h/H)", range=[0, 1])
        st.plotly_chart(base_layout(fig, "Hypsometric curves"), width="stretch")
    with c3:
        fig = go.Figure(go.Bar(x=["Engineered", "Analogue"], y=[res["dd_eng"], res["dd_ana"]],
                               marker_color=[ENG_COLOR, ANA_COLOR], width=[0.45, 0.45]))
        fig.update_yaxes(title="km/km²")
        fig.update_layout(showlegend=False)
        st.plotly_chart(base_layout(fig, "Drainage density"), width="stretch")

with tabs[1]:
    sub_mod, sub_meas = st.tabs(["Susceptibility (modelled)", "Measured rates"])

    with sub_mod:
        classes = ["Low", "Moderate", "High", "Very high"]
        e1, e2 = st.columns([2, 1], gap="medium")
        with e1:
            fig = go.Figure([go.Bar(x=classes, y=res["erosion_class_area_eng"], name="Engineered landform", marker_color=ENG_COLOR),
                             go.Bar(x=classes, y=res["erosion_class_area_ana"], name="Natural analogue", marker_color=ANA_COLOR)])
            fig.update_layout(barmode="group")
            fig.update_yaxes(title="Area (%)", range=[0, 100])
            st.plotly_chart(base_layout(fig, "Relative erosion susceptibility (area %)"), width="stretch")
        with e2:
            st.markdown(f"""
            <div class="ui-card"><div class="card-title">RUSLE inputs used</div><div class="kv">
              R = {p_run['r_factor']:.0f} MJ·mm/ha·h·yr ({p_run['r_source']})<br>
              K = {p_run['k_factor']:.3f} t·ha·h/ha·MJ·mm<br>
              L = {p_run['slope_len']:.1f} m · S from DEM<br>
              C from NDVI target {p_run['target_ndvi']:.2f}<br>
              Instability threshold {p_run['instability_thresh']}°</div>
            <div class="muted" style="margin-top:6px;">Relative classes until calibrated with measured rates
            (see Measured rates).</div></div>""", unsafe_allow_html=True)

    with sub_meas:
        srcs = []
        srcs.append("DoD from uploaded surveys" if ero["src"] == "surveys" else "DoD: demo values (upload two surveys)")
        srcs.append("field data: uploaded CSV" if field_src == "uploaded" else "field data: demo records")
        st.caption(" · ".join(srcs) + f" · ρb = {p_run['bulk_density']:.2f} t/m³ · "
                   f"LoD = {1.96 * np.sqrt(2) * p_run['survey_sigma']:.2f} m · Δt = {p_run['survey_years']:g} yr")

        r1, r2 = st.columns([1.75, 1], gap="medium")
        with r1:
            order_m = {"dod": 0, "trap": 1, "pin": 2, "outlet": 3}
            tbl = summary.assign(_o=summary["method"].map(order_m)).sort_values(["_o", "aoi"], ascending=[True, False])
            tbl["Method"] = tbl["method"].map(METHOD_LABEL)
            tbl["AOI"] = tbl["aoi"].str.capitalize()
            tbl["Rate (t/ha/yr)"] = tbl["rate_t_ha_yr"].map(lambda v: "—" if not np.isfinite(v) else f"{v:.3g}")
            tbl["Lowering (mm/yr)"] = tbl["rate_t_ha_yr"].map(
                lambda v: "—" if not np.isfinite(v) else f"{v / (p_run['bulk_density'] * 10):.2g}")
            st.markdown("**Erosion rates by method**")
            st.dataframe(tbl[["AOI", "Method", "Rate (t/ha/yr)", "Lowering (mm/yr)", "n", "note"]]
                         .rename(columns={"note": "Detail"}), hide_index=True, width="stretch",
                         column_config={"n": st.column_config.NumberColumn(width="small"),
                                        "Detail": st.column_config.TextColumn(width="large")})

            bench = []
            for mth in ("dod", "trap", "outlet", "pin"):
                e = summary[(summary.aoi == "engineered") & (summary.method == mth)]["rate_t_ha_yr"]
                a_ = summary[(summary.aoi == "analogue") & (summary.method == mth)]["rate_t_ha_yr"]
                if len(e) and len(a_) and np.isfinite(e.iloc[0]) and np.isfinite(a_.iloc[0]) and a_.iloc[0] > 0:
                    bench.append(f"{METHOD_LABEL[mth]}: **{e.iloc[0] / a_.iloc[0]:.0f}×** analogue")
            st.markdown("**Benchmark (same method, engineered ÷ analogue):** " +
                        ("  ·  ".join(bench) if bench else "no method measured on both sites yet."))
            d = ero["dod"].get("engineered") or {}
            if d:
                st.markdown(f"<div class='muted'>DoD, engineered: {d['erosion_m3']:,.0f} m³ eroded, "
                            f"{d['deposition_m3']:,.0f} m³ deposited, net {d['net_m3']:,.0f} m³ leaving "
                            f"{d['area_ha']:.1f} ha.</div>", unsafe_allow_html=True)
        with r2:
            fig = go.Figure()
            for aoi, color, name in [("engineered", ENG_COLOR, "Engineered landform"), ("analogue", ANA_COLOR, "Natural analogue")]:
                g = summary[summary.aoi == aoi]
                short = {"dod": "Survey<br>(DoD)", "trap": "Sediment<br>traps", "pin": "Erosion<br>pins", "outlet": "Outlet<br>yield"}
                g = g.assign(_o=g["method"].map({"dod": 0, "trap": 1, "pin": 2, "outlet": 3})).sort_values("_o")
                fig.add_trace(go.Bar(x=g["method"].map(short), y=g["rate_t_ha_yr"], name=name,
                                     marker_color=color,
                                     error_y=dict(type="data", array=g["spread"], visible=True, thickness=1)))
            fig.update_layout(barmode="group")
            fig.update_yaxes(title="t/ha/yr (log)", type="log", dtick=1)
            st.plotly_chart(base_layout(fig, "Engineered vs analogue erosion rate", h=300), width="stretch")

        r3, r4 = st.columns(2, gap="medium")
        with r3:
            ok = field[(field["issue"] == "") & field["method"].isin(["trap", "outlet"])]
            seas = ok.groupby(["season", "aoi"])["rate_t_ha_yr"].median().unstack()
            order = [x for x in ["Spring melt (May–Jun)", "Summer storms (Jul–Sep)", "Autumn / other"] if x in seas.index]
            fig = go.Figure()
            for aoi, color, name in [("engineered", ENG_COLOR, "Engineered landform"), ("analogue", ANA_COLOR, "Natural analogue")]:
                if aoi in seas:
                    fig.add_trace(go.Bar(x=order, y=seas.loc[order, aoi], name=name, marker_color=color))
            fig.update_layout(barmode="group")
            fig.update_yaxes(title="Median t/ha/yr (log)", type="log", dtick=1)
            st.plotly_chart(base_layout(fig, "Erosion rate by season (traps and outlet)", h=280), width="stretch")
        with r4:
            # >>> GEE: replace with RUSLE index sampled at each measurement plot
            rng = np.random.default_rng(3)
            idx = np.array([0.08, 0.15, 0.22, 0.35, 0.41, 0.55, 0.62, 0.78, 0.86])
            meas = idx * 95 * rng.lognormal(0, 0.25, idx.size)
            k = float((idx * meas).sum() / (idx ** 2).sum())
            r2v = 1 - ((meas - k * idx) ** 2).sum() / ((meas - meas.mean()) ** 2).sum()
            fig = go.Figure([go.Scatter(x=idx, y=meas, mode="markers", name="Measurement plots",
                                        marker=dict(color=ENG_COLOR, size=8)),
                             go.Scatter(x=[0, 1], y=[0, k], mode="lines", name=f"Calibration: {k:.0f}·index (R² {r2v:.2f})",
                                        line=dict(color="#DC2626", dash="dot"))])
            fig.update_xaxes(title="RUSLE relative index", range=[0, 1])
            fig.update_yaxes(title="Measured t/ha/yr")
            st.plotly_chart(base_layout(fig, "RUSLE calibration (demo plots)", h=280), width="stretch")

        st.markdown("**Proposed monitoring locations** — hotspots on the engineered landform, each paired "
                    "with a comparable position on the analogue")
        # >>> GEE: hotspots = connected components of 'Very high' susceptibility; pairs matched on slope/aspect/area
        from shapely.geometry import shape as _shape
        e_c = list(_shape(eng_geom).centroid.coords)[0] if eng_geom else (lon0, lat0)
        a_c = list(_shape(ana_geom).centroid.coords)[0] if ana_geom else (lon0, lat0)
        plan = pd.DataFrame([
            ["E1", "Engineered", e_c[1] + 0.0021, e_c[0] - 0.0030, 24, "Very high", "Sediment trap + erosion pins", "A1"],
            ["E2", "Engineered", e_c[1] - 0.0018, e_c[0] + 0.0012, 21, "Very high", "Sediment trap + erosion pins", "A2"],
            ["E3", "Engineered", e_c[1] + 0.0004, e_c[0] + 0.0031, 17, "High", "Erosion pin transect", "A3"],
            ["E-out", "Engineered", e_c[1] - 0.0032, e_c[0] + 0.0040, 3, "Outlet", "Turbidity logger + flow gauge", "A-out"],
            ["A1", "Analogue", a_c[1] + 0.0015, a_c[0] - 0.0020, 15, "Reference", "Sediment trap", "E1"],
            ["A2", "Analogue", a_c[1] - 0.0012, a_c[0] + 0.0018, 13, "Reference", "Sediment trap", "E2"],
            ["A3", "Analogue", a_c[1] + 0.0025, a_c[0] + 0.0030, 12, "Reference", "Erosion pin transect", "E3"],
            ["A-out", "Analogue", a_c[1] - 0.0035, a_c[0] + 0.0045, 2, "Outlet", "Turbidity logger + flow gauge", "E-out"],
        ], columns=["ID", "AOI", "Lat", "Lon", "Slope (°)", "Susceptibility", "Instrument", "Paired with"])
        st.dataframe(plan.style.format({"Lat": "{:.5f}", "Lon": "{:.5f}"}), hide_index=True, width="stretch")

        f1, f2, f3 = st.columns([1.2, 1.2, 3])
        f1.download_button("Download monitoring plan (CSV)", plan.to_csv(index=False), "monitoring_plan.csv",
                           "text/csv", width="stretch")
        f2.download_button("Download field-data template", FIELD_TEMPLATE.to_csv(index=False),
                           "field_data_template.csv", "text/csv", width="stretch")
        with st.expander("Upload field data (sediment traps, erosion pins, outlet loads)"):
            st.file_uploader("Field data CSV", type=["csv"], key="field_csv",
                             help="Columns: date, aoi (engineered/analogue), method (trap/pin/outlet), value, "
                                  "unit (kg/mm/t), contributing_area_ha, period_days")
            bad = field[field["issue"] != ""]
            if len(bad):
                st.warning(f"{len(bad)} record(s) skipped: missing contributing area, period or unknown method.")

with tabs[2]:
    crit = res["criteria"]
    a, b = st.columns([3, 1], gap="medium")
    with a:
        st.markdown("**Design inputs from the natural analogue** — distributions, not single means, "
                    "with the engineered surface checked against the P10–P90 envelope.")

        def _hl(row):
            bad = row["Design check"] == "Outside range"
            return ["background-color:#FEF2F2;color:#991B1B" if (bad and c in ("Engineered", "Design check"))
                    else "" for c in row.index]

        st.dataframe(crit.style.apply(_hl, axis=1).format(precision=2), hide_index=True, width="stretch")
        if not p_run["dem_source"].startswith(("LiDAR", "Proposed")):
            st.warning("Analysed on 30 m data: channel heads, ridge-to-head distance and hillslope length "
                       "are indicative only. Upload LiDAR for both sites before using them in design.")
    with b:
        fig = go.Figure()
        for i, r in crit.iloc[::-1].reset_index(drop=True).iterrows():
            span = r["Analogue P90"] - r["Analogue P10"]
            pos = lambda v: (v - r["Analogue P10"]) / span if span else 0.5
            fig.add_trace(go.Scatter(x=[0, 1], y=[i, i], mode="lines", line=dict(color="#BFDBFE", width=8),
                                     showlegend=False, hoverinfo="skip"))
            fig.add_trace(go.Scatter(x=[pos(r["Analogue median"])], y=[i], mode="markers",
                                     marker=dict(color=ANA_COLOR, size=8), showlegend=False,
                                     hovertext=f"Analogue median {r['Analogue median']}"))
            fig.add_trace(go.Scatter(x=[float(np.clip(pos(r["Engineered"]), -0.6, 1.6))], y=[i], mode="markers",
                                     marker=dict(color=ENG_COLOR if r["Design check"].startswith("Within") else "#DC2626",
                                                 size=10, symbol="diamond"), showlegend=False,
                                     hovertext=f"Engineered {r['Engineered']}"))
        fig.update_yaxes(tickvals=list(range(len(crit))), ticktext=list(crit["Parameter"][::-1]),
                         tickfont=dict(size=9), showgrid=False)
        fig.update_xaxes(tickvals=[0, 1], ticktext=["P10", "P90"], range=[-0.7, 1.7])
        st.plotly_chart(base_layout(fig, "Engineered vs analogue envelope", h=330), width="stretch")

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        crit.to_excel(xw, sheet_name="Design criteria", index=False)
        pd.DataFrame([{**{k: str(v) for k, v in p_run.items()}}]).T.rename(columns={0: "value"}) \
          .to_excel(xw, sheet_name="Run parameters")
    d1, d2, _ = st.columns([1, 1, 3])
    d1.download_button("Download CSV", crit.to_csv(index=False), "design_criteria.csv", "text/csv", width="stretch")
    d2.download_button("Download XLSX", buf.getvalue(), "design_criteria.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch")

with tabs[3]:
    s2, s1 = mon["s2"], mon["s1"]
    src_note = "Live Earth Engine series" if mon["live"] else "Demo series — add Earth Engine credentials for live data"
    st.caption(f"{src_note} · {p_run['mon_years'][0]}–{p_run['mon_years'][1]} · "
               "Sentinel-2: growing season (Jun–Sep), Cloud Score+ masked · "
               "Sentinel-1: single relative orbit, monthly median")
    m1, m2 = st.columns(2, gap="medium")
    with m1:
        st.plotly_chart(pair_lines(s2, "ndvi", "Sentinel-2 NDVI (season months)", "NDVI", [0, 0.9]), width="stretch")
    with m2:
        st.plotly_chart(pair_lines(s2, "bare_fraction", "Bare-soil fraction (NDVI < 0.2 and BSI > 0)",
                                   "Share of AOI", [0, 1]), width="stretch")
    m3, m4 = st.columns(2, gap="medium")
    with m3:
        st.plotly_chart(pair_lines(s1, "vh_db", "Sentinel-1 VH backscatter (all-weather)", "dB"), width="stretch")
    with m4:
        st.plotly_chart(pair_lines(s1, "rvi", "Radar Vegetation Index 4·VH/(VV+VH)", "RVI", [0, 1]), width="stretch")

    if not s2.empty:
        last_year = s2["date"].str[:4].max()
        ly = s2[s2["date"].str.startswith(last_year)].groupby("aoi")["ndvi"].mean()
        if {"engineered", "analogue"} <= set(ly.index):
            st.columns([1, 2])[0].metric(f"Recovery ratio {last_year} (engineered NDVI ÷ analogue NDVI)",
                      f"{ly['engineered'] / ly['analogue']:.2f}")

with tabs[4]:
    try:
        crit_md = crit.to_markdown(index=False)
    except ImportError:  # tabulate not installed
        crit_md = "```\n" + crit.to_string(index=False) + "\n```"
    memo = f"""# Technical brief — {p_run['site']}
Prepared with {APP_NAME} ({ORG}) · {date.today().isoformat()}
Status: {"Live Earth Engine monitoring" if mon["live"] else "DRAFT — demo values, backend not connected"}

## Inputs
- Elevation source: {p_run['dem_source']}
- Cover material: {p_run['cover_type']}
- R = {p_run['r_factor']:.0f} MJ·mm/ha·h·yr ({p_run['r_source']}); K = {p_run['k_factor']:.3f}; L = {p_run['slope_len']:.1f} m
- Target NDVI {p_run['target_ndvi']:.2f}; instability threshold {p_run['instability_thresh']}°; channel initiation {p_run['channel_thresh']} m²
- Monitoring period {p_run['mon_years'][0]}–{p_run['mon_years'][1]}

## Engineered landform vs natural analogue
| Metric | Engineered | Analogue |
|---|---|---|
| Hypsometric integral | {res['hi_eng']:.2f} | {res['hi_ana']:.2f} |
| Mean slope (°) | {res['mean_slope_eng']:.1f} | {res['mean_slope_ana']:.1f} |
| Area > {p_run['instability_thresh']}° (%) | {res['above_thr_eng']} | {res['above_thr_ana']} |
| Drainage density (km/km²) | {res['dd_eng']:.1f} | {res['dd_ana']:.1f} |
| Relative soil loss | {res['rel_soil_loss_eng']} | {res['rel_soil_loss_ana']} |

## Measured erosion rates
- Engineered landform: {eng_rate:.3g} t/ha/yr ({eng_m})
- Natural analogue: {ana_rate:.2g} t/ha/yr ({ana_m})
- Bulk density {p_run['bulk_density']:.2f} t/m³; survey LoD {1.96 * np.sqrt(2) * p_run['survey_sigma']:.2f} m over {p_run['survey_years']:g} years

## Design criteria and design check ({n_flags} of {len(crit)} parameters outside the analogue range)
{crit_md}

## Limitations
- RUSLE classes are relative unless calibrated with measured rates.
- DoD rates ignore change below the level of detection; slow diffuse erosion is under-represented.
- Design criteria from 30 m data are indicative; use LiDAR for both sites before design use.
- Sentinel-1 indicators are relative trends within one orbit, interpreted alongside the analogue.
"""
    st.markdown(memo)
    st.download_button("Download technical brief (.md)", memo, "technical_brief.md", "text/markdown")
