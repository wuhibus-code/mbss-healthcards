#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

r"""
Option A Sync Script (Robust) — MBSS HealthCards → GitHub Pages repo
===================================================================

What it does
------------
Given an export folder (--src), it will:
  - Ensure repo structure:
      ./index.html
      ./data/sites.geojson
      ./data/healthcards/
  - Copy healthcard HTML + images into ./data/healthcards/
  - Copy sites.geojson into ./data/sites.geojson (or generate a 1-site stub if missing)
  - Optionally clean old repo outputs (--clean)
  - Optionally git commit/push (--commit --push)

Supports 2 common export styles:
  A) Full package export already structured:
       src/index.html
       src/data/sites.geojson
       src/data/healthcards/*.html + *.png
  B) One-site export (your current case):
       src/HealthCard.html
       src/**/pdp_*.png, fi_*.png, etc in subfolders
     -> script will place:
       repo/data/healthcards/<SITEYR>.html (SITEYR inferred from HTML)
       repo/data/sites.geojson (copied if found; else generated stub if lat/lon provided)

Run examples (from repo folder):
  python sync_option_a.py --src "C:\...\ONE_SITE_EXPORT" --commit --push --message "Update 1 site"
  python sync_option_a.py --src "C:\...\HealthCard_Exports" --clean --commit --push --message "Update all sites"

If you need stub GeoJSON generation:
  python sync_option_a.py --src "C:\...\ONE_SITE_EXPORT" --lat 39.35986 --lon -77.3092 --commit --push

Notes
-----
- Close any browser tabs holding repo files (index.html) to avoid WinError 32.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from datetime import datetime


# -----------------------------
# helpers
# -----------------------------
def run(cmd, cwd: Path | None = None, check=True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=check, text=True, capture_output=True)


def safe_mkdir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def rm_tree(p: Path) -> None:
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)


def rm_file(p: Path) -> None:
    if p.exists():
        try:
            p.unlink()
        except PermissionError:
            # last resort
            try:
                os.chmod(p, 0o666)
                p.unlink()
            except Exception:
                pass


def copy_file(src: Path, dst: Path) -> None:
    safe_mkdir(dst.parent)
    # write to temp then replace to avoid partial copy issues
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    if tmp.exists():
        rm_file(tmp)
    shutil.copy2(str(src), str(tmp))
    if dst.exists():
        rm_file(dst)
    tmp.replace(dst)


def copy_tree(src_dir: Path, dst_dir: Path) -> None:
    safe_mkdir(dst_dir)
    for p in src_dir.rglob("*"):
        if p.is_dir():
            continue
        rel = p.relative_to(src_dir)
        copy_file(p, dst_dir / rel)


def find_first(root: Path, name: str) -> Path | None:
    # case-insensitive match
    target = name.lower()
    for p in root.rglob("*"):
        if p.is_file() and p.name.lower() == target:
            return p
    return None


def find_all_pngs(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.png") if p.is_file()]


def infer_siteyr_from_html(html_text: str) -> str | None:
    # common SITEYR patterns: ABCD-123-R-2016 or LMON-345-R-2016 etc
    m = re.search(r"\b([A-Z0-9]{3,}-\d{1,4}-[A-Z]-\d{4})\b", html_text)
    if m:
        return m.group(1)
    return None


def build_stub_geojson(siteyr: str, lat: float, lon: float, healthcard_url: str) -> dict:
    # GeoJSON coordinates are [lon, lat]
    feat = {
        "type": "Feature",
        "properties": {
            "SITEYR": siteyr,
            "HEALTHCARD_URL": healthcard_url,
        },
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
    }
    return {"type": "FeatureCollection", "features": [feat]}


def git_available(repo: Path) -> bool:
    try:
        run(["git", "--version"], cwd=repo, check=True)
        return True
    except Exception:
        return False


def git_commit_push(repo: Path, message: str, do_push: bool) -> None:
    run(["git", "add", "-A"], cwd=repo, check=True)
    # commit even if nothing changed? avoid failure
    st = run(["git", "status", "--porcelain"], cwd=repo, check=True).stdout.strip()
    if not st:
        print("[GIT] No changes to commit.")
        return
    run(["git", "commit", "-m", message], cwd=repo, check=True)
    if do_push:
        # this may trigger browser auth
        run(["git", "push"], cwd=repo, check=True)


# -----------------------------
# main sync logic
# -----------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="Export folder path (full package export OR one-site folder)")
    ap.add_argument("--repo", default=".", help="Repo path (default: current folder)")
    ap.add_argument("--clean", action="store_true", help="Remove old repo/data/healthcards/* and repo/data/sites.geojson before copying")
    ap.add_argument("--commit", action="store_true", help="Git commit after syncing")
    ap.add_argument("--push", action="store_true", help="Git push after commit (requires --commit)")
    ap.add_argument("--message", default=None, help="Commit message")
    ap.add_argument("--lat", type=float, default=None, help="Latitude for stub GeoJSON if sites.geojson is missing")
    ap.add_argument("--lon", type=float, default=None, help="Longitude for stub GeoJSON if sites.geojson is missing")
    args = ap.parse_args()

    src = Path(args.src).expanduser().resolve()
    repo = Path(args.repo).expanduser().resolve()

    if not src.exists():
        raise SystemExit(f"[ERROR] Export folder does not exist: {src}")
    if not (repo / ".git").exists():
        raise SystemExit(f"[ERROR] Repo folder does not look like a git repo (no .git): {repo}")

    data_dir = repo / "data"
    hc_dir = data_dir / "healthcards"
    sites_dst = data_dir / "sites.geojson"

    safe_mkdir(hc_dir)

    if args.clean:
        print(f"[CLEAN] Removing old healthcards in {hc_dir} ...")
        rm_tree(hc_dir)
        safe_mkdir(hc_dir)
        print(f"[CLEAN] Removing {sites_dst} ...")
        rm_file(sites_dst)

    # Detect export style A (full structure) vs B (one-site)
    index_src = (src / "index.html") if (src / "index.html").exists() else None
    sites_src = (src / "data" / "sites.geojson") if (src / "data" / "sites.geojson").exists() else None
    hc_src_dir = (src / "data" / "healthcards") if (src / "data" / "healthcards").exists() else None

    # Also allow site file anywhere in src
    if sites_src is None:
        sites_src = find_first(src, "sites.geojson")

    one_site_html = None
    if hc_src_dir is None:
        # common name in your workflow
        one_site_html = (src / "HealthCard.html") if (src / "HealthCard.html").exists() else None
        if one_site_html is None:
            # any html in root as fallback
            htmls = [p for p in src.glob("*.html") if p.is_file()]
            if htmls:
                # prefer a file with "health" in the name
                htmls_sorted = sorted(htmls, key=lambda p: ("health" not in p.name.lower(), p.name.lower()))
                one_site_html = htmls_sorted[0]

    # 1) index.html
    # Only copy index.html from src if it exists; otherwise keep repo's existing index.html
    if index_src is not None:
        print(f"[COPY] {index_src} -> {repo / 'index.html'}")
        copy_file(index_src, repo / "index.html")
    else:
        if not (repo / "index.html").exists():
            raise SystemExit("[ERROR] repo/index.html is missing AND src/index.html is missing.")
        print("[INFO] Using existing repo/index.html (src did not provide one).")

    # 2) healthcards content
    if hc_src_dir is not None:
        print(f"[SYNC] Copying healthcards folder: {hc_src_dir} -> {hc_dir}")
        copy_tree(hc_src_dir, hc_dir)
    elif one_site_html is not None:
        print(f"[SYNC] One-site export detected: {one_site_html}")

        html_text = one_site_html.read_text(encoding="utf-8", errors="ignore")
        siteyr = infer_siteyr_from_html(html_text) or f"SITE-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        dst_html_name = f"{siteyr}.html"
        dst_html = hc_dir / dst_html_name

        print(f"[COPY] {one_site_html} -> {dst_html}")
        copy_file(one_site_html, dst_html)

        # copy all pngs under src recursively into repo/data/healthcards
        pngs = find_all_pngs(src)
        if pngs:
            print(f"[SYNC] Copying {len(pngs)} PNG(s) into {hc_dir} ...")
            for p in pngs:
                copy_file(p, hc_dir / p.name)
        else:
            print("[WARN] No PNGs found under --src. The HealthCard may show broken images.")

        # If sites.geojson is missing, we can generate a 1-site stub if lat/lon provided
        if sites_src is None:
            if args.lat is not None and args.lon is not None:
                stub = build_stub_geojson(
                    siteyr=siteyr,
                    lat=float(args.lat),
                    lon=float(args.lon),
                    healthcard_url=f"data/healthcards/{dst_html_name}",
                )
                safe_mkdir(sites_dst.parent)
                sites_dst.write_text(json.dumps(stub, indent=2), encoding="utf-8")
                print(f"[GEN] Created stub GeoJSON: {sites_dst}")
            else:
                print("[WARN] No sites.geojson found in --src, and no --lat/--lon provided.")
                print("       Map may show 'Data load error' or have no clickable points.")
    else:
        raise SystemExit("[ERROR] Could not find src/data/healthcards/ nor a HealthCard HTML file to sync.")

    # 3) sites.geojson copy if available (and not already generated)
    if sites_src is not None:
        print(f"[COPY] {sites_src} -> {sites_dst}")
        copy_file(sites_src, sites_dst)

    print("\n[OK] Sync complete.")

    # 4) git commit/push
    if args.commit:
        if not git_available(repo):
            raise SystemExit("[ERROR] git is not available in PATH. Install Git for Windows and retry.")
        msg = args.message or "Update MBSS HealthCards"
        print("\n[GIT] Committing...")
        git_commit_push(repo, msg, do_push=args.push)
        print("\n[GIT] Done.")

        print("\nNext check:")
        print("  - Open: https://wuhibus-code.github.io/mbss-healthcards/")
        print("  - Hard refresh: Ctrl+F5")
        print("  - If points don’t appear: ensure data/sites.geojson exists and is valid JSON.")
    else:
        print("\n[GIT] Skipped (no --commit).")


if __name__ == "__main__":
    main()
