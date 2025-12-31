#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

r"""
sync_option_a.py  (Option A: Local -> GitHub Pages repo sync)

Goal
----
Copy HealthCard exports from a local folder into the Git repo layout expected by GitHub Pages:

  repo/
    index.html
    data/
      sites.geojson
      healthcards/
        <many>.html
        <many>.png

Supports:
- One-site export folder containing HealthCard.html (and images in same folder or subfolders)
- Multi-site export folder containing many .html and images

Key features:
- Recursively copies images (png/jpg/jpeg/svg/gif) from --src into repo/data/healthcards
- Copies HealthCard.html into repo/data/healthcards/<generated>.html
- Optionally generates a stub sites.geojson from --lat/--lon
- Optional --clean to wipe old repo/data/healthcards and repo/data/sites.geojson
- Optional --commit / --push

Example
-------
python sync_option_a.py --src "C:\path\to\ONE_SITE_EXPORT" --lat 39.35986 --lon -77.3092 --clean --commit --push --message "Add one MBSS HealthCard"
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional, Tuple


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp"}
HTML_NAMES = {"HealthCard.html", "HealthCard.htm", "healthcard.html", "healthcard.htm"}


def eprint(*args):
    print(*args, file=sys.stderr)


def run(cmd: list[str], cwd: Path) -> int:
    p = subprocess.run(cmd, cwd=str(cwd), shell=False)
    return int(p.returncode)


def safe_mkdir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def remove_tree(p: Path) -> None:
    if p.exists():
        shutil.rmtree(str(p), ignore_errors=True)


def remove_file(p: Path) -> None:
    try:
        if p.exists():
            p.unlink()
    except Exception:
        pass


def copy_file(src: Path, dst: Path) -> None:
    src = src.resolve()
    dst_parent = dst.parent
    safe_mkdir(dst_parent)

    # If src and dst are the same file, skip (prevents WinError 32 and self-copy)
    try:
        if src.samefile(dst):
            print(f"[SKIP] {src} is same as {dst}")
            return
    except Exception:
        pass

    shutil.copy2(str(src), str(dst))


def iter_files_recursive(root: Path) -> Iterable[Path]:
    for p in root.rglob("*"):
        if p.is_file():
            yield p


def find_one_healthcard_html(src_root: Path) -> Optional[Path]:
    # Prefer exact names
    for name in HTML_NAMES:
        p = src_root / name
        if p.exists() and p.is_file():
            return p
    # Otherwise, find the first HTML file that looks like a healthcard
    for p in src_root.rglob("*.html"):
        try:
            txt = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if "MBSS HealthCard" in txt or "BIBI" in txt and "FIBI" in txt and "Watershed context" in txt:
            return p
    return None


def parse_basic_fields_from_html(html_text: str) -> dict:
    # Best-effort parsing; if it fails, we still generate a minimal point.
    out = {}

    # SITEYR patterns (several possible formats)
    m = re.search(r"\b([A-Z0-9]{2,}-\d{1,4}-[A-Z]-\d{4})\b", html_text)
    if m:
        out["SITEYR"] = m.group(1)

    # Stream name (very heuristic)
    m = re.search(r"Stream name:\s*<\/[^>]+>\s*([^<]+)<", html_text, flags=re.I)
    if m:
        out["STREAMNAME"] = m.group(1).strip()

    # Province
    m = re.search(r"Province\/Physio:\s*<\/[^>]+>\s*([^<]+)<", html_text, flags=re.I)
    if m:
        out["PROVINCE"] = m.group(1).strip()

    # MDE8 + DNR12DIG
    m = re.search(r"\bMDE8:\s*<\/[^>]+>\s*([0-9]{7})\b", html_text, flags=re.I)
    if m:
        out["MDE8"] = m.group(1)
    m = re.search(r"\bDNR12DIG:\s*<\/[^>]+>\s*([0-9]{12})\b", html_text, flags=re.I)
    if m:
        out["DNR12DIG"] = m.group(1)

    # BIBI/FIBI in the summary panel often appear as numbers near labels
    # We'll try a few patterns.
    def grab_number(label: str) -> Optional[float]:
        pats = [
            rf"{label}\s*<\/[^>]+>\s*([0-9]+(\.[0-9]+)?)<",
            rf"{label}\s*[:=]\s*([0-9]+(\.[0-9]+)?)",
        ]
        for pat in pats:
            mm = re.search(pat, html_text, flags=re.I)
            if mm:
                try:
                    return float(mm.group(1))
                except Exception:
                    return None
        return None

    bibi = grab_number("BIBI")
    fibi = grab_number("FIBI")
    if bibi is not None:
        out["BIBI"] = bibi
    if fibi is not None:
        out["FIBI"] = fibi

    # Year (try to find a 4-digit year near "Year")
    m = re.search(r"Year\s*<\/[^>]+>\s*([12][0-9]{3})<", html_text, flags=re.I)
    if m:
        out["YEAR"] = int(m.group(1))

    return out


def extract_img_srcs(html_text: str) -> list[str]:
    # Extract img src="..."
    srcs = re.findall(r'<img[^>]+src\s*=\s*"(.*?)"', html_text, flags=re.I)
    srcs += re.findall(r"<img[^>]+src\s*=\s*'(.*?)'", html_text, flags=re.I)
    # Keep only relative-ish paths (ignore http(s) data URIs)
    cleaned = []
    for s in srcs:
        s = s.strip()
        if s.startswith("http://") or s.startswith("https://") or s.startswith("data:"):
            continue
        if s == "":
            continue
        cleaned.append(s)
    return cleaned


def patch_img_paths_to_healthcards_folder(html_text: str) -> str:
    """
    If HealthCard HTML references images like "pdp_XXX.png" or "plots/pdp_XXX.png",
    we want them to resolve when the HTML is moved into data/healthcards/.

    Strategy: rewrite any relative src to just basename (we copy all images into same folder).
    """
    def repl(match):
        path = match.group(1) or match.group(2) or ""
        if path.startswith("http") or path.startswith("data:"):
            return match.group(0)
        base = os.path.basename(path)
        return match.group(0).replace(path, base)

    # handle src="..." and src='...'
    html_text = re.sub(r'<img([^>]+)src\s*=\s*"(.*?)"', lambda m: '<img' + m.group(1) + 'src="' + os.path.basename(m.group(2)) + '"', html_text, flags=re.I)
    html_text = re.sub(r"<img([^>]+)src\s*=\s*'(.*?)'", lambda m: "<img" + m.group(1) + "src='" + os.path.basename(m.group(2)) + "'", html_text, flags=re.I)
    return html_text


def write_sites_geojson(dst_geojson: Path, lat: float, lon: float, props: dict, healthcard_rel_url: str) -> None:
    feature = {
        "type": "Feature",
        "properties": {
            **props,
            "HEALTHCARD_URL": healthcard_rel_url,
        },
        "geometry": {
            "type": "Point",
            "coordinates": [float(lon), float(lat)],
        },
    }
    fc = {"type": "FeatureCollection", "features": [feature]}
    safe_mkdir(dst_geojson.parent)
    dst_geojson.write_text(json.dumps(fc, indent=2), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="Source export folder (one-site or many-site).")
    ap.add_argument("--repo", default=".", help="Path to the mbss-healthcards git repo (default: .).")
    ap.add_argument("--lat", type=float, default=None, help="Latitude for stub sites.geojson (one-site).")
    ap.add_argument("--lon", type=float, default=None, help="Longitude for stub sites.geojson (one-site).")
    ap.add_argument("--clean", action="store_true", help="Wipe repo/data/healthcards and repo/data/sites.geojson before copy.")
    ap.add_argument("--commit", action="store_true", help="git add + commit")
    ap.add_argument("--push", action="store_true", help="git push (will prompt auth in browser if needed)")
    ap.add_argument("--message", default=None, help="Commit message")
    args = ap.parse_args()

    src = Path(args.src).expanduser().resolve()
    repo = Path(args.repo).expanduser().resolve()
    if not src.exists():
        eprint(f"[ERROR] Export folder does not exist: {src}")
        sys.exit(2)

    data_dir = repo / "data"
    hc_dir = data_dir / "healthcards"
    geojson_path = data_dir / "sites.geojson"

    safe_mkdir(data_dir)
    safe_mkdir(hc_dir)

    if args.clean:
        print(f"[CLEAN] Removing old healthcards in {hc_dir} ...")
        remove_tree(hc_dir)
        safe_mkdir(hc_dir)
        print(f"[CLEAN] Removing {geojson_path} ...")
        remove_file(geojson_path)

    # --- Handle index.html ---
    # If src contains index.html, copy it; otherwise keep repo/index.html as-is.
    src_index = src / "index.html"
    if src_index.exists():
        try:
            copy_file(src_index, repo / "index.html")
            print(f"[COPY] {src_index} -> {repo / 'index.html'}")
        except PermissionError as ex:
            eprint(f"[WARN] Could not overwrite index.html (maybe open in browser): {ex}")
            eprint("       Close any tab showing index.html and rerun, or skip copying index.html.")
    else:
        print("[INFO] Using existing repo/index.html (src did not provide one).")

    # --- Detect one-site export ---
    one_html = find_one_healthcard_html(src)

    if one_html is not None and one_html.name in HTML_NAMES:
        print(f"[SYNC] One-site export detected: {one_html}")
        html_text = one_html.read_text(encoding="utf-8", errors="ignore")
        props = parse_basic_fields_from_html(html_text)

        # Name the output file
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        siteyr = props.get("SITEYR")
        out_name = f"{siteyr}.html" if siteyr else f"SITE-{stamp}.html"

        # Patch image paths so everything loads from same folder
        html_text = patch_img_paths_to_healthcards_folder(html_text)

        out_html = hc_dir / out_name
        out_html.write_text(html_text, encoding="utf-8")
        print(f"[COPY] {one_html} -> {out_html}")

        # Copy referenced images (and also any images found anywhere under src)
        referenced = extract_img_srcs(html_text)
        copied_any = False

        # 1) copy images referenced by <img src="...">
        for rel in referenced:
            cand = (one_html.parent / rel).resolve()
            if cand.exists() and cand.is_file():
                if cand.suffix.lower() in IMAGE_EXTS:
                    copy_file(cand, hc_dir / cand.name)
                    copied_any = True

        # 2) copy all images under src recursively (covers subfolders)
        for p in iter_files_recursive(src):
            if p.suffix.lower() in IMAGE_EXTS:
                copy_file(p, hc_dir / p.name)
                copied_any = True

        if not copied_any:
            print("[WARN] No images copied. The HealthCard may show broken images.")
        else:
            print("[OK] Images copied into data/healthcards/.")

        # GeoJSON stub if lat/lon provided
        if args.lat is not None and args.lon is not None:
            rel_url = f"data/healthcards/{out_name}"
            write_sites_geojson(geojson_path, args.lat, args.lon, props, rel_url)
            print(f"[GEN] Created/updated stub GeoJSON: {geojson_path}")
        else:
            if not geojson_path.exists():
                print("[WARN] No sites.geojson found and no --lat/--lon provided.")
                print("       Map may have no clickable points.")
    else:
        # Multi-site copy mode:
        print(f"[SYNC] Multi-site copy mode from: {src}")
        # Copy any .html into data/healthcards (preserve basenames)
        html_count = 0
        img_count = 0
        for p in iter_files_recursive(src):
            ext = p.suffix.lower()
            if ext == ".html":
                copy_file(p, hc_dir / p.name)
                html_count += 1
            elif ext in IMAGE_EXTS:
                copy_file(p, hc_dir / p.name)
                img_count += 1

        # Copy sites.geojson if present
        src_geo = src / "sites.geojson"
        if src_geo.exists():
            copy_file(src_geo, geojson_path)
            print(f"[COPY] {src_geo} -> {geojson_path}")
        else:
            if args.lat is not None and args.lon is not None:
                # create minimal
                stamp = datetime.now().strftime("%Y%m%d%H%M%S")
                rel_url = "data/healthcards/" + (f"SITE-{stamp}.html" if html_count else "")
                write_sites_geojson(geojson_path, args.lat, args.lon, {}, rel_url)
                print(f"[GEN] Created minimal GeoJSON: {geojson_path}")
            else:
                print(f"[WARN] data/sites.geojson not found at: {src_geo}")
                print("       The map may show 'Data load error' unless you provide sites.geojson or --lat/--lon.")

        print(f"[OK] Copied {html_count} HTML + {img_count} images into repo/data/healthcards/.")

    print("\n[OK] Sync complete.\n")

    # --- Git operations ---
    if args.commit or args.push:
        # Stage
        run(["git", "add", "-A"], cwd=repo)

        if args.commit:
            msg = args.message or "Update MBSS HealthCards"
            print("[GIT] Committing...")
            rc = run(["git", "commit", "-m", msg], cwd=repo)
            if rc != 0:
                print("[GIT] Nothing to commit (or commit failed).")

        if args.push:
            print("[GIT] Pushing...")
            rc = run(["git", "push"], cwd=repo)
            if rc != 0:
                eprint("[GIT] Push failed. If prompted, complete browser authentication and retry: git push")

        print("[GIT] Done.\n")
        print("Next check:")
        print("  - Open: https://wuhibus-code.github.io/mbss-healthcards/")
        print("  - Hard refresh: Ctrl+F5")


if __name__ == "__main__":
    main()
