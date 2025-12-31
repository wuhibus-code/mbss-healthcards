#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def run(cmd, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=check, text=True, capture_output=True)


def safe_mkdir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def safe_unlink(p: Path) -> None:
    try:
        if p.is_file() or p.is_symlink():
            p.unlink()
    except FileNotFoundError:
        return


def safe_rmtree(p: Path) -> None:
    if not p.exists():
        return
    shutil.rmtree(p, ignore_errors=True)


def copy_file(src: Path, dst: Path) -> None:
    safe_mkdir(dst.parent)
    # Avoid copying file onto itself
    try:
        if src.resolve() == dst.resolve():
            return
    except Exception:
        pass

    # Windows can lock files if open in browser/editor; retry a bit.
    last_err = None
    for _ in range(6):
        try:
            shutil.copy2(str(src), str(dst))
            return
        except PermissionError as e:
            last_err = e
            time.sleep(0.35)

    raise PermissionError(
        f"PermissionError copying:\n  src={src}\n  dst={dst}\n"
        f"Close any browser tab/editor that is viewing the destination file, then re-run."
    ) from last_err


def copy_tree_flat(src_dir: Path, patterns: list[str], dst_dir: Path) -> int:
    """Copy matching files from src_dir (non-recursive) into dst_dir. Returns count."""
    n = 0
    for pat in patterns:
        for f in src_dir.glob(pat):
            if f.is_file():
                copy_file(f, dst_dir / f.name)
                n += 1
    return n


def find_first(src_root: Path, rel_candidates: list[Path]) -> Path | None:
    """Return first existing path among candidates under src_root."""
    for rel in rel_candidates:
        p = src_root / rel
        if p.exists() and p.is_file():
            return p
    return None


def build_single_site_geojson(
    out_path: Path,
    siteyr: str,
    lon: float,
    lat: float,
    stream: str | None,
    year: int | None,
    bibi: float | None,
    fibi: float | None,
    mde8: str | None,
    dnr12dig: str | None,
    province: str | None,
    healthcard_url: str,
) -> None:
    feat = {
        "type": "Feature",
        "properties": {
            "SITEYR": siteyr,
            "STREAMNAME": stream or "",
            "YEAR": year if year is not None else None,
            "BIBI": bibi if bibi is not None else None,
            "FIBI": fibi if fibi is not None else None,
            "MDE8": mde8 or "",
            "DNR12DIG": dnr12dig or "",
            "PROVINCE": province or "",
            "HEALTHCARD_URL": healthcard_url,
        },
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
    }
    gj = {"type": "FeatureCollection", "features": [feat]}
    safe_mkdir(out_path.parent)
    out_path.write_text(json.dumps(gj, indent=2), encoding="utf-8")


def git_is_repo(repo: Path) -> bool:
    return (repo / ".git").exists()


def git_status(repo: Path) -> str:
    cp = run(["git", "status"], cwd=repo, check=False)
    return cp.stdout + (("\n" + cp.stderr) if cp.stderr else "")


def git_add_commit_push(repo: Path, message: str, do_commit: bool, do_push: bool) -> None:
    if not git_is_repo(repo):
        print("[ERROR] This folder is not a git repo (missing .git). Run inside the cloned repo folder.")
        sys.exit(2)

    run(["git", "add", "-A"], cwd=repo)

    # If nothing staged, don’t commit
    diff = run(["git", "diff", "--cached", "--name-only"], cwd=repo, check=False).stdout.strip()
    if not diff:
        print("[GIT] No changes staged. Nothing to commit.")
        return

    if do_commit:
        run(["git", "commit", "-m", message], cwd=repo)
        print("[GIT] Commit created.")
    else:
        print("[GIT] Changes staged (no commit requested).")

    if do_push:
        print("info: if prompted, complete authentication in your browser...")
        run(["git", "push"], cwd=repo)
        print("[GIT] Pushed to origin.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Option A: Sync MBSS HealthCard exports into GitHub Pages repo.")
    ap.add_argument("--src", required=True, help="Export folder containing HealthCard.html and PNGs (and optionally sites.geojson).")
    ap.add_argument("--repo", default=".", help="Path to the cloned mbss-healthcards repo (default: current folder).")

    ap.add_argument("--clean", action="store_true", help="Remove old repo/data/healthcards/* (except .keep) and repo/data/sites.geojson before copying.")
    ap.add_argument("--commit", action="store_true", help="Commit changes after syncing.")
    ap.add_argument("--push", action="store_true", help="Push after committing.")
    ap.add_argument("--message", default="Update MBSS HealthCards", help="Commit message.")

    # If your export folder has NO sites.geojson, we can create a 1-site geojson automatically:
    ap.add_argument("--siteyr", default=None, help="SITEYR for the single-site export (e.g., LMON-345-R-2016).")
    ap.add_argument("--lat", type=float, default=None, help="Latitude for the single site.")
    ap.add_argument("--lon", type=float, default=None, help="Longitude for the single site.")

    ap.add_argument("--stream", default=None, help="Stream name (optional).")
    ap.add_argument("--year", type=int, default=None, help="Year (optional).")
    ap.add_argument("--bibi", type=float, default=None, help="BIBI (optional).")
    ap.add_argument("--fibi", type=float, default=None, help="FIBI (optional).")
    ap.add_argument("--mde8", default=None, help="MDE8 (optional).")
    ap.add_argument("--dnr12dig", default=None, help="DNR12DIG (optional).")
    ap.add_argument("--province", default=None, help="Province (optional).")

    args = ap.parse_args()

    src = Path(args.src).expanduser()
    repo = Path(args.repo).expanduser()

    if not src.exists() or not src.is_dir():
        print(f"[ERROR] Export folder does not exist: {src}")
        sys.exit(2)

    # Target structure in repo
    repo_data = repo / "data"
    repo_hc = repo_data / "healthcards"
    repo_sites = repo_data / "sites.geojson"

    safe_mkdir(repo_hc)

    if args.clean:
        print("[CLEAN] Removing old healthcards in repo/data/healthcards ...")
        for f in repo_hc.glob("*"):
            if f.name == ".keep":
                continue
            if f.is_dir():
                safe_rmtree(f)
            else:
                safe_unlink(f)
        print("[CLEAN] Removing repo/data/sites.geojson ...")
        safe_unlink(repo_sites)

    # --------- Copy HealthCard HTML ----------
    # Accept several common export layouts:
    #  A) src/HealthCard.html
    #  B) src/data/healthcards/*.html
    #  C) src/*.html (single)
    hc_html = find_first(src, [Path("HealthCard.html")])

    copied_any_html = False
    if hc_html:
        # Rename HealthCard.html to SITEYR.html if provided
        if args.siteyr:
            dst_name = f"{args.siteyr}.html"
        else:
            dst_name = "HealthCard.html"
        print(f"[COPY] {hc_html} -> {repo_hc / dst_name}")
        copy_file(hc_html, repo_hc / dst_name)
        copied_any_html = True

    # Copy any other HTMLs that might exist in src root
    n_html_root = copy_tree_flat(src, ["*.html", "*.htm"], repo_hc)
    if n_html_root:
        copied_any_html = True

    # Copy htmls if export already has data/healthcards
    src_hc_folder = src / "data" / "healthcards"
    if src_hc_folder.exists() and src_hc_folder.is_dir():
        for f in src_hc_folder.glob("*.html"):
            print(f"[COPY] {f} -> {repo_hc / f.name}")
            copy_file(f, repo_hc / f.name)
            copied_any_html = True

    if not copied_any_html:
        print("[WARN] No HealthCard HTML found in export folder.")
        print("       Expected at least HealthCard.html or *.html.")
        # Still continue for png/geojson if present.

    # --------- Copy PNGs ----------
    # Copy PNGs from src root and src/data/healthcards if present
    n_png_root = copy_tree_flat(src, ["*.png"], repo_hc)
    n_png_hc = 0
    if src_hc_folder.exists() and src_hc_folder.is_dir():
        n_png_hc = copy_tree_flat(src_hc_folder, ["*.png"], repo_hc)

    print(f"[OK] Copied PNGs: {n_png_root + n_png_hc}")

    # --------- sites.geojson ----------
    # Preferred: use export’s sites.geojson if provided
    src_sites = find_first(src, [Path("data/sites.geojson"), Path("sites.geojson")])
    if src_sites:
        print(f"[COPY] {src_sites} -> {repo_sites}")
        copy_file(src_sites, repo_sites)
    else:
        # If not available, auto-create a 1-site geojson (requires siteyr, lat, lon)
        if args.siteyr and (args.lat is not None) and (args.lon is not None):
            # ensure the healthcard filename matches what we placed
            hc_filename = f"{args.siteyr}.html"
            healthcard_url = f"data/healthcards/{hc_filename}"
            print(f"[BUILD] Creating single-site geojson -> {repo_sites}")
            build_single_site_geojson(
                out_path=repo_sites,
                siteyr=args.siteyr,
                lon=args.lon,
                lat=args.lat,
                stream=args.stream,
                year=args.year,
                bibi=args.bibi,
                fibi=args.fibi,
                mde8=args.mde8,
                dnr12dig=args.dnr12dig,
                province=args.province,
                healthcard_url=healthcard_url,
            )
        else:
            print("[WARN] data/sites.geojson not found in export folder.")
            print("       Provide it OR pass --siteyr --lat --lon to auto-create a 1-site geojson.")
            # Create an empty valid geojson so the site won’t throw JSON parse errors
            safe_mkdir(repo_sites.parent)
            repo_sites.write_text(json.dumps({"type": "FeatureCollection", "features": []}, indent=2), encoding="utf-8")

    # --------- Ensure .keep exists (optional) ----------
    keep = repo_hc / ".keep"
    if not keep.exists():
        keep.write_text("", encoding="utf-8")

    # --------- Git commit/push ----------
    print("\n[GIT] Status (before):")
    print(git_status(repo))

    if args.commit or args.push:
        git_add_commit_push(repo, args.message, do_commit=args.commit, do_push=args.push)

    print("\nNext check:")
    print("  - Open: https://wuhibus-code.github.io/mbss-healthcards/")
    print("  - If you don’t see updates immediately, hard refresh (Ctrl+F5).")


if __name__ == "__main__":
    main()
