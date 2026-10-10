"""Download the still-image test set for fallen trees and fallen poles (Wikimedia Commons,
CC/public-domain) into data/eval_images/<category>/ with an ATTRIBUTION.md.

    python -m tools.fetch_eval_images

Videos of these hazards are rare, so tools/eval_images.py checks the detector frame-by-frame on
these photos (must be found) and on frames of our own clean dashcam footage (must not be).
Downloads are throttled to respect Wikimedia's rate limits; existing files are skipped.
"""
import json
import time
import urllib.parse
import urllib.request

from hazardmap.config import ROOT

OUT = ROOT / "data" / "eval_images"
UA = {"User-Agent": "TerraTrace-hackathon/0.1 (road-hazard detector test set)"}
FILES = {
    "fallen_tree": [
        "Fallen Tree in Dormer Place, Leamington Spa (1).jpg",
        "Fallen tree, Balcombe Road, Crawley - geograph.org.uk - 3719094.jpg",
        "Moscow, Donskaya Street, fallen tree June 2022 02.jpg",
        "Road blocked by fallen tree - geograph.org.uk - 2927837.jpg",
        "2020aug10-derecho-damage-AAnsorge-MerleHay-DSM.jpg",
        "Casualty of Storm Arwen (geograph 7031616).jpg",
        "Fallen trees on Oregon 18 (6391503995).jpg",
        "Wyncham Avenue October 1987 - geograph.org.uk - 15221.jpg",
    ],
    "power_line": [
        "19106 gov stormTouring dz 009 (51422419253).jpg",
        "Collapsed power pole.jpg",
        "Fallen power poles in Ishinomaki.jpg",
        "Fallen power poles in Onahama.jpg",
        "Fallen telegraph pole - geograph.org.uk - 5407224.jpg",
        "Wilma-florida-fallen-power-pole.jpg",
        "Electric Pole Snapped.jpg",
        "May 2022 storm Uplands mosbo6.jpg",
    ],
}


def _info(title: str) -> dict:
    q = urllib.parse.urlencode({"action": "query", "format": "json", "titles": f"File:{title}", "prop": "imageinfo",
                                "iiprop": "url|extmetadata", "iiurlwidth": 1280})
    d = json.load(urllib.request.urlopen(urllib.request.Request(f"https://commons.wikimedia.org/w/api.php?{q}", headers=UA), timeout=20))
    page = next(iter(d["query"]["pages"].values()))
    ii = page["imageinfo"][0]
    meta = ii.get("extmetadata", {})
    strip = lambda k: urllib.parse.unquote(meta.get(k, {}).get("value", "")).replace("\n", " ")
    import re
    return {"url": ii.get("thumburl") or ii["url"], "page": ii["descriptionurl"], "license": strip("LicenseShortName"),
            "author": re.sub(r"<[^>]+>", "", strip("Artist")).strip()[:120]}


def main():
    credits = []
    for cat, titles in FILES.items():
        (OUT / cat).mkdir(parents=True, exist_ok=True)
        for i, title in enumerate(titles, 1):
            dst = OUT / cat / f"{cat}_{i:02d}.jpg"
            try:
                info = _info(title)
                time.sleep(2)
                if not dst.exists():
                    dst.write_bytes(urllib.request.urlopen(urllib.request.Request(info["url"], headers=UA), timeout=60).read())
                    time.sleep(3)
                credits.append(f"| {dst.relative_to(OUT)} | [{title}]({info['page']}) | {info['author'] or 'unknown'} | {info['license']} |")
                print(f"ok   {dst.relative_to(ROOT)}")
            except Exception as e:                     # rate limit / network: re-run later to fill gaps
                print(f"FAIL {title}: {e}")
                time.sleep(10)
    (OUT / "ATTRIBUTION.md").write_text("# Test images (Wikimedia Commons)\n\n| File | Source | Author | License |\n|---|---|---|---|\n"
                                        + "\n".join(credits) + "\n")


if __name__ == "__main__":
    main()
