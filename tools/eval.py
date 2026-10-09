"""Zero-config accuracy check: every clip runs with only its camera *type* and must find what
is really there (`present`) and nothing it shouldn't (`absent`).

    python -m tools.eval            # all clips in data/eval.json
    python -m tools.eval --only india-nh3-flood
"""
import argparse
import json
from pathlib import Path

from hazardmap.config import ROOT, PipelineConfig, apply_preset
from hazardmap.pipeline import run


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", default=str(ROOT / "data" / "eval.json"))
    ap.add_argument("--only")
    ap.add_argument("--out", default=str(ROOT / "runs"))
    a = ap.parse_args()

    clips = [c for c in json.loads(Path(a.spec).read_text())["clips"] if not a.only or c["name"] == a.only]
    checks = passed = 0
    rows = []
    for c in clips:
        cfg = PipelineConfig(camera_preset=c["camera"])
        apply_preset(cfg.camera, c["camera"])
        s = run(str(ROOT / c["video"]), str(ROOT / c["gps"]), Path(a.out) / f"eval-{c['name']}", cfg)
        found = s["by_category"]
        results = [(f"{cat} present", found.get(cat, 0) > 0) for cat in c["present"]]
        results += [(f"{cat} absent", found.get(cat, 0) == 0) for cat in c["absent"]]
        checks += len(results)
        passed += sum(ok for _, ok in results)
        cal = s.get("calibration") or {}
        rows.append((c["name"], found, results, cal))

    for name, found, results, cal in rows:
        bad = [r for r, ok in results if not ok]
        print(f"\n{'PASS' if not bad else 'FAIL'}  {name}")
        print(f"   found: {found or '{}'}")
        print(f"   camera: pitch {cal.get('pitch_deg')}° ({cal.get('source')}, horizon {cal.get('horizon')}), hood {cal.get('hood_frac')}")
        for r in bad:
            print(f"   ✗ {r}")
    print(f"\n{passed}/{checks} checks passed ({100 * passed / max(checks, 1):.0f}%)")


if __name__ == "__main__":
    main()
