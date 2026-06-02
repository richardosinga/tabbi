import hashlib
import json
import re
import secrets
import subprocess
import unicodedata
from functools import wraps
from pathlib import Path

from django.conf import settings
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.utils.safestring import mark_safe
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from django.conf import settings as _settings
from world66_content.models import CONTENT_DIR, load_page, resolve_location_name

def _slugify(text: str) -> str:
    """ASCII slug: transliterates unicode (ü→u, é→e) rather than stripping it."""
    nfd = unicodedata.normalize("NFD", text.lower())
    ascii_text = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")


def _w66_url(path):
    """Return an absolute world66.ai URL for a content path."""
    base = getattr(_settings, "WORLD66_SITE_URL", "https://world66.ai")
    return f"{base}/{path}"

PLANS_DIR = Path(settings.BASE_DIR) / "plans"
_PASSWORDS_FILE = PLANS_DIR / ".passwords.json"
_GEOCACHE_FILE = PLANS_DIR / ".geocache.json"


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _hash_password(password, salt=None):
    if salt is None:
        salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260000)
    return f"{salt}${h.hex()}"


def _check_password(password, stored):
    salt, _ = stored.split("$", 1)
    return secrets.compare_digest(_hash_password(password, salt), stored)


def _load_passwords():
    if not _PASSWORDS_FILE.is_file():
        return {}
    return json.loads(_PASSWORDS_FILE.read_text())


def _save_password(slug, password):
    data = _load_passwords()
    data[slug] = _hash_password(password)
    _PASSWORDS_FILE.write_text(json.dumps(data))


def _plan_authenticated(request, slug):
    return slug in request.session.get("authenticated_plans", [])


def _mark_plan_authenticated(request, slug):
    plans = request.session.get("authenticated_plans", [])
    if slug not in plans:
        plans = plans + [slug]
        request.session["authenticated_plans"] = plans


def _require_plan_auth(view_fn):
    @wraps(view_fn)
    def wrapper(request, slug, *args, **kwargs):
        passwords = _load_passwords()
        if slug not in passwords:
            return HttpResponseRedirect(f"/auth/signup/{slug}/")
        if not _plan_authenticated(request, slug):
            return HttpResponseRedirect(f"/auth/login/{slug}/?next={request.path}")
        return view_fn(request, slug, *args, **kwargs)
    return wrapper


def _plan_title(slug):
    import frontmatter as fm
    path = PLANS_DIR / f"{slug}.md"
    if not path.is_file():
        return slug
    return fm.load(path).metadata.get("title", slug)


# ── Content helpers ───────────────────────────────────────────────────────────

def _image_path(page):
    """Return the relative content path for a page's image, or None."""
    image = page.meta.get("image", "")
    if not image:
        return None
    for candidate in [
        f"{page.path}/{image}",
        f"{page.path.rsplit('/', 1)[0]}/{image}" if "/" in page.path else image,
    ]:
        if (CONTENT_DIR / candidate).is_file():
            return candidate
    return None


def _normalize(s):
    return re.sub(r"[\s_\-]+", "", s.lower())


def _find_poi_in_city(text, city_path):
    city_dir = CONTENT_DIR / city_path
    if not city_dir.is_dir():
        return None
    needle = _normalize(text)
    best = None
    for md_file in city_dir.rglob("*.md"):
        slug = md_file.stem
        if _normalize(slug) == needle:
            rel = str(md_file.relative_to(CONTENT_DIR).with_suffix(""))
            page = load_page(rel)
            if page and page.page_type == "poi":
                return page
        if best is None and needle in _normalize(slug):
            rel = str(md_file.relative_to(CONTENT_DIR).with_suffix(""))
            page = load_page(rel)
            if page and page.page_type == "poi":
                best = page
    if best:
        return best
    for md_file in city_dir.rglob("*.md"):
        rel = str(md_file.relative_to(CONTENT_DIR).with_suffix(""))
        page = load_page(rel)
        if page and page.page_type == "poi" and needle in _normalize(page.title):
            return page
    return None


# ── Geocoding ─────────────────────────────────────────────────────────────────

def _load_geocache():
    if _GEOCACHE_FILE.exists():
        try:
            return json.loads(_GEOCACHE_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_geocache(cache):
    _GEOCACHE_FILE.write_text(json.dumps(cache, indent=2))


def _geocode_nominatim(query):
    import urllib.request
    import urllib.parse
    params = urllib.parse.urlencode({"q": query, "format": "json", "limit": 1})
    url = f"https://nominatim.openstreetmap.org/search?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "World66/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=4) as r:
            data = json.loads(r.read())
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception:
        pass
    return None


def _city_coords(stop):
    city_path = stop.get("city_path")
    city_name = stop.get("city", "")
    cache_key = f"city:{city_path or city_name}"

    if city_path:
        city_page = load_page(city_path)
        if city_page and city_page.meta.get("latitude") and city_page.meta.get("longitude"):
            return float(city_page.meta["latitude"]), float(city_page.meta["longitude"])

    geocache = _load_geocache()
    if cache_key in geocache:
        return tuple(geocache[cache_key]) if geocache[cache_key] else None

    result = _geocode_nominatim(city_name)
    geocache[cache_key] = list(result) if result else None
    _save_geocache(geocache)
    return result


def _stop_markers(stop):
    geocache = _load_geocache()
    cache_dirty = False
    markers = []
    city_name = stop.get("city", "")

    for item in stop["items"]:
        page = item["page"]
        if not page:
            continue
        lat = page.meta.get("latitude")
        lng = page.meta.get("longitude")
        if lat and lng:
            markers.append({
                "lat": float(lat), "lng": float(lng),
                "title": page.title, "url": _w66_url(page.path),
            })
        elif page.path not in geocache:
            result = _geocode_nominatim(f"{page.title}, {city_name}")
            geocache[page.path] = list(result) if result else None
            cache_dirty = True
            if result:
                markers.append({
                    "lat": result[0], "lng": result[1],
                    "title": page.title, "url": _w66_url(page.path),
                })
        elif geocache[page.path]:
            lat, lng = geocache[page.path]
            markers.append({
                "lat": lat, "lng": lng,
                "title": page.title, "url": _w66_url(page.path),
            })

    if cache_dirty:
        _save_geocache(geocache)
    return markers


# ── Plan parsing ──────────────────────────────────────────────────────────────

def _parse_plan(path):
    import frontmatter as fm
    if not path.is_file():
        return None
    post = fm.load(path)
    slug = path.stem
    title = post.metadata.get("title", slug)
    stops = _parse_stops(post.content, slug)
    keywords = []
    for line in post.content.splitlines():
        m = re.match(r"^interests:\s*(.+)$", line.strip(), re.IGNORECASE)
        if m:
            keywords = [k.strip().lower() for k in re.split(r"[,;]+", m.group(1)) if k.strip()]
            break
    budget = None
    for line in post.content.splitlines():
        m = re.match(r"^budget:\s*(.+)$", line.strip(), re.IGNORECASE)
        if m:
            budget = m.group(1).strip()
            break
    description = post.metadata.get("description", "") or ""
    passport_slug = post.metadata.get("passport", "") or ""
    return {"slug": slug, "title": title, "description": description, "passport_slug": passport_slug, "body": post.content, "stops": stops, "keywords": keywords, "budget": budget}


def _parse_stops(body, plan_slug):
    stops = []
    current = None
    _months = (r"(?:january|february|march|april|may|june|july|august"
               r"|september|october|november|december"
               r"|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\b")
    _date_re = re.compile(
        rf"\b(\d{{1,2}}\s+{_months}|{_months}\s+\d{{1,2}})",
        re.IGNORECASE,
    )

    for line in body.splitlines():
        h2 = re.match(r"^##\s+(.+)$", line)
        if h2:
            heading = h2.group(1)
            if "|" in heading:
                city_part, dates = heading.split("|", 1)
            else:
                dm = _date_re.search(heading)
                if dm:
                    city_part = heading[:dm.start()]
                    dates = heading[dm.start():]
                else:
                    city_part, dates = heading, ""
            city_part = city_part.strip()
            if "/" in city_part:
                city_path = city_part
                city_name = city_part.split("/")[-1].replace("_", " ").title()
            else:
                city_name = city_part
                hint = plan_slug + " " + " ".join(s.get("city_path") or "" for s in stops)
                city_path = resolve_location_name(city_part, hint)
            city_slug = _slugify(city_name)
            current = {
                "city": city_name,
                "city_slug": city_slug,
                "city_path": city_path,
                "dates": dates.strip(),
                "url": f"/plans/{plan_slug}/{city_slug}/",
                "items": [],
            }
            stops.append(current)
            continue
        if current is None:
            continue
        bullet = re.match(r"^[-*]\s+(.+)$", line)
        if bullet:
            text = bullet.group(1).strip()
            page = None
            external_url = None
            display_label = None
            display_domain = None
            if re.match(r"^https?://", text):
                external_url = text
                from urllib.parse import urlparse as _urlparse
                _p = _urlparse(text)
                display_domain = _p.netloc.lstrip("www.")
                display_path = (_p.path.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").replace("_", " ").title()
                                if _p.path and _p.path != "/" else "")
                display_label = display_path or display_domain
            elif text.startswith("/"):
                page = load_page(text.lstrip("/"))
            elif text.startswith("~pois/"):
                from world66_content.models import _load_page_from_file
                poi_file = PLANS_DIR / "pois" / (text[6:] + ".md")
                if poi_file.is_file():
                    page = _load_page_from_file(poi_file, text)
            elif re.match(r"^[\w/_-]+$", text):
                page = load_page(text)
                if not page and current.get("city_path"):
                    page = _find_poi_in_city(text, current["city_path"])
            else:
                if current.get("city_path"):
                    page = _find_poi_in_city(text, current["city_path"])
            image_url = None
            if page:
                if not page.meta.get("snippet") and page.body:
                    first = next((p.strip() for p in page.body.split("\n\n") if p.strip()), "")
                    if first:
                        page.meta["snippet"] = first[:180] + ("…" if len(first) > 180 else "")
                if page.meta.get("image_url"):
                    image_url = page.meta["image_url"]
                else:
                    img = _image_path(page)
                    if img:
                        image_url = f"/content-image/{img}"
            current["items"].append({
                "text": text,
                "page": page,
                "external_url": external_url,
                "display_label": display_label if external_url else None,
                "display_domain": display_domain if external_url else None,
                "image_url": image_url,
            })

    for stop in stops:
        if stop.get("city_path"):
            stop["destination_url"] = _w66_url(stop["city_path"])
        else:
            dest_url = None
            for item in stop["items"]:
                if item["page"] and "/" in item["page"].path:
                    dest_url = _w66_url(item["page"].path.rsplit("/", 1)[0])
                    break
            stop["destination_url"] = dest_url

    return stops


def authenticated_plan_stops(request):
    """Return list of {slug, title, stops, poi_paths} for authenticated plans.

    Public API used by guide.views to show trip tags on POI pages.
    """
    result = []
    for slug in request.session.get("authenticated_plans", []):
        plan = _parse_plan(PLANS_DIR / f"{slug}.md")
        if plan:
            poi_paths = {item["text"] for s in plan["stops"] for item in s["items"]}
            result.append({
                "slug": slug,
                "title": plan["title"],
                "stops": [{"city": s["city"], "city_slug": s["city_slug"], "url": s["url"]} for s in plan["stops"]],
                "poi_paths": poi_paths,
            })
    return result


# ── Passphrase generation ─────────────────────────────────────────────────────

_PASSPHRASE_WORDS = [
    "canyon", "delta", "fjord", "glacier", "harbor", "lagoon", "meadow", "mesa",
    "oasis", "rapids", "reef", "ridge", "steppe", "summit", "tundra", "valley",
    "atlas", "compass", "ferry", "lantern", "passage", "pilgrim", "rover", "voyage",
    "amber", "birch", "cedar", "cobalt", "coral", "crimson", "dusk", "ember",
    "falcon", "fern", "flint", "heron", "indigo", "jasper", "lemon", "lotus",
    "maple", "marigold", "mist", "moonrise", "mossy", "ochre", "onyx", "pebble",
    "pine", "pollen", "quartz", "saffron", "sage", "scarlet", "sienna", "slate",
    "spruce", "sterling", "talon", "thistle", "thorn", "topaz", "umber", "wren",
    "ancient", "azure", "bold", "bright", "calm", "distant", "golden", "hidden",
    "ivory", "jade", "keen", "lofty", "lunar", "misty", "noble", "pale",
    "quiet", "rugged", "serene", "silent", "silver", "slow", "solar", "spare",
    "stone", "swift", "tall", "vast", "warm", "wild",
]


def _generate_passphrase():
    import random
    passwords = _load_passwords()
    existing = set(passwords.keys())
    for _ in range(100):
        words = random.sample(_PASSPHRASE_WORDS, 3)
        phrase = "-".join(words)
        if phrase not in existing:
            return phrase
    return "-".join(random.sample(_PASSPHRASE_WORDS, 3)) + f"-{random.randint(10,99)}"


# ── Views ─────────────────────────────────────────────────────────────────────

def plan_list(request):
    authenticated = set(request.session.get("authenticated_plans", []))
    join_error = request.session.pop("plan_join_error", None)
    plans = []
    for f in sorted(PLANS_DIR.glob("*.md")):
        slug = f.stem
        if slug not in authenticated:
            continue
        plan = _parse_plan(f)
        if not plan:
            continue
        stops = plan["stops"]
        total_places = sum(len(s["items"]) for s in stops)
        cover_url = None
        for stop in stops:
            if cover_url:
                break
            city_page = load_page(stop["city_path"]) if stop.get("city_path") else None
            img = _image_path(city_page) if city_page else None
            if img:
                cover_url = f"/content-image/{img}"
            else:
                for item in stop["items"]:
                    if item.get("image_url"):
                        cover_url = item["image_url"]
                        break
        all_dates = [s["dates"] for s in stops if s.get("dates")]
        date_range = (f"{all_dates[0].split('–')[0].strip()} – {all_dates[-1].split('–')[-1].strip()}"
                      if len(all_dates) > 1 else (all_dates[0] if all_dates else None))
        cities = [s["city"] for s in stops if s.get("city")]
        plans.append({
            "slug": slug,
            "title": plan["title"],
            "description": plan.get("description", ""),
            "stop_count": len(stops),
            "place_count": total_places,
            "cities": cities,
            "date_range": date_range,
            "cover_url": cover_url,
        })
    return render(request, "plans/plan_list.html", {"plans": plans, "join_error": join_error})


def plan_join(request):
    if request.method != "POST":
        return HttpResponseRedirect("/plans/")
    pw = request.POST.get("password", "")
    passwords = _load_passwords()
    for slug, hashed in passwords.items():
        if _check_password(pw, hashed):
            _mark_plan_authenticated(request, slug)
            return HttpResponseRedirect(f"/plans/{slug}/")
    request.session["plan_join_error"] = "No trip found with that passphrase."
    return HttpResponseRedirect("/plans/")


def plan_new(request):
    error = None
    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        if not title:
            error = "Please enter a trip title."
        else:
            import frontmatter as fm
            slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
            path = PLANS_DIR / f"{slug}.md"
            if path.exists():
                error = f"A trip named '{slug}' already exists."
            else:
                passphrase = _generate_passphrase()
                description_raw = request.POST.get("description", "").strip()
                locations_raw = request.POST.get("locations", "").strip()
                budget_raw = request.POST.get("budget", "").strip()
                body_lines = []
                if budget_raw:
                    body_lines.append(f"budget: {budget_raw}\n")
                city_headings = []
                if locations_raw:
                    for loc in re.split(r"[,;]+", locations_raw):
                        loc = loc.strip()
                        if not loc:
                            continue
                        city_path = resolve_location_name(loc, title)
                        if city_path:
                            city_page = load_page(city_path)
                            city_headings.append(f"## {city_page.title if city_page else loc.title()}")
                        else:
                            city_headings.append(f"## {loc.title()}")
                else:
                    # Fall back to extracting locations from the trip title
                    title_words = re.split(r"[\s,&+]+", title)
                    i = 0
                    while i < len(title_words):
                        matched = False
                        for length in range(min(4, len(title_words) - i), 0, -1):
                            phrase = " ".join(title_words[i:i+length])
                            if resolve_location_name(phrase, title):
                                city_headings.append(f"## {phrase}")
                                i += length
                                matched = True
                                break
                        if not matched:
                            i += 1
                if city_headings:
                    if body_lines:
                        body_lines.append("")
                    body_lines.extend(city_headings)
                body = "\n".join(body_lines)
                meta = {"title": title, "passphrase": passphrase}
                if description_raw:
                    meta["description"] = description_raw
                passport_slugs = request.session.get("authenticated_passports", [])
                if passport_slugs:
                    meta["passport"] = passport_slugs[0]
                post = fm.Post(body, **meta)
                with open(path, "w", encoding="utf-8") as fh:
                    fm.dump(post, fh)
                _save_password(slug, passphrase)
                _mark_plan_authenticated(request, slug)
                request.session["new_plan_passphrase"] = passphrase
                return HttpResponseRedirect(f"/plans/{slug}/created/")
    return render(request, "plans/plan_new.html", {"error": error})


@_require_plan_auth
def plan_created(request, slug):
    passphrase = request.session.pop("new_plan_passphrase", None)
    plan = _parse_plan(PLANS_DIR / f"{slug}.md")
    if not plan:
        raise Http404
    return render(request, "plans/plan_created.html", {"plan": plan, "passphrase": passphrase})


@_require_plan_auth
def plan_detail(request, slug):
    plan = _parse_plan(PLANS_DIR / f"{slug}.md")
    if not plan:
        raise Http404

    for stop in plan["stops"]:
        city_page = load_page(stop["city_path"]) if stop.get("city_path") else None
        img = _image_path(city_page) if city_page else None
        if not img:
            for item in stop["items"]:
                if item.get("image_url"):
                    stop["city_image_url"] = item["image_url"]
                    break
        stop["city_image_url"] = f"/content-image/{img}" if img else stop.get("city_image_url")

    stop_markers = []
    for stop in plan["stops"]:
        pts = _stop_markers(stop)
        if pts:
            lat = sum(m["lat"] for m in pts) / len(pts)
            lng = sum(m["lng"] for m in pts) / len(pts)
        else:
            coords = _city_coords(stop)
            if coords:
                lat, lng = coords
            else:
                continue
        stop_markers.append({
            "lat": lat, "lng": lng,
            "title": stop["city"], "dates": stop["dates"],
            "url": stop["url"],
        })

    if len(plan["stops"]) == 1:
        return HttpResponseRedirect(plan["stops"][0]["url"])

    import frontmatter as _fmb
    _plan_meta = _fmb.load(str(PLANS_DIR / f"{slug}.md")).metadata
    all_budgets = _plan_meta.get("budgets") or {}
    total_budget: dict = {"hotel": 0.0, "food": 0.0, "activities": 0.0, "travel": 0.0}
    currency = ""
    for b in all_budgets.values():
        for k in ("hotel", "food", "activities", "travel"):
            try:
                total_budget[k] += float(b.get(k) or 0)
            except (ValueError, TypeError):
                pass
        if not currency and b.get("currency"):
            currency = b["currency"]
    total_budget["currency"] = currency
    total_budget["total"] = sum(total_budget[k] for k in ("hotel", "food", "activities", "travel"))

    return render(request, "plans/plan_detail.html", {
        "plan": plan,
        "stop_markers": mark_safe(json.dumps(stop_markers)),
        "total_budget": total_budget,
    })


@_require_plan_auth
def plan_stop(request, slug, city_slug):
    plan = _parse_plan(PLANS_DIR / f"{slug}.md")
    if not plan:
        raise Http404
    stop = next((s for s in plan["stops"] if s["city_slug"] == city_slug), None)
    if not stop:
        raise Http404
    markers = _stop_markers(stop)
    city_page = load_page(stop["city_path"]) if stop.get("city_path") else None
    if not markers:
        coords = _city_coords(stop)
        if coords:
            markers = [{"lat": coords[0], "lng": coords[1], "title": stop["city"], "url": stop.get("destination_url") or ""}]
    city_snippet = None
    city_image_url = None
    if city_page:
        city_snippet = city_page.meta.get("snippet") or ""
        if not city_snippet and city_page.body:
            first_para = re.split(r"\n\n+", city_page.body.strip())[0]
            first_para = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", first_para)
            first_para = re.sub(r"[*_`#>]+", "", first_para).strip()
            city_snippet = first_para[:300] + ("…" if len(first_para) > 300 else "")
        img = _image_path(city_page)
        if img:
            city_image_url = f"/content-image/{img}"

    # Checked in priority order — specific beats generic, so things_to_do last
    _TAG_PRIORITY = [
        ("museum", "🏛️"), ("museums", "🏛️"), ("gallery", "🖼️"),
        ("beach", "🏖️"), ("beaches", "🏖️"),
        ("hiking", "🥾"),
        ("castle", "🏰"), ("palace", "👑"),
        ("church", "⛪"), ("cathedral", "⛪"),
        ("temple", "🛕"), ("mosque", "🕌"),
        ("market", "🧺"),
        ("shopping", "🛍️"), ("shop", "🛍️"),
        ("nightlife", "🎉"), ("club", "🎉"),
        ("bars_and_cafes", "🍺"), ("bar", "🍺"), ("pub", "🍺"),
        ("restaurant", "🍽️"), ("eating_out", "🍽️"),
        ("food", "🍜"), ("cuisine", "🍜"),
        ("cafe", "☕"), ("coffee", "☕"),
        ("jazz", "🎷"), ("music", "🎵"),
        ("theatre", "🎭"), ("theater", "🎭"), ("opera", "🎭"),
        ("cinema", "🎬"),
        ("architecture", "🏗️"),
        ("sport", "⚽"), ("cycling", "🚴"), ("swimming", "🏊"),
        ("boat", "⛵"), ("canal_ring", "🛶"),
        ("historic_site", "📜"), ("historical_site", "📜"), ("history", "📜"), ("historic", "📜"),
        ("heritage", "🏺"),
        ("art", "🎨"),
        ("garden", "🌸"), ("park", "🌳"),
        ("zoo", "🦁"), ("wildlife", "🦁"),
        ("nature", "🌿"),
        ("spa", "🧖"),
        ("viewpoint", "👁️"),
        ("monument", "🗿"),
        ("square", "🏙️"), ("neighbourhood", "🏘️"),
        ("festival", "🎪"), ("festivals", "🎪"),
        ("day_trips", "🗺️"), ("day_trip", "🗺️"),
        ("landmark", "📍"), ("sight", "📍"), ("sights", "📍"),
        ("things_to_do", "📍"),  # generic catch-all, last
    ]
    # Title/slug keywords for content-specific icons (pizza → 🍕 etc.)
    _TITLE_KEYWORDS = [
        ("pizza", "🍕"), ("burger", "🍔"), ("steak", "🥩"),
        ("sushi", "🍱"), ("ramen", "🍜"), ("noodle", "🍜"),
        ("taco", "🌮"), ("pasta", "🍝"), ("paella", "🥘"),
        ("tapas", "🫒"), ("kebab", "🥙"),
        ("bakery", "🥐"), ("bread", "🥖"), ("cake", "🎂"),
        ("ice cream", "🍦"), ("gelato", "🍦"),
        ("chocolate", "🍫"),
        ("wine", "🍷"), ("cocktail", "🍸"), ("gin", "🍸"),
        ("whisky", "🥃"), ("whiskey", "🥃"),
        ("beer", "🍺"), ("brewery", "🍺"),
        ("tea", "🍵"),
        ("jazz", "🎷"), ("blues", "🎵"), ("rock", "🎸"),
        ("museum", "🏛️"), ("gallery", "🖼️"),
        ("park", "🌳"), ("garden", "🌸"),
        ("beach", "🏖️"), ("surf", "🏄"),
        ("market", "🧺"), ("bazaar", "🧺"),
        ("castle", "🏰"), ("palace", "👑"), ("fort", "🏰"),
        ("cathedral", "⛪"), ("church", "⛪"),
        ("mosque", "🕌"), ("temple", "🛕"),
        ("hammam", "🧖"), ("spa", "🧖"),
        ("aquarium", "🐠"), ("zoo", "🦁"),
        ("boat", "⛵"), ("kayak", "🛶"), ("canoe", "🛶"),
        ("bike", "🚴"), ("cycling", "🚴"),
        ("tower", "🗼"), ("bridge", "🌉"),
        ("waterfall", "💧"), ("lake", "🏞️"),
        ("library", "📚"), ("bookshop", "📚"),
        ("theatre", "🎭"), ("theater", "🎭"), ("opera", "🎭"),
        ("cinema", "🎬"), ("film", "🎬"),
        ("statue", "🗿"), ("monument", "🗿"),
    ]
    _PALETTE = [
        "#FFF3C4", "#FFE0B2", "#E8F5E9", "#E3F2FD",
        "#FCE4EC", "#F3E5F5", "#E0F7FA", "#FFF8E1",
    ]

    def _placeholder(page):
        # Check title + slug for content keywords first
        slug_words = page.path.split("/")[-1].replace("_", " ").replace("-", " ")
        search = page.title.lower() + " " + slug_words.lower()
        emoji = next((e for kw, e in _TITLE_KEYWORDS if kw in search), None)
        # Fall back to priority-ordered tag matching (specific tags win over generic)
        if not emoji:
            tag_set = {t.lower() for t in page.tags}
            emoji = next((e for k, e in _TAG_PRIORITY if k in tag_set), "📍")
        h = 0
        for c in page.path:
            h = (h * 31 + ord(c)) & 0xFFFFFFFF
        bg = _PALETTE[h % len(_PALETTE)]
        return emoji, bg

    suggestions = []
    if stop.get("city_path"):
        already_added = {item["text"] for item in stop["items"]}
        already_added_paths = {item["page"].path for item in stop["items"] if item["page"]}
        note_needles = [_normalize(item["text"]) for item in stop["items"]
                        if not item["page"] and not item["external_url"]]
        _KEYWORD_EXPANSIONS = {
            "art": ["museum", "gallery", "art", "culture", "exhibition"],
            "culture": ["museum", "theatre", "theater", "opera", "concert", "culture", "heritage", "history"],
            "opera": ["opera", "concert", "music", "theatre", "theater"],
            "music": ["music", "concert", "jazz", "opera", "nightlife"],
            "food": ["restaurant", "food", "market", "cafe", "dining", "cuisine"],
            "hiking": ["hiking", "nature", "walk", "park", "outdoors", "trail"],
            "beaches": ["beach", "sea", "coast", "swimming", "waterfront"],
            "history": ["history", "heritage", "museum", "monument", "cathedral", "church", "castle"],
            "architecture": ["architecture", "building", "design"],
            "nightlife": ["nightlife", "bar", "club", "music"],
            "shopping": ["shopping", "market", "shop"],
            "nature": ["nature", "park", "garden", "outdoors"],
        }
        expanded_keywords = set()
        all_keywords = list(plan.get("keywords", []))
        # Merge passport interests if a passport is linked
        passport_slug = plan.get("passport_slug", "")
        if passport_slug:
            try:
                from passport.views import _load_passport
                pp = _load_passport(passport_slug)
                if pp:
                    all_keywords = all_keywords + list(pp.get("interests", []))
            except Exception:
                pass
        for k in all_keywords:
            kn = k.lower().strip()
            expanded_keywords.add(_normalize(kn))
            for exp in _KEYWORD_EXPANSIONS.get(kn, []):
                expanded_keywords.add(_normalize(exp))

        _FOOD_TAGS = {"eating_out", "restaurant", "food", "market", "cuisine", "dining"}
        _DRINKS_TAGS = {"bars_and_cafes", "bar", "nightlife", "drinks", "pub", "cafe", "coffee"}

        def _suggestion_category(page_tags):
            tag_set = {t.lower() for t in page_tags}
            if tag_set & _DRINKS_TAGS:
                return "drinks"
            if tag_set & _FOOD_TAGS:
                return "food"
            return "todo"

        city_dir = CONTENT_DIR / stop["city_path"]
        for md_file in sorted(city_dir.rglob("*.md")):
            rel = str(md_file.relative_to(CONTENT_DIR).with_suffix(""))
            if rel in already_added or rel in already_added_paths:
                continue
            page = load_page(rel)
            if not page or page.page_type != "poi":
                continue
            img = _image_path(page)
            slug_norm = _normalize(page.path.split("/")[-1])
            title_norm = _normalize(page.title)
            tags_norm = [_normalize(t) for t in page.tags]
            poi_text = slug_norm + " " + title_norm + " " + " ".join(tags_norm)
            note_match = any(n in poi_text or poi_text in n for n in note_needles) if note_needles else False
            keyword_match = any(k in poi_text for k in expanded_keywords) if expanded_keywords else False
            score = (2 if note_match else 0) + (2 if keyword_match else 0) + (1 if img else 0)
            if not page.meta.get("snippet") and page.body:
                first = next((p.strip() for p in page.body.split("\n\n") if p.strip()), "")
                if first:
                    page.meta["snippet"] = first[:100] + ("…" if len(first) > 100 else "")
            ph_emoji, ph_bg = _placeholder(page)
            suggestions.append({
                "page": page,
                "image_url": f"/content-image/{img}" if img else None,
                "_score": score,
                "note_match": note_match or keyword_match,
                "category": _suggestion_category(page.tags),
                "placeholder_emoji": ph_emoji,
                "placeholder_bg": ph_bg,
            })
        suggestions.sort(key=lambda x: -x["_score"])

    _CATEGORIES = [
        ("Do", "todo", "🎯"),
        ("Eat", "food", "🍜"),
        ("Drink", "drinks", "🍻"),
    ]
    suggestion_groups = [
        {"label": label, "key": key, "icon": icon, "items": [s for s in suggestions if s["category"] == key]}
        for label, key, icon in _CATEGORIES
        if any(s["category"] == key for s in suggestions)
    ]

    for item in stop["items"]:
        p = item.get("page")
        item["is_expandable"] = bool(p and p.body and not p.meta.get("external_url") and not p.meta.get("source_url"))

    import frontmatter as _fmb
    _plan_meta = _fmb.load(str(PLANS_DIR / f"{plan['slug']}.md")).metadata
    stop_budget = (_plan_meta.get("budgets") or {}).get(stop["city_slug"]) or {}

    EAT_SIGNALS = {"restaurant", "eating_out", "food", "cafe", "café"}
    DRINK_SIGNALS = {"bar", "bars_and_cafes", "pub", "nightlife", "drink"}

    def _item_group(item):
        if not item["page"]:
            return "do"
        page = item["page"]
        cat = (page.meta.get("category") or "").lower()
        tags = [t.lower() for t in (page.tags if hasattr(page, "tags") else page.meta.get("tags") or [])]
        signals = {cat} | set(tags)
        if signals & DRINK_SIGNALS:
            return "drink"
        if signals & EAT_SIGNALS:
            return "eat"
        return "do"

    items_do    = [i for i in stop["items"] if _item_group(i) == "do"]
    items_eat   = [i for i in stop["items"] if _item_group(i) == "eat"]
    items_drink = [i for i in stop["items"] if _item_group(i) == "drink"]
    ungrouped   = False

    inspo_count = (1 if city_image_url else 0) + sum(1 for i in stop["items"] if i.get("image_url"))

    return render(request, "plans/plan_stop.html", {
        "plan": plan,
        "stop": stop,
        "markers": mark_safe(json.dumps(markers)),
        "city_snippet": city_snippet,
        "city_image_url": city_image_url,
        "suggestion_groups": suggestion_groups,
        "budget_json": json.dumps(stop_budget),
        "items_do": items_do,
        "items_eat": items_eat,
        "items_drink": items_drink,
        "ungrouped": ungrouped,
        "inspo_count": inspo_count,
    })


def _plan_save_budget(slug, city_slug, budget_data):
    import frontmatter as fm
    path = PLANS_DIR / f"{slug}.md"
    post = fm.load(path)
    budgets = dict(post.metadata.get("budgets") or {})
    budgets[city_slug] = {k: v for k, v in budget_data.items() if v is not None}
    post.metadata["budgets"] = budgets
    with open(path, "w", encoding="utf-8") as fh:
        fm.dump(post, fh)


def _parse_poi_price(poi_path):
    """Return the numeric price of a POI, or None if it has no parseable price."""
    page = load_page(poi_path)
    if not page:
        return None
    raw = str(page.meta.get("price", "") or "")
    m = re.search(r"\d+(?:[.,]\d+)?", raw.replace(",", "."))
    if not m:
        return None
    return float(m.group().replace(",", "."))


def _budget_add_poi_price(slug, city_slug, poi_path):
    """If the POI has a parseable numeric price, add it to the stop's activities budget."""
    amount = _parse_poi_price(poi_path)
    if amount is None:
        return
    import frontmatter as fm
    path = PLANS_DIR / f"{slug}.md"
    if not path.is_file():
        return
    post = fm.load(path)
    budgets = dict(post.metadata.get("budgets") or {})
    stop_budget = dict(budgets.get(city_slug) or {})
    current = 0.0
    try:
        current = float(stop_budget.get("activities") or 0)
    except (ValueError, TypeError):
        pass
    stop_budget["activities"] = round(current + amount, 2)
    budgets[city_slug] = stop_budget
    post.metadata["budgets"] = budgets
    with open(path, "w", encoding="utf-8") as fh:
        fm.dump(post, fh)


def _budget_remove_poi_price(slug, city_slug, poi_path):
    """If the POI has a parseable numeric price, subtract it from the stop's activities budget."""
    amount = _parse_poi_price(poi_path)
    if amount is None:
        return
    import frontmatter as fm
    path = PLANS_DIR / f"{slug}.md"
    if not path.is_file():
        return
    post = fm.load(path)
    budgets = dict(post.metadata.get("budgets") or {})
    stop_budget = dict(budgets.get(city_slug) or {})
    current = 0.0
    try:
        current = float(stop_budget.get("activities") or 0)
    except (ValueError, TypeError):
        pass
    new_val = round(max(0.0, current - amount), 2)
    if new_val == 0:
        stop_budget.pop("activities", None)
    else:
        stop_budget["activities"] = new_val
    budgets[city_slug] = stop_budget
    post.metadata["budgets"] = budgets
    with open(path, "w", encoding="utf-8") as fh:
        fm.dump(post, fh)


@_require_plan_auth
@csrf_exempt
def plan_budget_save(request, slug, city_slug):
    if request.method != "POST":
        raise Http404
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "invalid JSON"}, status=400)
    allowed = {"hotel", "food", "activities", "travel", "currency", "notes"}
    budget = {k: data[k] for k in allowed if k in data}
    _plan_save_budget(slug, city_slug, budget)
    total = sum(float(budget.get(k, 0) or 0) for k in ("hotel", "food", "activities", "travel"))
    return JsonResponse({"ok": True, "total": total})


@_require_plan_auth
def plan_edit(request, slug):
    path = PLANS_DIR / f"{slug}.md"
    if not path.is_file():
        raise Http404
    import frontmatter as fm
    if request.method == "POST":
        body = request.POST.get("body", "")
        post = fm.load(path)
        post.content = body
        with open(path, "w", encoding="utf-8") as fh:
            fm.dump(post, fh)
        return HttpResponseRedirect(f"/plans/{slug}/")
    post = fm.load(path)
    return render(request, "plans/plan_edit.html", {
        "plan": {"slug": slug, "title": post.metadata.get("title", slug)},
        "body": post.content,
        "passphrase": post.metadata.get("passphrase"),
    })


def _plan_file_add(slug, city_slug, poi_path):
    path = PLANS_DIR / f"{slug}.md"
    import frontmatter as fm
    post = fm.load(path)
    lines = post.content.splitlines()
    insert_at = None
    in_section = False
    for i, line in enumerate(lines):
        h2 = re.match(r"^##\s+(.+)$", line)
        if h2:
            heading = h2.group(1)
            city_raw = heading.split("|", 1)[0].strip()
            if "/" in city_raw:
                heading_slug = _slugify(city_raw.split("/")[-1].replace("_", " "))
            else:
                heading_slug = _slugify(city_raw)
            in_section = (heading_slug == city_slug)
            if in_section:
                insert_at = i + 1
            continue
        if in_section:
            if re.match(r"^[-*]\s+", line):
                insert_at = i + 1
            elif line.strip() == "":
                pass
            else:
                break
    if insert_at is None:
        # Stop doesn't exist yet — append a new section
        city_title = city_slug.replace("-", " ").title()
        lines.append(f"## {city_title}")
        lines.append(f"- {poi_path}")
        post.content = "\n".join(lines)
        with open(path, "w", encoding="utf-8") as fh:
            fm.dump(post, fh)
        return True
    if any(l.strip().lstrip("-* ") == poi_path for l in lines):
        return False
    lines.insert(insert_at, f"- {poi_path}")
    post.content = "\n".join(lines)
    with open(path, "w", encoding="utf-8") as fh:
        fm.dump(post, fh)
    return True


def _plan_file_remove(slug, poi_path):
    path = PLANS_DIR / f"{slug}.md"
    import frontmatter as fm
    post = fm.load(path)
    lines = post.content.splitlines()
    new_lines = [l for l in lines if l.strip().lstrip("-* ") != poi_path]
    if len(new_lines) == len(lines):
        return False
    post.content = "\n".join(new_lines)
    with open(path, "w", encoding="utf-8") as fh:
        fm.dump(post, fh)
    return True


@_require_plan_auth
def plan_poi_add(request, slug, city_slug=None):
    if request.method != "POST":
        raise Http404
    poi_path = request.POST.get("poi_path", "").strip()
    # Custom spot (from mini-form): prefer URL, fall back to title
    if not poi_path:
        custom_url = request.POST.get("custom_url", "").strip()
        custom_title = request.POST.get("custom_title", "").strip()
        custom_desc = request.POST.get("custom_desc", "").strip()
        if custom_title:
            # Create a draft POI file so title, description, and URL are stored together
            _poi_slug = re.sub(r"[\s_]+", "-", re.sub(r"[^\w\s-]", "", custom_title.lower()).strip()).strip("-") or "spot"
            _cs = city_slug or "unknown"
            draft_dir = PLANS_DIR / "pois" / slug / _cs
            draft_dir.mkdir(parents=True, exist_ok=True)
            import frontmatter as _fm
            _meta = {"title": custom_title, "type": "poi"}
            if custom_url and re.match(r"^https?://", custom_url):
                _meta["external_url"] = custom_url
            _post = _fm.Post(custom_desc, **_meta)
            (draft_dir / f"{_poi_slug}.md").write_text(_fm.dumps(_post))
            poi_path = f"~pois/{slug}/{_cs}/{_poi_slug}"
        elif custom_url and re.match(r"^https?://", custom_url):
            poi_path = custom_url
    if poi_path:
        if city_slug is None:
            plan = _parse_plan(PLANS_DIR / f"{slug}.md")
            if plan:
                for stop in plan["stops"]:
                    cp = stop.get("city_path")
                    if cp and poi_path.startswith(cp + "/"):
                        city_slug = stop["city_slug"]
                        break
                if city_slug is None:
                    for stop in plan["stops"]:
                        cs = stop["city_slug"].replace("-", "")
                        if cs in poi_path.replace("/", "").replace("_", "").lower():
                            city_slug = stop["city_slug"]
                            break
        if city_slug:
            _plan_file_add(slug, city_slug, poi_path)
            _budget_add_poi_price(slug, city_slug, poi_path)
    return HttpResponseRedirect(request.POST.get("next", f"/plans/{slug}/"))


@_require_plan_auth
def plan_note_edit(request, slug, city_slug):
    if request.method != "POST":
        raise Http404
    old_text = request.POST.get("old_text", "").strip()
    new_text = request.POST.get("new_text", "").strip()
    if old_text and new_text and old_text != new_text:
        import frontmatter as fm
        path = PLANS_DIR / f"{slug}.md"
        post = fm.load(path)
        lines = post.content.splitlines()
        new_lines = [
            re.sub(r"^([-*]\s+)" + re.escape(old_text) + r"$", r"\g<1>" + new_text, l)
            for l in lines
        ]
        post.content = "\n".join(new_lines)
        with open(path, "w", encoding="utf-8") as fh:
            fm.dump(post, fh)
    return HttpResponseRedirect(request.POST.get("next", f"/plans/{slug}/{city_slug}/"))


@_require_plan_auth
def plan_poi_remove(request, slug, city_slug):
    if request.method != "POST":
        raise Http404
    poi_path = request.POST.get("poi_path", "").strip()
    if poi_path:
        _plan_file_remove(slug, poi_path)
        _budget_remove_poi_price(slug, city_slug, poi_path)
    return HttpResponseRedirect(request.POST.get("next", f"/plans/{slug}/{city_slug}/"))


# ── Auth views ────────────────────────────────────────────────────────────────

def plan_login(request, slug):
    passwords = _load_passwords()
    if slug not in passwords:
        return HttpResponseRedirect("/plans/new/")
    error = None
    if request.method == "POST":
        pw = request.POST.get("password", "")
        if _check_password(pw, passwords[slug]):
            _mark_plan_authenticated(request, slug)
            next_url = request.GET.get("next", f"/plans/{slug}/")
            return HttpResponseRedirect(next_url)
        error = "Incorrect passphrase. Please try again."
    plan_title_str = _plan_title(slug)
    return render(request, "plans/plan_login.html", {
        "slug": slug,
        "plan_title": plan_title_str,
        "error": error,
    })


def plan_signup(request, slug):
    return render(request, "plans/plan_signup.html", {"slug": slug})


def plan_logout(request):
    request.session["authenticated_plans"] = []
    return HttpResponseRedirect("/plans/")


# ── MCP API endpoints ─────────────────────────────────────────────────────────

def _check_plan_auth(body: dict, plan_slug: str) -> bool:
    passphrase = body.get("passphrase", "")
    if not passphrase:
        return False
    passwords = _load_passwords()
    hashed = passwords.get(plan_slug)
    return bool(hashed and _check_password(passphrase, hashed))


def _resolve_stop(destination: str, start_date: str, end_date: str, notes: str, hint: str = "") -> dict:
    from datetime import date as _date
    dest = destination.strip()
    if "/" in dest:
        city_path = dest
        from world66_content.models import load_page as _load_page
        city_page = _load_page(dest)
        city_title = city_page.title if city_page else dest.split("/")[-1].replace("_", " ").title()
    else:
        city_path = resolve_location_name(dest, hint) or ""
        city_page = load_page(city_path) if city_path else None
        city_title = city_page.title if city_page else dest

    try:
        s = _date.fromisoformat(start_date)
        e = _date.fromisoformat(end_date) if end_date else s
        if s.month == e.month and s.year == e.year:
            date_str = f"{s.day}–{e.day} {s.strftime('%B %Y')}" if s != e else s.strftime("%-d %B %Y")
        else:
            date_str = f"{s.strftime('%-d %B')} – {e.strftime('%-d %B %Y')}"
    except ValueError:
        date_str = f"{start_date} – {end_date}" if end_date else start_date

    city_slug = _slugify(city_title)
    return {
        "city_title": city_title,
        "city_path":  city_path,
        "city_slug":  city_slug,
        "date_str":   date_str,
        "notes":      notes,
        "start_date": start_date,
    }


@csrf_exempt
@require_POST
def api_plan_open(request):
    """Look up an existing plan by passphrase and return its stops — for MCP/Claude use."""
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "invalid JSON"}, status=400)

    passphrase = (body.get("passphrase") or "").strip()
    if not passphrase:
        return JsonResponse({"error": "passphrase required"}, status=400)

    passwords = _load_passwords()
    slug = None
    for s, hashed in passwords.items():
        if _check_password(passphrase, hashed):
            slug = s
            break

    if not slug:
        return JsonResponse({"error": "no plan found with that passphrase"}, status=404)

    plan = _parse_plan(PLANS_DIR / f"{slug}.md")
    if not plan:
        return JsonResponse({"error": "plan file not found"}, status=404)

    base_url = request.build_absolute_uri("/").rstrip("/")
    cities = [
        {
            "city_title": s["city"],
            "city_path":  s.get("city_path", ""),
            "city_slug":  s["city_slug"],
        }
        for s in plan["stops"]
    ]
    return JsonResponse({
        "url":        f"{base_url}/plans/{slug}/",
        "slug":       slug,
        "title":      plan["title"],
        "passphrase": passphrase,
        "cities":     cities,
    })


@csrf_exempt
@require_POST
def api_plan_create(request):
    import frontmatter as fm
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "invalid JSON"}, status=400)

    raw_stops = body.get("stops") or []
    if not raw_stops:
        return JsonResponse({"error": "stops list is required"}, status=400)

    trip_hint = body.get("title", "").strip() + " " + " ".join(s.get("destination", "") for s in raw_stops)
    resolved = [_resolve_stop(
        s.get("destination", ""), s.get("start_date", ""),
        s.get("end_date", ""), s.get("notes", ""), trip_hint,
    ) for s in raw_stops]

    trip_title = body.get("title", "").strip() or (
        f"Trip to {', '.join(r['city_title'] for r in resolved)}"
    )
    first = resolved[0]
    base = re.sub(r"[^\w\s-]", "", first["city_title"].lower()).strip()
    base = re.sub(r"[\s_]+", "-", base)
    import secrets as _secrets
    slug = f"{base}-{first['start_date'][:7]}-{_secrets.token_hex(3)}"

    passphrase = _generate_passphrase()
    _save_password(slug, passphrase)

    PLANS_DIR.mkdir(exist_ok=True)

    used_slugs: dict = {}
    for r in resolved:
        b = r["city_slug"]
        if b not in used_slugs:
            used_slugs[b] = 1
        else:
            used_slugs[b] += 1
            r["city_slug"] = f"{b}-{used_slugs[b]}"

    content_lines = []
    for r in resolved:
        content_lines.append(f"## {r['city_title']} | {r['date_str']}")
        if r["notes"]:
            content_lines.append(f"- {r['notes']}")
        content_lines.append("")

    post = fm.Post("\n".join(content_lines), title=trip_title, passphrase=passphrase)
    (PLANS_DIR / f"{slug}.md").write_text(fm.dumps(post))

    base_url = request.build_absolute_uri("/").rstrip("/")
    return JsonResponse({
        "url":        f"{base_url}/plans/join/?next=/plans/{slug}/",
        "slug":       slug,
        "passphrase": passphrase,
        "cities":     [{"city_title": r["city_title"],
                        "city_path":  r["city_path"],
                        "city_slug":  r["city_slug"]} for r in resolved],
    })


@csrf_exempt
@require_POST
def api_plan_remove_poi(request):
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "invalid JSON"}, status=400)

    plan_slug = body.get("plan_slug", "").strip()
    poi_path = body.get("poi_path", "").strip()
    if not plan_slug or not poi_path:
        return JsonResponse({"error": "plan_slug and poi_path are required"}, status=400)
    if not _check_plan_auth(body, plan_slug):
        return JsonResponse({"error": "unauthorized"}, status=403)

    removed = _plan_file_remove(plan_slug, poi_path)
    return JsonResponse({"removed": removed})


@csrf_exempt
@require_POST
def api_plan_add_pois(request):
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "invalid JSON"}, status=400)

    plan_slug = body.get("plan_slug", "").strip()
    city_slug = body.get("city_slug", "").strip()
    if not plan_slug or not city_slug:
        return JsonResponse({"error": "plan_slug and city_slug are required"}, status=400)
    if not _check_plan_auth(body, plan_slug):
        return JsonResponse({"error": "unauthorized"}, status=403)

    added = 0
    for path in body.get("poi_paths", []):
        if isinstance(path, str) and path.strip():
            if _plan_file_add(plan_slug, city_slug, path.strip()):
                added += 1
    return JsonResponse({"added": added})


@csrf_exempt
@require_POST
def api_research_submit(request):
    import frontmatter as fm
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "invalid JSON"}, status=400)

    plan_slug  = body.get("plan_slug", "").strip()
    city_slug  = body.get("city_slug", "").strip()
    city_path  = body.get("city_path", "").strip().strip("/")
    city_title = body.get("city_title", "").strip()
    pois       = body.get("pois", [])
    intro      = body.get("intro", "").strip()

    if not isinstance(pois, list) or not city_title:
        return JsonResponse({"error": "city_title and pois are required"}, status=400)
    if plan_slug and not _check_plan_auth(body, plan_slug):
        return JsonResponse({"error": "unauthorized"}, status=403)

    if not city_path:
        city_path = re.sub(r"[^a-z0-9]+", "-", city_title.lower()).strip("-")

    # Write draft POIs to plans/pois/<plan_slug>/<city_path>/
    poi_prefix = f"{plan_slug}/{city_path}" if plan_slug else city_path
    city_dir = PLANS_DIR / "pois" / poi_prefix
    city_dir.mkdir(parents=True, exist_ok=True)

    # Save intro text
    if intro and plan_slug and city_slug:
        intro_dir = PLANS_DIR / "intros" / plan_slug
        intro_dir.mkdir(parents=True, exist_ok=True)
        (intro_dir / f"{city_slug}.md").write_text(intro)

    def _slugify(text):
        return re.sub(r"[\s_]+", "-", re.sub(r"[^\w\s-]", "", text.lower()).strip()).strip("-")

    written = 0
    draft_paths = []
    for poi in pois:
        name     = poi.get("name", "").strip()
        poi_body = poi.get("body", "").strip()
        if not name or not poi_body:
            continue
        slug = _slugify(name)
        out_path = city_dir / f"{slug}.md"
        meta = {"title": name, "type": "poi", "category": poi.get("category", "Landmark")}
        if poi.get("latitude") is not None:
            meta["latitude"]  = round(float(poi["latitude"]), 7)
        if poi.get("longitude") is not None:
            meta["longitude"] = round(float(poi["longitude"]), 7)
        post = fm.Post(poi_body, **meta)
        out_path.write_text(fm.dumps(post))
        draft_paths.append(f"~pois/{poi_prefix}/{slug}")
        written += 1

    if plan_slug and city_slug:
        for draft_path in draft_paths:
            _plan_file_add(plan_slug, city_slug, draft_path)

    return JsonResponse({"written": written, "city_path": city_path})


def api_search(request):
    from world66_content.models import CONTENT_DIR, _load_page_from_file
    q = request.GET.get("q", "").strip().lower()
    if not q or len(q) < 2:
        return JsonResponse({"results": []})
    results = []
    for md_file in sorted(CONTENT_DIR.rglob("*.md")):
        rel = str(md_file.relative_to(CONTENT_DIR).with_suffix(""))
        if q in rel.lower() or q in md_file.stem.lower():
            page = _load_page_from_file(md_file, rel)
            if page:
                results.append({
                    "title":     page.title,
                    "url_path":  rel,
                    "page_type": page.page_type,
                })
        if len(results) >= 20:
            break
    return JsonResponse({"results": results})


def _cors(response, request=None):
    response["Access-Control-Allow-Origin"] = "*"
    response["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response["Access-Control-Allow-Headers"] = "Content-Type"
    return response


@csrf_exempt
def api_plans_list(request):
    """List plans the caller can access, authenticated by passphrase(s).

    POST body:
      passphrases: {slug: passphrase, ...}  — known pairs stored by extension
      passphrase:  "word-word-word"          — discover flow (unknown slug)
    """
    if request.method == "OPTIONS":
        from django.http import HttpResponse as _HR
        return _cors(_HR(), request)

    import frontmatter as fm
    accessible = set()

    if request.method == "POST":
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {}

        for slug, phrase in (body.get("passphrases") or {}).items():
            if _check_plan_auth({"passphrase": phrase}, slug):
                accessible.add(slug)

        discover = (body.get("passphrase") or "").strip()
        if discover:
            passwords = _load_passwords()
            for slug, hashed in passwords.items():
                if _check_password(discover, hashed):
                    accessible.add(slug)

    plans = []
    for f in sorted(PLANS_DIR.glob("*.md")):
        if f.name.startswith(".") or f.stem not in accessible:
            continue
        try:
            post = fm.load(str(f))
            title = post.metadata.get("title", f.stem)
            plans.append({"slug": f.stem, "title": title})
        except Exception:
            pass

    return _cors(JsonResponse({"plans": plans}), request)


@csrf_exempt
def api_add_from_url(request):
    """Browser extension endpoint: fetch a URL, extract POIs via Claude, add to plan."""
    if request.method == "OPTIONS":
        from django.http import HttpResponse as _HR
        return _cors(_HR(), request)
    if request.method != "POST":
        return _cors(JsonResponse({"error": "POST required"}, status=405), request)
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _cors(JsonResponse({"error": "invalid JSON"}, status=400), request)

    url = body.get("url", "").strip()
    plan_slug = body.get("plan_slug", "").strip()
    page_content = body.get("page_content", "").strip()
    page_title = body.get("page_title", "").strip()

    if not url or not plan_slug:
        return _cors(JsonResponse({"error": "url and plan_slug required"}, status=400), request)
    if not _check_plan_auth(body, plan_slug):
        return _cors(JsonResponse({"error": "unauthorized"}, status=403), request)

    plan = _parse_plan(PLANS_DIR / f"{plan_slug}.md")
    if not plan:
        return _cors(JsonResponse({"error": "plan not found"}, status=404), request)

    # Fetch page content server-side if extension couldn't provide it
    if not page_content:
        import urllib.request as _urllib_req
        from html.parser import HTMLParser as _HTMLParser

        class _TextExtractor(_HTMLParser):
            def __init__(self):
                super().__init__()
                self._skip = False
                self.parts = []
            def handle_starttag(self, tag, attrs):
                if tag in ("script", "style", "nav", "header", "footer", "aside"):
                    self._skip = True
            def handle_endtag(self, tag):
                if tag in ("script", "style", "nav", "header", "footer", "aside"):
                    self._skip = False
            def handle_data(self, data):
                if not self._skip:
                    s = data.strip()
                    if s:
                        self.parts.append(s)

        try:
            req = _urllib_req.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with _urllib_req.urlopen(req, timeout=10) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
            p = _TextExtractor()
            p.feed(raw)
            page_content = "\n".join(p.parts)
        except Exception as e:
            return _cors(JsonResponse({"error": f"Could not fetch page: {e}"}, status=400), request)

    page_content = page_content[:10000]

    stops_lines = "\n".join(
        f"- city_slug={s['city_slug']!r}, city={s['city']!r}"
        for s in plan["stops"]
    )
    city_slugs = ", ".join(s["city_slug"] for s in plan["stops"])

    import anthropic as _anthropic
    client = _anthropic.Anthropic()

    system_prompt = f"""You are a travel assistant extracting places from webpages for a trip planner.

Trip: "{plan['title']}"
Stops:
{stops_lines}

Extract all relevant travel places (sights, restaurants, bars, activities, markets, museums) from the webpage.
Assign each to the best matching trip stop.

Respond ONLY with valid JSON — no explanation, no markdown:
{{"places": [{{"title": "...", "description": "1-2 sentences", "city_slug": "...", "category": "sight|restaurant|bar|activity|museum|market"}}]}}

Rules:
- city_slug must be exactly one of: {city_slugs}
- Only include places that clearly belong to a trip stop
- Maximum 10 places
- If nothing matches, return {{"places": []}}"""

    try:
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": f"Page title: {page_title}\nURL: {url}\n\nContent:\n{page_content}"}],
        )
        raw_text = msg.content[0].text.strip()
        m = re.search(r"\{.*\}", raw_text, re.DOTALL)
        if not m:
            return _cors(JsonResponse({"error": "AI returned no JSON"}, status=500), request)
        places = json.loads(m.group()).get("places", [])
    except Exception as e:
        return _cors(JsonResponse({"error": f"AI error: {e}"}, status=500), request)

    if not places:
        return _cors(JsonResponse({"added": [], "message": "No matching places found for your trip stops."}), request)

    def _slugify(text):
        return re.sub(r"[\s_]+", "-", re.sub(r"[^\w\s-]", "", text.lower()).strip()).strip("-")

    import frontmatter as fm
    added = []
    for place in places:
        title = (place.get("title") or "").strip()
        description = (place.get("description") or "").strip()
        city_slug = (place.get("city_slug") or "").strip()
        category = (place.get("category") or "Landmark").strip().title()

        if not title or not city_slug:
            continue
        stop = next((s for s in plan["stops"] if s["city_slug"] == city_slug), None)
        if not stop:
            continue

        poi_slug = _slugify(title) or "spot"
        draft_dir = PLANS_DIR / "pois" / plan_slug / city_slug
        draft_dir.mkdir(parents=True, exist_ok=True)

        post = fm.Post(description, title=title, type="poi", category=category, source_url=url)
        (draft_dir / f"{poi_slug}.md").write_text(fm.dumps(post))

        draft_path = f"~pois/{plan_slug}/{city_slug}/{poi_slug}"
        _plan_file_add(plan_slug, city_slug, draft_path)
        added.append({"title": title, "city": stop["city"], "city_slug": city_slug})

    return _cors(JsonResponse({"added": added}), request)
