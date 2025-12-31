#!/usr/bin/env python3
# -*- coding: utf-8 -*-

r"""
MBSS HealthCards - Option A Sync Script (Windows-friendly)
=========================================================

Goal
----
Automatically sync a locally-exported "static site" folder into your GitHub Pages repo.

Expected export folder layout (recommended)
------------------------------------------
<EXPORT>/
  index.html
  HealthCard.html              (optional)
  *.png                        (optional)
  data/
    sites.geojson
    healthcards/
      *.html
      *.png (optional)

What this script does
---------------------
- Copies EVERYTHING from <EXPORT> into the repo folder (except .git)
- Optional: deletes old repo/data/healthcards and repo/data/sites.geojson before copying
- Optional: runs git add/commit/push

Typical use
-----------
1) From inside your repo folder:
   python sync_option_a.py --src "C:\path\to\HealthCard_Exports" --clean --commit --push

Notes
-----
- If Windows says a file is "being used by another process", CLOSE any browser tab
  that is viewing the local file (file:///) or any local server using that file.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def eprint(*args):
    print(*args, file=sys.stderr)


def run(cmd, cwd: Path | None = None, check=True):
    """Run a shell command and return CompletedProcess."""
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=check)


def safe_rmtree(p: Path):
    if p.exists() and p.is_dir():
        shutil.rmtree(p, ignore_errors=True)


def ensure_parent_dir(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)


def same_file(src: Path, dst: Path) -> bool:
    try:
        return src.resolve() == dst.resolve()
    except Exception:
        return str(src) == str(dst)


def copy_file(src: Path, dst: Path):
    ensure_parent_dir(dst)
    if same_file(src, dst):
        # Avoid the "copy to itself" issue
        return
    shutil.copy2(str(src), str(dst))


def copy_tree(src_root: Path, dst_root: Path):
    """
    Copy entire directory tree from src_root -> dst_root
    skipping .git and sync script itself (optional).
    """
    for src_path in src_root.rglob("*"):
        rel = src_path.relative_to(src_root)

        # Skip .git anywhere in src export
        if ".git" in rel.parts:
            continue

        dst_path = dst_root / rel

        if src_path.is_dir():
            dst_path.mkdir(parents=True, exist_ok=True)
        else:
            copy_file(src_path, dst_path)


def validate_export_folder(src: Path):
    """
    Validate that src looks like a static site export.
    """
    idx = src / "index.html"
    if not idx.exists():
        # Provide helpful hint: maybe user pointed one folder too high
        eprint(f'[ERROR] Source index.html not found at: {idx}')
        eprint("Hints:")
        eprint("  - Make sure --src points to the folder that CONTAINS index.html")
        eprint("  - If your export is inside a subfolder, point --src to that subfolder.")
        eprint("  - Example: --src \"C:\\...\\FINAL\\mbss-healthcards\" (if that folder has index.html)")
        raise SystemExit(2)

    sites = src / "data" / "sites.geojson"
    if not sites.exists():
        eprint(f'[WARN] data/sites.geojson not found at: {sites}')
        eprint("       The map may show 'Data load error' unless you export/copy sites.geojson.")
    # healthcards folder is optional during early testing
    hc = src / "data" / "healthcards"
    if not hc.exists():
        eprint(f'[WARN] data/healthcards folder not found at: {hc}')
        eprint("       'Open full HealthCard' may fail unless healthcard html files exist.")


def git_status(repo: Path):
    run(["git", "status"], cwd=repo, check=False)


def git_commit_push(repo: Path, message: str, do_commit: bool, do_push: bool):
    # Stage all changes
    run(["git", "add", "-A"], cwd=repo)

    if do_commit:
        # Commit (if there are changes)
        cp = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=str(repo))
        if cp.returncode == 0:
            print("[GIT] No changes to commit.")
        else:
            run(["git", "commit", "-m", message], cwd=repo)

    if do_push:
        run(["git", "push"], cwd=repo)


def main():
    ap = argparse.ArgumentParser(description="Sync Option A export folder into GitHub Pages repo.")
    ap.add_argument("--src", required=True, help="Path to export folder that contains index.html")
    ap.add_argument("--repo", default=".", help="Path to repo clone (default: current folder)")
    ap.add_argument("--clean", action="store_true", help="Remove old repo/data/healthcards and repo/data/sites.geojson before copying")
    ap.add_argument("--commit", action="store_true", help="git commit after copying")
    ap.add_argument("--push", action="store_true", help="git push after commit/add")
    ap.add_argument("--message", default="Sync healthcards export", help="Commit message (if --commit)")
    args = ap.parse_args()

    src = Path(args.src).expanduser()
    repo = Path(args.repo).expanduser()

    if not src.exists() or not src.is_dir():
        eprint(f"[ERROR] --src is not a folder: {src}")
        raise SystemExit(2)

    if not repo.exists() or not (repo / ".git").exists():
        eprint(f"[ERROR] --repo does not look like a git repo (missing .git): {repo}")
        eprint("Run this inside the cloned repo folder, or pass --repo to your clone path.")
        raise SystemExit(2)

    validate_export_folder(src)

    # CLEAN (optional)
    if args.clean:
        print("[CLEAN] Removing old healthcards in repo/data/healthcards ...")
        safe_rmtree(repo / "data" / "healthcards")
        sites = repo / "data" / "sites.geojson"
        if sites.exists():
            print("[CLEAN] Removing repo/data/sites.geojson ...")
            try:
                sites.unlink()
            except Exception:
                pass

    # COPY
    print(f"[SYNC] Copying from export:\n  {src}\ninto repo:\n  {repo}")
    try:
        copy_tree(src, repo)
    except PermissionError as ex:
        eprint("\n[ERROR] PermissionError (WinError 32): a file is open/locked by another program.")
        eprint("Fix:")
        eprint("  1) Close any browser tab opened from your local repo (file:///...)")
        eprint("  2) Close any program previewing index.html / HealthCard.html")
        eprint("  3) Re-run the same command.\n")
        raise

    print("[OK] Files copied.")

    # GIT
    if args.commit or args.push:
        print("\n[GIT] Status:")
        git_status(repo)
        git_commit_push(repo, args.message, do_commit=args.commit, do_push=args.push)
        print("[GIT] Done.")

    print("\nNext check:")
    print("  - Open: https://wuhibus-code.github.io/mbss-healthcards/")
    print("  - If you don’t see updates immediately, hard refresh (Ctrl+F5).")


if __name__ == "__main__":
    main()
