# GeoClosure Explorer

Geomorphic landform assessment and design support for mine closure: engineered landform vs natural analogue,
design criteria from reference sites, erosion susceptibility and measured erosion rates, and Sentinel-2 /
Sentinel-1 monitoring.

Quantum Geomatics · portfolio demonstration

## Project layout

All files sit in one folder, so the repository can be uploaded through GitHub's web interface.

```
app.py                   Streamlit interface
gee_connection.py        Earth Engine connection and dataset checks
check_gee.py             Command-line connection test
test_gee_connection.py   pytest tests (run without network or credentials)
config.toml              Streamlit settings (see "Streamlit settings" below)
secrets.toml.example     Template for credentials (same format as the ECDS app)
requirements.txt
README.md
.gitignore
```

## Streamlit settings

Streamlit only reads settings from a `.streamlit` folder. To apply `config.toml` (it raises the upload limit to
500 MB for LiDAR files):

- **On GitHub:** Add file → Create new file, type `.streamlit/config.toml` as the name (GitHub creates the folder
  from the slash), and paste in the contents of `config.toml`.
- **Locally:** create a `.streamlit` folder next to `app.py` and copy `config.toml` into it.

Without it the app still runs, with Streamlit's default 200 MB upload limit.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m pytest -q          # should report all tests passed
```

## Connect Earth Engine

You need a Google Cloud project registered for Earth Engine
(https://code.earthengine.google.com/register). Choose the usage type that fits how you use the app.
Then use **one** of the two options below.

### Option A: your own Google login (quickest for local development)

```bash
earthengine authenticate
python check_gee.py --project your-google-cloud-project-id
streamlit run app.py
```

### Option B: service account (needed for Streamlit Community Cloud)

GeoClosure uses **the same secrets format as the ECDS app**, so if ECDS already connects, reuse its
secrets unchanged:

```toml
EE_PROJECT = "your-google-cloud-project-id"
EE_SERVICE_ACCOUNT_JSON = '''{ ...entire service-account JSON key, pasted verbatim... }'''
```

Locally, create a `.streamlit` folder next to `app.py` and save this as `.streamlit/secrets.toml`
(template: `secrets.toml.example`).
On Streamlit Community Cloud, paste it into App settings → Secrets. To test a key file from the command line:

```bash
python check_gee.py --key /path/to/key.json --project your-google-cloud-project-id
```

Never commit the key file or `secrets.toml` (both are in `.gitignore`). Connection errors shown in the app are
redacted; full detail is in the server log under the diagnostic ID shown.

### What the check reports

`check_gee.py` connects, then runs one small query against each dataset the app uses:
AW3D30 elevation, Sentinel-2 SR, Cloud Score+, Sentinel-1 GRD and ERA5-Land precipitation, all near Kiruna.
In the app, the same test is under **System check → Test datasets** in the sidebar.

Exit codes: `0` all checks passed · `1` couldn't connect · `2` connected but a dataset check failed.

## Status of components

| # | Component | Status |
|---|---|---|
| 1 | Project structure and Earth Engine connection | Done |
| 2 | Area of interest module | Next |
| 3 | DEM retrieval (AW3D30 / LiDAR) | Planned |
| 4 | Terrain derivatives | Planned |
| 5 | Hydrology | Planned |
| 6 | Morphometrics | Planned |
| 7 | Design criteria | Planned |
| 8 | RUSLE | Planned |
| 9 | Map layers | Planned |
| 10 | Monitoring (Sentinel-2 / Sentinel-1) | Planned |
| 11 | Erosion rates, report, deployment | Planned |

Until a component is done, the app shows clearly labelled demo values for it.
