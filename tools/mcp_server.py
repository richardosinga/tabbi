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
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

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
            "After creating the plan, call research_city for each stop."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Trip title (e.g. 'Summer in Spain').",
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
                "city_path":  {"type": "string", "description": "World66 content path (e.g. 'europe/spain/catalonia/barcelona'). Use the city_path returned by plan_trip."},
                "city_title": {"type": "string", "description": "Human-readable city name (e.g. 'Barcelona')"},
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
            "Call after research_city once you've written up the missing places."
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
                            "name":     {"type": "string"},
                            "category": {"type": "string", "description": "Landmark|Museum|Restaurant|Market|Park|Neighbourhood|Viewpoint|Bar|Gallery"},
                            "body":     {"type": "string", "description": "2-4 paragraphs, under 280 words"},
                            "latitude": {"type": "number"},
                            "longitude":{"type": "number"},
                        },
                        "required": ["name", "body"],
                    },
                },
            },
            "required": ["city_title", "pois", "plan_slug", "passphrase"],
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

    lines = [
        f"**Opened plan: {result['title']}**", "",
        f"**Plan URL:** {result['url']}",
        f"**Passphrase:** `{result['passphrase']}`", "",
        "You can now add places to this trip. Call research_city for any stop:",
    ]
    for city in result.get("cities", []):
        poi_count = _count_existing_pois(city.get("city_path", ""))
        coverage = f"{poi_count} place(s) already in the guide" if poi_count else "ready to research"
        lines.append(
            f"- **{city['city_title']}**: city_path={city['city_path']!r}, "
            f"city_slug={city['city_slug']!r} — {coverage}"
        )
    return "\n".join(lines)


def tool_plan_trip(stops=None, title="", destination="", start_date="", end_date="", notes="") -> str:
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
    return "\n".join(lines)


def tool_research_city(city_title: str, city_path: str = "") -> str:
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

    sections = [f"## What we have for {city_title}"]
    if existing_pois:
        place_lines = "\n".join(f"- {p['title']} (`{p['path']}`)" for p in existing_pois)
        sections.append(f"### Places ({len(existing_pois)})\n{place_lines}")
    else:
        sections.append(f"No existing content for {city_title} — research from scratch.")

    sections.append(
        "## Instructions\n"
        f"1. Call add_pois_to_plan with ALL paths above to add existing content.\n"
        f"2. Use web search to find what's notable in {city_title}.\n"
        f"3. Write 2-4 new places not already in the list above. For each:\n"
        f"   - 2-4 paragraphs of prose, under 280 words, per the style guide below\n"
        f"   - One category: Landmark|Museum|Restaurant|Market|Park|Neighbourhood|Viewpoint|Bar|Gallery\n"
        f"   - Latitude/longitude coordinates\n"
        f"4. Write a 2-4 sentence intro for the city stop.\n"
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
        return (
            f"Submitted {len(pois)} place(s) for {city_title}. "
            f"Server added {result.get('written', 0)} to the trip plan."
        )
    except RuntimeError as e:
        return f"Submit failed: {e}"


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
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "tabbi", "version": "1.0.0"},
        })

    if method == "notifications/initialized":
        return None

    if method == "tools/list":
        return ok({"tools": TOOLS})

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
                )
            elif name == "research_city":
                text = tool_research_city(city_title=args["city_title"], city_path=args.get("city_path", ""))
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
