#!/usr/bin/env python3
"""
Tabbi MCP Server — exposes trip-planning tools to Claude and other MCP clients.

Usage (stdio transport):
  python tools/mcp_server.py

Configuration (environment variables or .env in the repo root):
  TABBI_BASE_URL   Base URL of the Tabbi site (default: http://localhost:8001)
  WORLD66_DIR      Path to the world66 repo, for reading content (default: ./world66)

To add to Claude Code, run:
  claude mcp add tabbi -- python /path/to/tabbi/tools/mcp_server.py

Or add to ~/Library/Application Support/Claude/claude_desktop_config.json:
  {
    "mcpServers": {
      "tabbi": {
        "command": "python",
        "args": ["/path/to/tabbi/tools/mcp_server.py"]
      }
    }
  }
"""

from __future__ import annotations

import json
import os
import re as _re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def _slugify(text: str) -> str:
    nfd = unicodedata.normalize("NFD", text.lower())
    ascii_text = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    return _re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")


# ---------------------------------------------------------------------------
# Load .env from repo root
# ---------------------------------------------------------------------------
REPO_PATH = Path(__file__).resolve().parent.parent
_dotenv = REPO_PATH / ".env"
if _dotenv.exists():
    for _line in _dotenv.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

TABBI_BASE_URL = os.environ.get("TABBI_BASE_URL", "http://localhost:8001").rstrip("/")
WORLD66_DIR = Path(os.environ.get("WORLD66_DIR", str(REPO_PATH / "world66")))

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "open_plan",
        "description": (
            "Open an existing Tabbi trip plan using a passphrase. "
            "Use this when the user provides a passphrase for a trip they already created. "
            "Returns the plan URL, slug, title, and city stops — then call research_city for each stop to add more places."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "passphrase": {
                    "type": "string",
                    "description": "The trip passphrase (e.g. 'scarlet-cobalt-swift')",
                },
            },
            "required": ["passphrase"],
        },
    },
    {
        "name": "plan_trip",
        "description": (
            "Create a Tabbi trip plan. Supports single or multi-city trips. "
            "Returns a shareable plan URL and a passphrase. "
            "After creating the plan, call research_city for each stop.\n\n"
            "IMPORTANT: Before calling this tool, ask the user these questions if not already answered:\n"
            "1. Who is travelling? (solo, couple, family with kids, group of friends, etc.)\n"
            "2. What are their main interests? (food, art, history, nature, nightlife, shopping, etc.)\n"
            "3. What's the travel pace? (relaxed, packed, somewhere in between)\n"
            "4. Any must-haves or things to avoid?\n"
            "Summarise the answers in the 'preferences' field so research_city can use them to curate places."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Trip title (e.g. 'Summer in Spain').",
                },
                "preferences": {
                    "type": "string",
                    "description": "Summary of traveller profile and interests, e.g. 'Family with two kids, interested in history and food, relaxed pace, avoid nightlife'.",
                },
                "stops": {
                    "type": "array",
                    "description": "List of destinations for a multi-city trip.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "destination": {"type": "string", "description": "City name — never a country (e.g. 'Barcelona', not 'Spain')"},
                            "start_date":  {"type": "string", "description": "YYYY-MM-DD"},
                            "end_date":    {"type": "string", "description": "YYYY-MM-DD"},
                            "notes":       {"type": "string"},
                        },
                        "required": ["destination", "start_date", "end_date"],
                    },
                },
                "destination": {"type": "string", "description": "Single city name"},
                "start_date":  {"type": "string", "description": "Start date YYYY-MM-DD (single city)"},
                "end_date":    {"type": "string", "description": "End date YYYY-MM-DD (single city)"},
                "notes":       {"type": "string"},
            },
        },
    },
    {
        "name": "research_city",
        "description": (
            "Returns existing world66 POIs for a city plus writing instructions. "
            "Always call this before doing any external research — add existing POIs first, "
            "then research only what's missing."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "city_path":   {"type": "string", "description": "World66 content path (e.g. 'europe/spain/catalonia/barcelona'). Use the city_path returned by plan_trip."},
                "city_title":  {"type": "string", "description": "Human-readable city name (e.g. 'Barcelona')"},
                "city_slug":   {"type": "string", "description": "City slug (e.g. 'barcelona'). Use the city_slug returned by open_plan or plan_trip."},
                "plan_slug":   {"type": "string", "description": "Plan slug, so existing plan items are shown and not duplicated."},
                "preferences": {"type": "string", "description": "Traveller preferences from plan_trip, passed through to curate place selection."},
            },
            "required": ["city_title"],
        },
    },
    {
        "name": "add_pois_to_plan",
        "description": (
            "Add existing world66 POI content paths directly to a trip plan. "
            "Call this with paths returned by research_city before doing any external research."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "plan_slug":  {"type": "string"},
                "passphrase": {"type": "string"},
                "city_slug":  {"type": "string"},
                "poi_paths":  {"type": "array", "items": {"type": "string"}},
            },
            "required": ["plan_slug", "passphrase", "city_slug", "poi_paths"],
        },
    },
    {
        "name": "submit_pois",
        "description": (
            "Submit researched places to Tabbi as plan entries. "
            "Call after research_city once you've written up the missing places. "
            "IMPORTANT: latitude and longitude are required for every POI. "
            "Call the geocode tool for each place before submitting — do NOT guess or estimate coordinates. "
            "Wrong coordinates break the map."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "plan_slug":  {"type": "string"},
                "passphrase": {"type": "string"},
                "city_slug":  {"type": "string"},
                "city_path":  {"type": "string"},
                "city_title": {"type": "string"},
                "intro": {
                    "type": "string",
                    "description": "2-4 sentence intro for the city stop, shown at the top of the stop page.",
                },
                "pois": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name":      {"type": "string"},
                            "category":  {"type": "string", "description": "Landmark|Museum|Restaurant|Market|Park|Neighbourhood|Viewpoint|Bar|Gallery"},
                            "body":      {"type": "string", "description": "2-4 paragraphs, under 280 words"},
                            "latitude":  {"type": "number", "description": "REQUIRED. Must be geocoded via search — never estimated."},
                            "longitude": {"type": "number", "description": "REQUIRED. Must be geocoded via search — never estimated."},
                            "image_url": {"type": "string", "description": "Direct image URL from Wikimedia Commons or similar free source."},
                        },
                        "required": ["name", "body", "latitude", "longitude"],
                    },
                },
            },
            "required": ["city_title", "pois", "plan_slug", "passphrase"],
        },
    },
    {
        "name": "remove_poi_from_plan",
        "description": (
            "Remove a place from a trip plan. "
            "Use this when the user wants to delete a specific place from a city stop. "
            "The poi_path must match exactly what's in the plan (e.g. 'europe/greece/athens/acropolis' or '~pois/...')."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "plan_slug":  {"type": "string"},
                "passphrase": {"type": "string"},
                "poi_path":   {"type": "string", "description": "Exact path of the place to remove"},
            },
            "required": ["plan_slug", "passphrase", "poi_path"],
        },
    },
    {
        "name": "geocode",
        "description": (
            "Look up the latitude and longitude of a place by name. "
            "Use this instead of fetching Nominatim directly — call it for every POI before submit_pois. "
            "Returns latitude, longitude, and the matched display name."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Place name to geocode, e.g. 'Eiffel Tower, Paris'"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_world66",
        "description": "Search the world66 travel guide for a destination or place.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
            },
            "required": ["query"],
        },
    },
]

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _http_post(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"HTTP {e.code}: {body}")


def _http_get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "tabbi-mcp/1.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)

# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def tool_open_plan(passphrase: str) -> str:
    try:
        result = _http_post(f"{TABBI_BASE_URL}/api/plans/open", {"passphrase": passphrase})
    except RuntimeError as e:
        return f"Failed to open plan: {e}"

    plan_slug = result["slug"]
    plan_items = _read_plan_items(plan_slug)

    lines = [
        f"**Opened plan: {result['title']}**", "",
        f"**Plan URL:** {result['url']}",
        f"**Passphrase:** `{result['passphrase']}`", "",
        "Here are the stops and what's already in the plan:",
    ]
    for city in result.get("cities", []):
        city_slug = city["city_slug"]
        existing = plan_items.get(city_slug, [])
        poi_count = _count_existing_pois(city.get("city_path", ""))
        guide_note = f"{poi_count} place(s) in the world66 guide" if poi_count else "no world66 content"
        lines.append(
            f"\n- **{city['city_title']}**: city_path={city['city_path']!r}, "
            f"city_slug={city_slug!r} — {guide_note}"
        )
        if existing:
            lines.append(f"  Already in plan ({len(existing)}): {', '.join(existing)}")
        else:
            lines.append("  Nothing added yet.")
    lines += [
        "",
        "Call research_city for each stop you want to fill in. "
        "Do NOT re-add places already listed above.",
    ]
    return "\n".join(lines)


def tool_plan_trip(stops=None, title="", destination="", start_date="", end_date="", notes="", preferences="") -> str:
    if not stops:
        if not destination:
            return "Error: provide 'stops' (multi-city) or 'destination' (single city)."
        stops = [{"destination": destination, "start_date": start_date, "end_date": end_date, "notes": notes}]
    try:
        result = _http_post(f"{TABBI_BASE_URL}/api/plans/create", {"title": title, "stops": stops})
    except RuntimeError as e:
        return f"Failed to create plan: {e}"

    lines = [
        "**Trip plan created**", "",
        f"**Plan URL:** {result['url']}",
        f"**Passphrase:** `{result['passphrase']}`", "",
        "Share this URL and passphrase with anyone joining the trip.",
        "Keep the passphrase — you need it for add_pois_to_plan and submit_pois.", "",
        "Now call research_city for each stop:",
    ]
    for city in result.get("cities", []):
        poi_count = _count_existing_pois(city.get("city_path", ""))
        coverage = f"{poi_count} place(s) already in the guide" if poi_count else "ready to research"
        lines.append(
            f"- **{city['city_title']}**: city_path={city['city_path']!r}, "
            f"city_slug={city['city_slug']!r} — {coverage}"
        )
    if preferences:
        lines += ["", f"Traveller preferences: {preferences}",
                  "Use these when calling research_city to pick and write places that match."]
    return "\n".join(lines)


def tool_research_city(city_title: str, city_path: str = "", city_slug: str = "", plan_slug: str = "", preferences: str = "") -> str:
    style_md = ""
    style_file = WORLD66_DIR / "STYLE.md"
    if style_file.exists():
        style_md = style_file.read_text()[:3000]

    existing_pois = []
    if city_path:
        city_dir = WORLD66_DIR / "content" / city_path
        if city_dir.is_dir():
            for md_file in sorted(city_dir.rglob("*.md")):
                try:
                    head = md_file.read_text(encoding="utf-8", errors="ignore")[:512]
                    title = ""
                    for line in head.splitlines():
                        if line.startswith("title:"):
                            title = line.split(":", 1)[1].strip().strip('"\'')
                            break
                    rel_path = str(md_file.relative_to(WORLD66_DIR / "content").with_suffix(""))
                    if "type: poi" in head or 'type: "poi"' in head:
                        existing_pois.append({"title": title, "path": rel_path})
                except Exception:
                    pass

    # What's already in the plan for this city
    already_in_plan: list[str] = []
    if plan_slug and city_slug:
        plan_items = _read_plan_items(plan_slug)
        already_in_plan = plan_items.get(city_slug, [])
    elif plan_slug and city_title:
        plan_items = _read_plan_items(plan_slug)
        slug_guess = _slugify(city_title)
        already_in_plan = plan_items.get(slug_guess, [])

    sections = [f"## What we have for {city_title}"]

    if already_in_plan:
        stop_full = len(already_in_plan) >= 10
        sections.append(
            f"### Already in the plan ({len(already_in_plan)}/10) — do NOT add these again\n"
            + "\n".join(f"- {item}" for item in already_in_plan)
            + ("\n\n**This stop is full (10/10). Do NOT call add_pois_to_plan or submit_pois for this city.**" if stop_full else
               f"\n\nRoom for {10 - len(already_in_plan)} more place(s) maximum.")
        )

    if existing_pois:
        place_lines = "\n".join(f"- {p['title']} (`{p['path']}`)" for p in existing_pois)
        sections.append(f"### World66 guide places ({len(existing_pois)} total)\n{place_lines}")
    else:
        sections.append(f"No existing world66 content for {city_title} — research from scratch.")

    # Filter out world66 POIs already in the plan so add_pois_to_plan only gets new ones
    already_titles = {a.lower() for a in already_in_plan}
    new_pois = [p for p in existing_pois if p["title"].lower() not in already_titles]

    if new_pois:
        prefs_note = f" Traveller profile: {preferences}. Pick places that match." if preferences else \
            " Pick the most essential ones a first-time visitor should not miss."
        poi_instruction = (
            f"1. From the {len(new_pois)} world66 place(s) above, select 6–8 that best fit this trip.{prefs_note} "
            f"Skip duplicates, obscure entries, and anything that doesn't stand on its own. "
            f"Call add_pois_to_plan with only those selected path(s):\n"
            + "\n".join(f"   - `{p['path']}`" for p in new_pois)
            + "\n   (Pick the best 6–8 from this list, not all of them.)\n"
        )
    else:
        poi_instruction = "1. No new world66 POIs to add.\n"

    sections.append(
        "## Instructions\n"
        + poi_instruction
        + (f"2. Check if the selected world66 places cover the traveller's interests ({preferences}). "
           f"Before writing anything new, call search_world66 for any interest area that has no coverage "
           f"(e.g. 'restaurants {city_title}', 'parks {city_title}') — use world66 paths if found. "
           f"Only write new places for genuine gaps where world66 has nothing.\n"
           if preferences else
           f"2. Before writing new places, call search_world66 for any obvious gaps "
           f"(e.g. 'restaurants {city_title}', 'parks {city_title}'). Use world66 paths if found.\n")
        + f"3. Only if world66 has no coverage for a category: write 1 new place to fill that gap. Total places per stop should not exceed 10.\n"
        f"   Default categories to fill: Landmark, Museum, Park, Market, Neighbourhood, Viewpoint, Gallery.\n"
        f"   Only add Restaurant or Bar if the traveller explicitly mentioned food, eating, bars, or nightlife in their preferences.\n"
        f"   For each new place:\n"
        f"   - 2-4 paragraphs of prose, under 280 words, per the style guide below\n"
        f"   - One category: Landmark|Museum|Restaurant|Market|Park|Neighbourhood|Viewpoint|Bar|Gallery\n"
        f"   - Exact latitude/longitude — call the geocode tool for each place.\n"
        f"     NEVER guess or estimate coordinates. Wrong coords break the map.\n"
        f"   - A direct image_url from Wikimedia Commons if one exists\n"
        f"4. Write a short intro for the city stop (2-4 sentences). "
        f"Write it in the same language the traveller used — if their preferences were in Dutch, write Dutch; French, write French; etc. "
        f"Make it specific and personal to their trip: mention the dates, the group, what they plan to do there. "
        f"POI descriptions (the `body` field) must always be in English.\n"
        f"5. Call submit_pois with the intro and all new places."
    )
    if style_md:
        sections.append(f"## Writing style guide\n{style_md}")
    return "\n\n".join(sections)


def tool_add_pois_to_plan(plan_slug: str, passphrase: str, city_slug: str, poi_paths: list) -> str:
    try:
        result = _http_post(f"{TABBI_BASE_URL}/api/plan/add-pois", {
            "plan_slug": plan_slug, "passphrase": passphrase,
            "city_slug": city_slug, "poi_paths": poi_paths,
        })
        return f"Added {result.get('added', 0)} existing place(s) to the plan."
    except RuntimeError as e:
        return f"Failed to add POIs: {e}"


def tool_submit_pois(city_title: str, pois: list, plan_slug: str, passphrase: str,
                     city_path: str = "", city_slug: str = "", intro: str = "") -> str:
    try:
        result = _http_post(f"{TABBI_BASE_URL}/api/research/submit", {
            "city_path": city_path, "city_title": city_title,
            "passphrase": passphrase, "pois": pois,
            "plan_slug": plan_slug, "city_slug": city_slug, "intro": intro,
        })
        count = result.get("accepted", result.get("written", len(pois)))
        return f"Accepted {count} place(s) for {city_title} — being written to the trip plan."
    except RuntimeError as e:
        return f"Submit failed: {e}"


def tool_remove_poi_from_plan(plan_slug: str, passphrase: str, poi_path: str) -> str:
    try:
        result = _http_post(f"{TABBI_BASE_URL}/api/plan/remove-poi", {
            "plan_slug": plan_slug, "passphrase": passphrase, "poi_path": poi_path,
        })
        if result.get("removed"):
            return f"Removed '{poi_path}' from the plan."
        return f"'{poi_path}' was not found in the plan."
    except RuntimeError as e:
        return f"Failed to remove: {e}"


def tool_geocode(query: str) -> str:
    try:
        url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(query)}&format=json&limit=1"
        result = _http_get(url)
    except Exception as e:
        return f"Geocoding failed: {e}"
    if not result:
        return f"No results found for '{query}'."
    r = result[0]
    return f"latitude: {r['lat']}, longitude: {r['lon']}, display_name: {r['display_name']}"


def tool_search_world66(query: str) -> str:
    try:
        url = f"{TABBI_BASE_URL}/api/search?q={urllib.parse.quote(query)}"
        result = _http_get(url)
    except Exception as e:
        return f"Search failed: {e}"
    results = result.get("results", [])
    if not results:
        return f"No results found for '{query}'."
    lines = [f"Search results for '{query}':"]
    for r in results[:10]:
        lines.append(
            f"- **{r.get('title', '')}** (`{r.get('url_path', '')}`) — "
            f"{r.get('page_type', '')}" + (f", {r['location']}" if r.get('location') else "")
        )
    return "\n".join(lines)


def _read_plan_items(plan_slug: str) -> dict[str, list[str]]:
    """Return {city_slug: [poi title or path, ...]} for what's already in the plan."""
    plan_file = REPO_PATH / "plans" / f"{plan_slug}.md"
    if not plan_file.exists():
        return {}
    items_by_city: dict[str, list[str]] = {}
    current_city = None
    for line in plan_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.rstrip()
        if line.startswith("## "):
            heading = line[3:].strip()
            city_part = heading.split("|")[0].strip()
            if "/" in city_part:
                current_city = _slugify(city_part.split("/")[-1].replace("_", " "))
            else:
                current_city = _slugify(city_part)
            items_by_city.setdefault(current_city, [])
        elif line.startswith("- ") and current_city is not None:
            entry = line[2:].strip()
            # strip markdown links, keep the label
            if entry.startswith("["):
                label = entry[1:entry.index("]")] if "]" in entry else entry
            else:
                label = entry.split("](")[0].lstrip("[") if "](" in entry else entry
            items_by_city[current_city].append(label)
    return items_by_city


def _count_existing_pois(city_path: str) -> int:
    if not city_path:
        return 0
    city_dir = WORLD66_DIR / "content" / city_path
    if not city_dir.is_dir():
        return 0
    count = 0
    for md_file in city_dir.rglob("*.md"):
        try:
            head = md_file.read_text(encoding="utf-8", errors="ignore")[:512]
            if "type: poi" in head or 'type: "poi"' in head:
                count += 1
        except Exception:
            pass
    return count

# ---------------------------------------------------------------------------
# MCP JSON-RPC dispatch
# ---------------------------------------------------------------------------

def _handle(message: dict) -> dict | None:
    method = message.get("method", "")
    msg_id = message.get("id")

    def ok(result):
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def err(code, msg):
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": msg}}

    if method == "initialize":
        return ok({
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            "serverInfo": {"name": "tabbi", "version": "1.0.0"},
        })

    if method in ("notifications/initialized", "notifications/cancelled"):
        return None

    if method == "tools/list":
        return ok({"tools": TOOLS})

    if method == "resources/list":
        return ok({"resources": []})

    if method == "prompts/list":
        return ok({"prompts": []})

    if method == "tools/call":
        params = message.get("params", {})
        name = params.get("name", "")
        args = params.get("arguments", {})
        try:
            if name == "open_plan":
                text = tool_open_plan(passphrase=args["passphrase"])
            elif name == "plan_trip":
                text = tool_plan_trip(
                    stops=args.get("stops"), title=args.get("title", ""),
                    destination=args.get("destination", ""), start_date=args.get("start_date", ""),
                    end_date=args.get("end_date", ""), notes=args.get("notes", ""),
                    preferences=args.get("preferences", ""),
                )
            elif name == "research_city":
                text = tool_research_city(
                    city_title=args["city_title"], city_path=args.get("city_path", ""),
                    city_slug=args.get("city_slug", ""), plan_slug=args.get("plan_slug", ""),
                    preferences=args.get("preferences", ""),
                )
            elif name == "add_pois_to_plan":
                text = tool_add_pois_to_plan(
                    plan_slug=args["plan_slug"], passphrase=args["passphrase"],
                    city_slug=args["city_slug"], poi_paths=args["poi_paths"],
                )
            elif name == "submit_pois":
                text = tool_submit_pois(
                    city_title=args["city_title"], pois=args["pois"],
                    plan_slug=args["plan_slug"], passphrase=args["passphrase"],
                    city_path=args.get("city_path", ""), city_slug=args.get("city_slug", ""),
                    intro=args.get("intro", ""),
                )
            elif name == "remove_poi_from_plan":
                text = tool_remove_poi_from_plan(
                    plan_slug=args["plan_slug"], passphrase=args["passphrase"],
                    poi_path=args["poi_path"],
                )
            elif name == "geocode":
                text = tool_geocode(query=args["query"])
            elif name == "search_world66":
                text = tool_search_world66(query=args["query"])
            else:
                return err(-32601, f"Unknown tool: {name}")
        except KeyError as e:
            return err(-32602, f"Missing argument: {e}")
        except Exception as e:
            return err(-32603, f"Tool error: {e}")
        return ok({"content": [{"type": "text", "text": text}]})

    if method == "ping":
        return ok({})

    return err(-32601, f"Method not found: {method}")


def main():
    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            message = json.loads(raw_line)
        except json.JSONDecodeError:
            sys.stdout.write(json.dumps({
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }) + "\n")
            sys.stdout.flush()
            continue
        response = _handle(message)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
