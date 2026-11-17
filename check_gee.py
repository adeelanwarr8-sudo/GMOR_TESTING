"""
Check that Earth Engine works with your credentials, outside Streamlit.

Usage (from the project folder):
    python check_gee.py                      # uses .streamlit/secrets.toml (ECDS format) if present
    python check_gee.py --project my-ee-project
    python check_gee.py --key path/to/key.json
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gee_connection import connection_test, init_ee  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="GeoClosure Explorer: Earth Engine connection check")
    ap.add_argument("--project", help="Google Cloud project registered for Earth Engine")
    ap.add_argument("--key", help="Path to a service-account JSON key file")
    args = ap.parse_args()
    if args.key:
        os.environ["GEE_SERVICE_ACCOUNT_FILE"] = args.key

    secrets = {}
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".streamlit", "secrets.toml")
    if os.path.exists(path) and not args.key:
        import tomllib
        with open(path, "rb") as fh:
            secrets = tomllib.load(fh)
        print("Using .streamlit/secrets.toml")
    status = init_ee(project=args.project, secrets=secrets)
    print(f"\nConnection: {'OK' if status.ok else 'FAILED'}  ({status.method})")
    print(f"Project:    {status.project}")
    print(f"Message:    {status.message}")
    if status.attempts:
        print("Attempts:")
        for a in status.attempts:
            print(f"  - {a}")
    if not status.ok:
        sys.exit(1)

    print("\nDataset checks:")
    results = connection_test()
    for name, r in results.items():
        mark = "OK  " if r["ok"] else "FAIL"
        detail = r["value"] if r["ok"] else r["error"]
        if isinstance(detail, float):
            detail = round(detail, 1)
        print(f"  [{mark}] {name}: {detail}  ({r['seconds']} s)")
    sys.exit(0 if all(r["ok"] for r in results.values()) else 2)


if __name__ == "__main__":
    main()
