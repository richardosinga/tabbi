#!/usr/bin/env python3
"""
Flow 2: Replace ~pois/ references in plan files with real world66 paths,
but only where the world66 file actually exists.

Usage:
  python deploy/update-plan-refs.py           # dry-run, shows what would change
  python deploy/update-plan-refs.py --apply   # actually updates plan files
"""

import argparse
import os
import re
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
PLANS_DIR = REPO_DIR / "plans"

dotenv = REPO_DIR / ".env"
if dotenv.exists():
    for line in dotenv.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

WORLD66_DIR = Path(os.environ.get("WORLD66_DIR", str(REPO_DIR / "world66")))
WORLD66_CONTENT = WORLD66_DIR / "content"

CONTINENTS = {"africa", "asia", "australiaandpacific", "europe", "northamerica", "southamerica"}


def resolve_world66_path(draft_ref: str) -> str | None:
    """
    Given a ~pois/ reference, return the world66 content path if the file exists.
    draft_ref format: ~pois/{plan_slug}/{path}/{poi_slug}
    """
    without_prefix = draft_ref[len("~pois/"):]
    parts = without_prefix.split("/")
    if len(parts) < 3:
        return None

    plan_slug = parts[0]
    rest = parts[1:]  # everything after plan_slug

    # Full world66 path: starts with a continent
    if rest[0] in CONTINENTS:
        world66_path = "/".join(rest)
        if (WORLD66_CONTENT / (world66_path + ".md")).exists():
            return world66_path
        return None

    # Short path: rest[0] is a city slug — search world66 for it
    city_slug = rest[0]
    poi_slug = rest[-1]
    matches = [p for p in WORLD66_CONTENT.rglob(city_slug) if p.is_dir()]
    if len(matches) == 1:
        candidate = matches[0] / (poi_slug + ".md")
        if candidate.exists():
            return str(candidate.relative_to(WORLD66_CONTENT)).removesuffix(".md")
    return None


def process_plan(plan_file: Path, apply: bool) -> int:
    text = plan_file.read_text(encoding="utf-8")
    replacements = 0
    new_lines = []

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- ~pois/"):
            draft_ref = stripped[2:]  # strip "- "
            world66_path = resolve_world66_path(draft_ref)
            if world66_path:
                new_line = line.replace(draft_ref, world66_path)
                new_lines.append(new_line)
                replacements += 1
                print(f"  {plan_file.stem}: {draft_ref}  →  {world66_path}")
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    if replacements and apply:
        plan_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    return replacements


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Actually update plan files")
    args = parser.parse_args()

    if not WORLD66_CONTENT.exists():
        print(f"Error: world66 content not found at {WORLD66_CONTENT}")
        sys.exit(1)

    plan_files = sorted(PLANS_DIR.glob("*.md"))
    if not plan_files:
        print("No plan files found.")
        return

    total = 0
    for plan_file in plan_files:
        total += process_plan(plan_file, apply=args.apply)

    if total == 0:
        print("No ~pois/ references found with matching world66 content.")
    elif not args.apply:
        print(f"\n{total} replacement(s) ready. Run with --apply to update plan files.")
    else:
        print(f"\n{total} reference(s) updated.")


if __name__ == "__main__":
    main()
