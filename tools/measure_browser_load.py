"""Put a number on each stage of the browser's load, to name the one that is slow.

`tools/bench_viewport.py` shows the server is not the problem: it reopens the 4,500-part project in
0.59 s and answers every mesh request in 217 ms warm. `tools/bench_scene.py` reports 36 s from the
page arriving to all parts drawn. The difference is inside the browser, and the loader marks
(`web/src/viewport/parts.ts`, `window.__stokesLoad`) say where.

Stages, in the page's own clock:
  nav-ish   the harness's first successful probe of the page
  tree      the tree arrived, so the page knows what to fetch
  first     the first part's mesh was applied to the scene
  all       every part the tree names has been applied

    python tools/measure_browser_load.py --project <folder.stokes> [--runs 3]

The mark is set by the app itself, so this measures the shipped path, not a copy of it.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench_scene import Chrome, DevTools, find_browser
from bench_viewport import Server


def one_run(project: Path, browser: Path, expect: int, timeout: float) -> dict[str, Any]:
    """One cold load: a fresh server and a fresh page, with the loader's own marks read back."""
    server = Server(project)
    chrome: Chrome | None = None
    try:
        page = f"http://127.0.0.1:{server.port}/?token={server.token}&debug=1"
        chrome = Chrome(browser, page, swiftshader=False)
        dev = DevTools(chrome.page_url)
        try:
            began = time.monotonic()
            probe_ms: float | None = None
            result: dict[str, Any] | None = None
            while time.monotonic() - began < timeout:
                snap = dev.evaluate(
                    "(() => { const l = window.__stokesLoad; const p = window.__stokesScene;"
                    " return {marks: l ? l.marks : null, scene: p ? p.perf() : null}; })()"
                )
                if isinstance(snap, dict) and snap.get("scene"):
                    if probe_ms is None:
                        probe_ms = (time.monotonic() - began) * 1000
                    marks = snap.get("marks") or {}
                    stats = snap["scene"]
                    if int(stats.get("parts", 0)) >= expect:
                        result = {"marks": marks, "scene": stats, "probe_ms": round(probe_ms, 1)}
                        break
                time.sleep(0.25)
            if result is None:
                raise SystemExit(f"the page never drew {expect} parts within {timeout:.0f} s")
            return result
        finally:
            dev.close()
    finally:
        if chrome is not None:
            chrome.stop()
        server.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="measure-browser-load", description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--expect", type=int, default=4500)
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args(argv)

    browser = find_browser()
    rows: list[dict[str, Any]] = []
    for run in range(args.runs):
        got = one_run(args.project, browser, args.expect, args.timeout)
        marks = got["marks"]
        tree = marks.get("tree")
        first = marks.get("firstMesh")
        allm = marks.get("allMeshes")
        scene = got["scene"]
        row = {
            "run": run + 1,
            "parts": scene.get("parts"),
            "triangles": scene.get("triangles"),
            "batches": scene.get("batches"),
            "probe_ms": got["probe_ms"],
            "tree_ms": round(tree, 1) if tree else None,
            "first_mesh_ms": round(first, 1) if first else None,
            "all_meshes_ms": round(allm, 1) if allm else None,
            "apply_ms": round(allm - tree, 1) if allm and tree else None,
            "first_part_after_tree_ms": round(first - tree, 1) if first and tree else None,
        }
        rows.append(row)
        print(
            f"run {row['run']}: tree {row['tree_ms']} ms | "
            f"first part +{row['first_part_after_tree_ms']} ms | "
            f"all parts {row['all_meshes_ms']} ms | applying cost {row['apply_ms']} ms | "
            f"{row['parts']} parts, {row['triangles']} triangles, {row['batches']} batches"
        )
    if len(rows) > 1:
        for key in ("tree_ms", "all_meshes_ms", "apply_ms"):
            values = [float(r[key]) for r in rows if r.get(key) is not None]
            if values:
                print(f"  {key}: p50 {round(statistics.median(values), 1)} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
