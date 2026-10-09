"""One-time geocode of the NHAI office list, so hazards can be matched to the nearest office.

    python -m tools.geocode_authorities          # data/authorities/*.csv -> data/authorities.json

Each office is looked up by the PIN code in its address (precise), falling back to city + state.
Uses public OSM Nominatim at <= 1 request/second; offices already in the JSON are skipped, so
re-running only fills gaps.
"""
import csv
import json
import re
import time
import urllib.parse
import urllib.request

from hazardmap.config import ROOT

SRC = ROOT / "data" / "authorities" / "india_road_authority_dataset.csv"
OUT = ROOT / "data" / "authorities.json"
UA = "TerraTrace-hackathon/0.1 (road hazard mapping demo)"


def _search(**params) -> tuple[float, float] | None:
    time.sleep(1.05)                                     # Nominatim policy: <= 1 request/second
    q = urllib.parse.urlencode({**params, "countrycodes": "in", "format": "jsonv2", "limit": 1})
    req = urllib.request.Request(f"https://nominatim.openstreetmap.org/search?{q}", headers={"User-Agent": UA})
    hits = json.load(urllib.request.urlopen(req, timeout=10))
    return (float(hits[0]["lat"]), float(hits[0]["lon"])) if hits else None


def _split(v: str, sep: str) -> list[str]:
    return [x.strip() for x in re.split(sep, v or "") if x.strip()]


def main():
    done = {o["office_code"]: o for o in json.loads(OUT.read_text())} if OUT.exists() else {}
    out = []
    with open(SRC, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    for i, r in enumerate(rows, 1):
        code = r["office_code"].strip()
        prev = done.get(code)
        if prev and prev.get("lat") is not None:
            out.append(prev)
            continue
        pin = (re.findall(r"\b(\d{6})\b", r["address"]) or [None])[-1]
        city, state = r["city"].strip(), r["state_ut"].strip()
        pos, precision = None, None
        try:
            if pin:
                pos, precision = _search(postalcode=pin, country="India"), "pin"
            if pos is None:
                pos, precision = _search(city=city, state=state, country="India"), "city"
            if pos is None:
                pos, precision = _search(q=f"{city}, India"), "city"
        except OSError as e:
            print(f"  {code}: lookup failed ({e}); re-run to retry")
        rec = {
            "office_code": code, "office_type": r["office_type"].strip(), "city": city, "state": state,
            "designation": r["designation"].strip(), "officer_name": r["officer_name"].strip() or None,
            "address": r["address"].strip(),
            # "0712-2420322 / 2420355": the extra numbers share the first one's STD code
            "phones": _expand_phones(r["phone"]),
            "emails": _split(r["email"], r"[;,/\s]+"),
            "note": r["note"].strip() or None, "source": r["source"].strip(), "data_as_of": r["data_as_of"].strip(),
            "lat": pos[0] if pos else None, "lon": pos[1] if pos else None,
            "geo_precision": precision if pos else None,
        }
        out.append(rec)
        print(f"[{i}/{len(rows)}] {code}: {precision if pos else 'NOT FOUND'}")
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    missing = [o["office_code"] for o in out if o["lat"] is None]
    print(f"{len(out) - len(missing)}/{len(out)} offices located -> {OUT.relative_to(ROOT)}"
          + (f"; missing: {', '.join(missing)}" if missing else ""))


def _expand_phones(v: str) -> list[str]:
    parts = _split(v, r"[/,;]")
    std = parts[0].split("-")[0] if parts and "-" in parts[0] else None
    return [p if "-" in p or not std or len(p) > 8 else f"{std}-{p}" for p in parts]


if __name__ == "__main__":
    main()
