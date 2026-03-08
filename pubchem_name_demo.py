import argparse
import csv
import os
import re
import urllib.parse

import requests


def normalize_name(name: str) -> str:
    s = str(name or "")
    s = s.replace("′", "'").replace("’", "'").replace("–", "-").replace("—", "-")
    s = s.replace("−", "-").replace("\u00a0", " ")
    s = " ".join(s.split())
    s = s.strip(" ;,")
    return s


def pubchem_name_url(name: str) -> str:
    encoded = urllib.parse.quote(urllib.parse.unquote(name), safe="")
    return (
        "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
        f"{encoded}/property/IsomericSMILES,CanonicalSMILES,ConnectivitySMILES/JSON"
    )


def pick_smiles_from_json(data: dict) -> str:
    props = (data.get("PropertyTable") or {}).get("Properties") or []
    if not props:
        return ""
    prop = props[0] or {}
    for k in ("IsomericSMILES", "CanonicalSMILES", "ConnectivitySMILES", "SMILES"):
        v = prop.get(k)
        if v:
            return str(v)
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--row", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=15.0)
    ap.add_argument("--no-proxy", action="store_true")
    args = ap.parse_args()

    with open(args.csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        row = None
        for i, r in enumerate(reader, 1):
            if i == args.row:
                row = r
                break
    if not row:
        raise SystemExit(f"row {args.row} not found in {args.csv}")

    raw = row.get("full_name") or ""
    norm = normalize_name(raw)
    url = pubchem_name_url(norm)

    print("csv:", args.csv)
    print("row:", args.row)
    print("env HTTP_PROXY:", os.getenv("HTTP_PROXY") or "")
    print("env HTTPS_PROXY:", os.getenv("HTTPS_PROXY") or "")
    print("env http_proxy:", os.getenv("http_proxy") or "")
    print("env https_proxy:", os.getenv("https_proxy") or "")
    print("full_name raw:", raw)
    print("full_name normalized:", norm)
    print("pubchem url:", url)

    session = requests.Session()
    if args.no_proxy:
        session.trust_env = False

    try:
        res = session.get(url, timeout=args.timeout)
    except Exception as e:
        print("request error:", repr(e))
        raise SystemExit(2)

    print("status:", res.status_code)
    print("content-type:", res.headers.get("Content-Type") or "")
    head = re.sub(r"\\s+", " ", (res.text or "")[:300]).strip()
    print("body head:", head)
    if res.status_code != 200:
        raise SystemExit(1)

    data = res.json()
    smiles = pick_smiles_from_json(data)
    cid = ""
    try:
        props = (data.get("PropertyTable") or {}).get("Properties") or []
        if props:
            cid = str((props[0] or {}).get("CID") or "")
    except Exception:
        cid = ""
    print("CID:", cid)
    print("SMILES:", smiles)


if __name__ == "__main__":
    main()
