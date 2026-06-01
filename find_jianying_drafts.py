#!/usr/bin/env python3
"""
Find local Jianying/CapCut draft projects.

Scans known installation paths and lists all found drafts.
Supports user-configured paths via config.json.

Usage:
    python find_jianying_drafts.py
    python find_jianying_drafts.py --config /path/to/config.json
"""

import os
import sys
import json
import platform
from pathlib import Path
from datetime import datetime

SCRIPT_DIR = Path(__file__).parent
DEFAULT_CONFIG = SCRIPT_DIR / "config.json"

# ── Default scan paths (no hardcoded user-specific paths) ──
def get_default_scan_roots() -> list[str]:
    """Return platform-specific default Jianying project directories."""
    system = platform.system()
    roots = []

    if system == "Windows":
        local = os.environ.get("LOCALAPPDATA", "")
        appdata = os.environ.get("APPDATA", "")
        for base in [local, appdata]:
            if not base:
                continue
            roots.extend([
                f"{base}/JianyingPro/User Data/Projects/com.lveditor.draft",
                f"{base}/JianyingPro/User Data/Projects/compositon",
                f"{base}/CapCut/User Data/Projects/compositon",
                f"{base}/CapCut/User Data/Projects/com.lveditor.draft",
            ])
    elif system == "Darwin":
        home = Path.home()
        for sub in ["Movies", "Documents"]:
            for app in ["JianyingPro", "CapCut"]:
                roots.append(f"{home}/{sub}/{app}/User Data/Projects/com.lveditor.draft")
                roots.append(f"{home}/{sub}/{app}/User Data/Projects/compositon")
    elif system == "Linux":
        home = Path.home()
        for base in [f"{home}/.local/share", f"{home}/.config"]:
            for app in ["JianyingPro", "CapCut"]:
                roots.append(f"{base}/{app}/User Data/Projects/com.lveditor.draft")
                roots.append(f"{base}/{app}/User Data/Projects/compositon")

    return roots


def load_config(config_path: Path | None = None) -> dict:
    """Load config.json, return empty dict if not found."""
    if config_path is None:
        config_path = DEFAULT_CONFIG
    if not config_path.exists():
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def expand_path(path: str) -> str:
    """Expand environment variables and ~ in path."""
    path = os.path.expandvars(path)
    path = os.path.expanduser(path)
    return path


def find_drafts(config: dict) -> list[dict]:
    """Scan for Jianying drafts. Uses config paths first, then defaults."""
    results = []
    seen = set()

    # Build scan roots: user-configured first, then defaults
    scan_roots = []
    for p in config.get("jianying_projects_dirs", []):
        scan_roots.append(expand_path(p))
    scan_roots.extend(get_default_scan_roots())

    for root in scan_roots:
        if not os.path.isdir(root):
            continue

        for entry in os.scandir(root):
            if not entry.is_dir():
                continue

            # Must contain draft_content.json (or template.json.bak)
            has_draft = os.path.isfile(os.path.join(entry.path, "draft_content.json"))
            has_backup = os.path.isfile(os.path.join(entry.path, "template.json.bak"))
            if not has_draft and not has_backup:
                continue

            # Deduplicate by resolved path
            resolved = str(Path(entry.path).resolve())
            if resolved in seen:
                continue
            seen.add(resolved)

            # Build info with a single file read
            draft_file = os.path.join(entry.path, "draft_content.json")
            info = {
                "folder": entry.path,
                "folder_name": entry.name,
                "modified": datetime.fromtimestamp(
                    os.path.getmtime(draft_file if has_draft else os.path.join(entry.path, "template.json.bak"))
                ).isoformat(),
                "name": entry.name,
                "encrypted": False,
            }

            # Single read attempt
            if has_draft:
                try:
                    with open(draft_file, "r", encoding="utf-8") as f:
                        content = json.load(f)
                    info["name"] = content.get("name") or content.get("id") or entry.name
                except json.JSONDecodeError:
                    info["encrypted"] = True
                    # Try backup for name
                    meta_path = os.path.join(entry.path, "draft_meta_info.json")
                    if os.path.isfile(meta_path):
                        try:
                            with open(meta_path, "r", encoding="utf-8") as f:
                                meta = json.load(f)
                            info["name"] = meta.get("name") or entry.name
                        except Exception:
                            pass

            results.append(info)

    # Sort by modification time, newest first
    results.sort(key=lambda x: x.get("modified", ""), reverse=True)
    return results


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Find Jianying/CapCut draft projects")
    parser.add_argument("--config", type=str, default=None, help="Path to config.json")
    args = parser.parse_args()

    config_path = Path(args.config) if args.config else DEFAULT_CONFIG
    config = load_config(config_path)

    print("Scanning for Jianying drafts...")
    drafts = find_drafts(config)

    if not drafts:
        print("\nNo drafts found.")
        print("\nPossible reasons:")
        print("  1. Jianying is not installed at a standard location")
        print("  2. No projects have been created yet")
        print("  3. You may need to add custom paths to config.json")
        print(f"\nConfig file: {config_path}")
        print('\nAdd your projects path to config.json:')
        print('  "jianying_projects_dirs": ["C:/path/to/Projects"]')
        return

    print(f"\nFound {len(drafts)} draft(s):\n")
    for i, d in enumerate(drafts):
        status = "ENCRYPTED" if d.get("encrypted") else "OK"
        print(f"  [{i+1}] {d['name']}")
        print(f"      Path: {d['folder']}")
        print(f"      Modified: {d['modified']}")
        print(f"      Status: {status}")
        print()

    # Stats
    parseable = [d for d in drafts if not d.get("encrypted")]
    encrypted = [d for d in drafts if d.get("encrypted")]

    print("--- Stats ---")
    print(f"  Parseable: {len(parseable)}")
    print(f"  Encrypted: {len(encrypted)} (Jianying 6.x+)")

    if parseable:
        print(f"\nConversion example:")
        d = parseable[0]
        print(f'  python jianying_to_xml.py "{d["folder"]}"')

    if encrypted:
        print(f"\nNote: {len(encrypted)} draft(s) encrypted (Jianying 6.x+), will try template.json.bak fallback.")


if __name__ == "__main__":
    main()
