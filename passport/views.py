import hashlib
import json
import random
import secrets
import sqlite3
import struct
from collections import defaultdict
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Optional

from django.conf import settings
from django.http import Http404
from django.shortcuts import redirect, render

from .wordlist import generate_passphrase

PASSPORT_DIR = Path(settings.BASE_DIR) / "passports"
PASSWORDS_FILE = PASSPORT_DIR / ".passwords.json"
SEARCH_DB = Path(settings.BASE_DIR) / "search.db"


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _hash_password(password: str, salt: Optional[str] = None) -> str:
    if salt is None:
        salt = secrets.token_hex(16)
    h = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    return f"{salt}:{h}"


def _check_password(password: str, stored: str) -> bool:
    parts = stored.split(":", 1)
    if len(parts) != 2:
        return False
    salt = parts[0]
    return secrets.compare_digest(_hash_password(password, salt), stored)


def _load_passwords() -> dict:
    if not PASSWORDS_FILE.is_file():
        return {}
    return json.loads(PASSWORDS_FILE.read_text())


def _save_password(slug: str, password: str) -> None:
    PASSPORT_DIR.mkdir(parents=True, exist_ok=True)
    data = _load_passwords()
    data[slug] = _hash_password(password)
    PASSWORDS_FILE.write_text(json.dumps(data))


def _passport_authenticated(request, slug: str) -> bool:
    return slug in request.session.get("authenticated_passports", [])


def _mark_authenticated(request, slug: str) -> None:
    current = list(request.session.get("authenticated_passports", []))
    if slug not in current:
        current.append(slug)
        request.session["authenticated_passports"] = current


def _require_auth(view_fn):
    @wraps(view_fn)
    def wrapper(request, slug, *args, **kwargs):
        if not _passport_path(slug).is_file():
            current = list(request.session.get("authenticated_passports", []))
            if slug in current:
                request.session["authenticated_passports"] = [s for s in current if s != slug]
            return redirect("/passport/new")
        passwords = _load_passwords()
        if slug not in passwords:
            return redirect(f"/passport/{slug}/protect")
        if not _passport_authenticated(request, slug):
            return redirect(f"/passport/{slug}/login/?next={request.path}")
        return view_fn(request, slug, *args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Passport data helpers
# ---------------------------------------------------------------------------

def _passport_path(slug: str) -> Path:
    return PASSPORT_DIR / slug / "passport.json"


def _load_passport(slug: str) -> Optional[dict]:
    path = _passport_path(slug)
    if not path.is_file():
        return None
    return json.loads(path.read_text())


def _save_passport(data: dict) -> None:
    slug = data["slug"]
    path = _passport_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def _new_passport(title: str) -> dict:
    slug = secrets.token_hex(5)
    return {
        "slug": slug,
        "title": title or "My Travel Passport",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "step": 0,
        "liked_pois": [],
    }


# ---------------------------------------------------------------------------
# Session helper (used by plans to personalise suggestions)
# ---------------------------------------------------------------------------

def get_session_passport(request):
    """Return the passport dict for the first authenticated passport in session, or None."""
    for slug in request.session.get("authenticated_passports", []):
        p = _load_passport(slug)
        if p:
            return p
    return None


# ---------------------------------------------------------------------------
# Traveler profile
# ---------------------------------------------------------------------------

_PROFILE_DEFS = [
    {
        "id": "sights", "emoji": "🏛️", "label": "History & Landmarks",
        "intro": "a history buff and landmark hunter",
        "follow": "You plan around the iconic sites but always find something surprising off the main route.",
        "predictions": ["UNESCO heritage sites", "ancient ruins", "royal palaces", "historic old towns"],
        "keywords": ["museum", "palace", "castle", "cathedral", "church", "basilica",
                     "temple", "monument", "tower", "ruin", "fort", "historic", "shrine"],
    },
    {
        "id": "food", "emoji": "🍜", "label": "Food & Cuisine",
        "intro": "a culinary explorer who eats their way through every city",
        "follow": "A trip isn't complete until you've found the neighbourhood spot that locals actually go to.",
        "predictions": ["neighbourhood trattorias", "street food markets", "local food halls", "morning bakeries"],
        "keywords": ["restaurant", "cafe", "bistro", "food", "kitchen", "dining",
                     "gastro", "terrace", "brasserie", "trattoria", "taverna"],
    },
    {
        "id": "bars", "emoji": "🍸", "label": "Bars & Drinks",
        "intro": "someone who finds the best local watering holes",
        "follow": "You know that the best conversations happen over a well-made drink in the right place.",
        "predictions": ["craft cocktail bars", "local wine bars", "legendary pubs", "hidden speakeasies"],
        "keywords": ["/bar", "_bar", "pub", "cocktail", "wine", "beer", "brewery",
                     "tavern", "gin", "whisky", "speakeasy"],
    },
    {
        "id": "nightlife", "emoji": "🌙", "label": "Nightlife",
        "intro": "a night owl who lives for the city after dark",
        "follow": "You've learned that cities reveal their real character long after midnight.",
        "predictions": ["late-night jazz clubs", "rooftop bars", "underground clubs", "live music venues"],
        "keywords": ["club", "nightlife", "jazz", "dance", "lounge", "cabaret", "disco"],
    },
    {
        "id": "art", "emoji": "🎨", "label": "Art & Culture",
        "intro": "drawn to galleries, theaters, and the creative pulse of a city",
        "follow": "You believe a city's soul is found in its art spaces, not its souvenir shops.",
        "predictions": ["contemporary art galleries", "historic opera houses", "public sculpture trails", "street art districts"],
        "keywords": ["gallery", "theater", "theatre", "opera", "cinema", "exhibition",
                     "mural", "sculpture", "contemporary"],
    },
    {
        "id": "nature", "emoji": "🌿", "label": "Nature & Outdoors",
        "intro": "happiest outdoors, whether on a coastal walk or a city park",
        "follow": "You recharge in parks, waterfronts, and botanical gardens rather than shopping malls.",
        "predictions": ["coastal hiking trails", "botanical gardens", "riverside walks", "city parks"],
        "keywords": ["park", "garden", "beach", "river", "lake", "forest", "botanical",
                     "waterfall", "bay", "island", "promenade"],
    },
    {
        "id": "markets", "emoji": "🛒", "label": "Markets & Local Life",
        "intro": "a market wanderer who loves the chaos and character of local trade",
        "follow": "Markets show you a city's real character — the smells, the noise, the unexpected finds.",
        "predictions": ["morning produce markets", "antique flea markets", "artisan bazaars", "local street fairs"],
        "keywords": ["market", "souk", "bazar", "bazaar", "flea", "antique"],
    },
]


def _poi_categories(poi: dict) -> list[str]:
    text = ((poi.get("url_path") or "") + " " + (poi.get("title") or "")).lower()
    return [p["id"] for p in _PROFILE_DEFS if any(k in text for k in p["keywords"])]


def _compute_traveler_profile(liked_pois: list, skipped_pois: Optional[list] = None) -> dict:
    n = len(liked_pois)
    accuracy = round(92 * n / (n + 8)) if n > 0 else 0
    skipped_pois = skipped_pois or []

    likes: dict[str, int] = {p["id"]: 0 for p in _PROFILE_DEFS}
    seen: dict[str, int]  = {p["id"]: 0 for p in _PROFILE_DEFS}
    for poi in liked_pois:
        for cat in _poi_categories(poi):
            likes[cat] += 1
            seen[cat]  += 1
    for poi in skipped_pois:
        for cat in _poi_categories(poi):
            seen[cat] += 1

    # ratio score: like-rate within each category
    scores = {pid: (likes[pid] / seen[pid] if seen[pid] else 0.0) for pid in likes}

    total = sum(scores.values()) or 1
    ranked = sorted(
        [{
            "id": p["id"], "label": p["label"], "emoji": p["emoji"],
            "score": scores[p["id"]], "pct": round(scores[p["id"]] / total * 100),
            "predictions": p["predictions"],
        } for p in _PROFILE_DEFS],
        key=lambda x: x["score"], reverse=True,
    )

    active = [r for r in ranked if r["score"] > 0]
    if not active:
        profile_text = "Keep swiping to build your travel profile."
        top_emoji, top_label = "✈️", "Traveler"
    else:
        top = active[0]
        top_def = next(p for p in _PROFILE_DEFS if p["id"] == top["id"])
        top_emoji, top_label = top["emoji"], top["label"]
        second = next((r for r in active[1:] if r["pct"] >= 20), None)
        if second:
            second_label = next(p["label"] for p in _PROFILE_DEFS if p["id"] == second["id"])
            profile_text = (
                f"You're {top_def['intro']}, with a side passion for "
                f"{second_label.lower()}. {top_def['follow']}"
            )
        else:
            profile_text = f"You're {top_def['intro']}. {top_def['follow']}"

    cities: dict[str, int] = {}
    for poi in liked_pois:
        city = poi.get("city", "")
        if city:
            cities[city] = cities.get(city, 0) + 1

    predictions = []
    for r in active[:2]:
        predictions.extend(next(p["predictions"] for p in _PROFILE_DEFS if p["id"] == r["id"]))
    predictions = predictions[:6]

    return {
        "accuracy": accuracy,
        "ranked": ranked,
        "active": active,
        "profile_text": profile_text,
        "top_emoji": top_emoji,
        "top_label": top_label,
        "cities": sorted(cities.items(), key=lambda x: x[1], reverse=True),
        "n_liked": n,
        "predictions": predictions,
    }


# ---------------------------------------------------------------------------
# POI swipe helpers
# ---------------------------------------------------------------------------

_POI_CATS = [
    ("sights",     ["museum", "palace", "castle", "cathedral", "church", "basilica",
                    "temple", "monument", "tower", "ruin", "fort", "historic", "shrine"]),
    ("food",       ["restaurant", "cafe", "bistro", "food", "kitchen", "dining",
                    "gastro", "terrace", "brasserie", "trattoria", "taverna"]),
    ("bars",       ["/bar", "_bar", "pub", "cocktail", "wine", "beer", "brewery",
                    "tavern", "gin", "whisky", "speakeasy"]),
    ("nightlife",  ["club", "nightlife", "jazz", "dance", "lounge", "cabaret", "disco"]),
    ("art",        ["gallery", "theater", "theatre", "opera", "cinema", "exhibition",
                    "mural", "sculpture", "contemporary"]),
    ("nature",     ["park", "garden", "beach", "river", "lake", "forest", "botanical",
                    "waterfall", "bay", "island", "promenade"]),
    ("markets",    ["market", "souk", "bazar", "bazaar", "flea", "antique", "mall"]),
]


def _categorize_poi(url_path: str, title: str) -> str:
    text = (url_path + " " + (title or "")).lower()
    for name, keys in _POI_CATS:
        if any(k in text for k in keys):
            return name
    return "other"


def _diverse_pick(pois: list[dict], n: int) -> list[dict]:
    """Round-robin across categories to get a varied mix."""
    by_cat: dict = defaultdict(list)
    for p in pois:
        by_cat[_categorize_poi(p["url_path"], p["title"])].append(p)
    buckets = list(by_cat.values())
    random.shuffle(buckets)
    result: list[dict] = []
    i = 0
    while len(result) < n and buckets:
        b = buckets[i % len(buckets)]
        if b:
            result.append(b.pop(0))
        if not b:
            buckets.pop(i % len(buckets))
            if not buckets:
                break
            i -= 1
        i += 1
    return result


def _load_swipe_poi_groups(
    n_cities: int = 8, pois_per_city: int = 6, excluded_paths: Optional[set] = None
) -> list[dict]:
    """Return POI groups for the swipe UI.

    Cities are ranked by score so only well-known destinations appear.
    POIs within each city are selected for category diversity.
    """
    if not SEARCH_DB.is_file():
        return []
    try:
        conn = sqlite3.connect(f"file:{SEARCH_DB}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row

        # Top-scoring cities (not regions/neighbourhoods) that have a hero image
        city_rows = conn.execute(
            """SELECT url_path, title, image
               FROM geo
               WHERE page_type = 'location'
               AND loc_type = 'city'
               AND image != ''
               ORDER BY COALESCE(score, 0) DESC
               LIMIT 80"""
        ).fetchall()

        cities = [dict(r) for r in city_rows]
        if excluded_paths:
            cities = [c for c in cities if c["url_path"] not in excluded_paths]

        # Shuffle within the top pool so each session feels fresh
        pool_size = min(len(cities), max(n_cities * 4, 32))
        pool = cities[:pool_size]
        random.shuffle(pool)

        groups: list[dict] = []
        for city in pool:
            if len(groups) >= n_cities:
                break
            city_path = city["url_path"]

            poi_rows = conn.execute(
                """SELECT d.url_path, d.title, d.body
                   FROM geo g
                   JOIN docs d ON d.url_path = g.url_path
                   WHERE g.page_type = 'poi'
                   AND g.lat IS NOT NULL AND g.lng IS NOT NULL
                   AND d.url_path LIKE ? || '/%'
                   AND length(d.body) > 80
                   ORDER BY COALESCE(g.score, 0) DESC""",
                (city_path,),
            ).fetchall()

            if len(poi_rows) < 3:
                continue

            pois = []
            for row in poi_rows:
                snippet = (row["body"] or "").strip()
                end = snippet.find(". ")
                snippet = snippet[: end + 1] if end > 0 else snippet
                pois.append({
                    "url_path": row["url_path"],
                    "title": row["title"],
                    "snippet": snippet[:220],
                })

            selected = _diverse_pick(pois, pois_per_city)
            if len(selected) < 2:
                continue

            random.shuffle(selected)
            groups.append({
                "city": city["title"],
                "city_path": city_path,
                "image_url": f"/content-image/{city_path.rsplit('/', 1)[0]}/{city['image']}",
                "pois": selected,
            })

        conn.close()
    except Exception:
        return []

    return groups


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

def passport_list(request):
    known_slugs = request.session.get("authenticated_passports", [])
    passports = []
    for slug in known_slugs:
        data = _load_passport(slug)
        if data:
            passports.append(data)
    if len(passports) == 1:
        return redirect(f"/passport/{passports[0]['slug']}")
    return render(request, "passport/list.html", {"passports": passports})


def passport_new(request):
    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        data = _new_passport(title)
        _save_passport(data)
        return redirect(f"/passport/{data['slug']}/protect")
    return render(request, "passport/new.html")


def passport_protect(request, slug):
    if not _passport_path(slug).is_file():
        return redirect("/passport/new")
    passwords = _load_passwords()
    if slug in passwords:
        return redirect(f"/passport/{slug}/login")

    session_key = f"passport_phrase_{slug}"

    if request.method == "POST":
        phrase = request.session.get(session_key, "")
        if not phrase:
            return redirect(f"/passport/{slug}/protect")
        _save_password(slug, phrase)
        del request.session[session_key]
        _mark_authenticated(request, slug)
        return redirect(f"/passport/{slug}/swipe")

    phrase = generate_passphrase(4)
    request.session[session_key] = phrase
    passport = _load_passport(slug)
    return render(request, "passport/protect.html", {
        "passport": passport,
        "phrase": phrase,
    })


def passport_login(request, slug):
    if not _passport_path(slug).is_file():
        return redirect("/passport/new")
    passwords = _load_passwords()
    if slug not in passwords:
        return redirect(f"/passport/{slug}/protect")
    next_url = request.GET.get("next", f"/passport/{slug}")
    error = None
    if request.method == "POST":
        next_url = request.POST.get("next", next_url)
        phrase = request.POST.get("phrase", "")
        if _check_password(phrase, passwords[slug]):
            _mark_authenticated(request, slug)
            return redirect(next_url)
        error = "Wrong passphrase."
    passport = _load_passport(slug)
    return render(request, "passport/login.html", {
        "passport": passport,
        "error": error,
        "next": next_url,
    })


@_require_auth
def passport_swipe(request, slug):
    passport = _load_passport(slug)
    if not passport:
        raise Http404

    if request.method == "POST":
        try:
            liked = json.loads(request.POST.get("liked", "[]"))
        except (ValueError, TypeError):
            liked = []
        try:
            skipped = json.loads(request.POST.get("skipped", "[]"))
        except (ValueError, TypeError):
            skipped = []
        passport["liked_pois"] = liked
        passport["skipped_pois"] = skipped
        passport["step"] = max(passport.get("step", 0), 1)
        _save_passport(passport)
        return redirect(f"/passport/{slug}")

    if request.GET.get("redo"):
        passport["liked_pois"] = []
        passport["step"] = 0
        _save_passport(passport)
        return redirect(f"/passport/{slug}/swipe")

    existing_liked = passport.get("liked_pois", [])
    # Load fresh cities, excluding city_paths already represented in existing likes
    excluded = {p.get("city_path", "") for p in existing_liked if p.get("city_path")}
    groups = _load_swipe_poi_groups(n_cities=30, pois_per_city=9, excluded_paths=excluded if excluded else None)
    return render(request, "passport/swipe.html", {
        "passport": passport,
        "groups_json": json.dumps(groups),
        "existing_liked_json": json.dumps(existing_liked),
    })


@_require_auth
def passport_swipe_autosave(request, slug):
    from django.http import JsonResponse
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    passport = _load_passport(slug)
    if not passport:
        return JsonResponse({"error": "not found"}, status=404)
    try:
        liked = json.loads(request.POST.get("liked", "[]"))
    except (ValueError, TypeError):
        liked = []
    try:
        skipped = json.loads(request.POST.get("skipped", "[]"))
    except (ValueError, TypeError):
        skipped = []
    passport["liked_pois"] = liked
    passport["skipped_pois"] = skipped
    if liked:
        passport["step"] = max(passport.get("step", 0), 1)
    _save_passport(passport)
    return JsonResponse({"ok": True, "count": len(liked), "skipped": len(skipped)})


def _embedding_recommendations(liked_pois: list[dict], k: int = 3) -> list[dict]:
    """Return POIs most similar to the centroid of liked POI embeddings."""
    if not liked_pois or not SEARCH_DB.is_file():
        return []
    try:
        import apsw
        import sqlite_vec
    except ImportError:
        return []

    try:
        conn = apsw.Connection(str(SEARCH_DB), flags=apsw.SQLITE_OPEN_READONLY)
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        paths = [p["url_path"] + ".md" for p in liked_pois if p.get("url_path")]
        ph = ",".join("?" * len(paths))
        emb_rows = conn.execute(
            f"SELECT embedding FROM embeddings WHERE path IN ({ph})", paths
        ).fetchall()

        if not emb_rows:
            conn.close()
            return []

        dims = len(emb_rows[0][0]) // 4
        centroid = [0.0] * dims
        for (blob,) in emb_rows:
            vals = struct.unpack(f"{dims}f", blob)
            for i, v in enumerate(vals):
                centroid[i] += v
        n = len(emb_rows)
        centroid = [v / n for v in centroid]
        mag = sum(v * v for v in centroid) ** 0.5
        if mag > 0:
            centroid = [v / mag for v in centroid]
        centroid_blob = struct.pack(f"{dims}f", *centroid)

        already = set(paths)
        knn_rows = conn.execute(
            "SELECT path, distance FROM embeddings WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (centroid_blob, min(4096, k * 30)),
        ).fetchall()

        candidates = [p for p, _ in knn_rows if p not in already][:k * 10]
        if not candidates:
            conn.close()
            return []

        dist = {p.replace(".md", ""): d for p, d in knn_rows}
        cph = ",".join("?" * len(candidates))
        meta = conn.execute(
            f"""SELECT d.url_path, d.title, d.body, g.image
                FROM docs d LEFT JOIN geo g ON g.url_path = d.url_path
                WHERE d.path IN ({cph}) AND d.page_type = 'poi'""",
            candidates,
        ).fetchall()
        conn.close()

        meta.sort(key=lambda r: dist.get(r[0], 999))
        results = []
        for url_path, title, body, image in meta[:k]:
            snippet = (body or "").strip()
            end = snippet.find(". ")
            snippet = snippet[:end + 1] if end > 0 else snippet[:200]
            parts = url_path.split("/")
            city_slug = parts[-2] if len(parts) >= 2 else ""
            city = city_slug.replace("_", " ").title()
            results.append({
                "url_path": url_path,
                "title": title,
                "snippet": snippet,
                "city": city,
            })
        return results
    except Exception:
        return []


def _fallback_city_image() -> Optional[str]:
    """Return a cover image URL from the top-scoring city that has one."""
    if not SEARCH_DB.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{SEARCH_DB}?mode=ro", uri=True)
        row = conn.execute(
            "SELECT url_path, image FROM geo WHERE page_type='location' AND loc_type='city'"
            " AND image != '' ORDER BY COALESCE(score,0) DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row:
            parent = row[0].rsplit("/", 1)[0]
            return f"/content-image/{parent}/{row[1]}"
    except Exception:
        pass
    return None


@_require_auth
def passport_recommendations(request, slug):
    from django.http import JsonResponse
    passport = _load_passport(slug)
    if not passport:
        return JsonResponse({"error": "not found"}, status=404)
    liked_pois = passport.get("liked_pois", [])
    recs = _embedding_recommendations(liked_pois, k=3) if liked_pois else []
    return JsonResponse({"recommendations": recs})


@_require_auth
def passport_detail(request, slug):
    passport = _load_passport(slug)
    if not passport:
        raise Http404

    liked_pois = passport.get("liked_pois", [])
    seen: set = set()
    city_images: list = []
    for poi in liked_pois:
        img = poi.get("image_url", "")
        if img and img not in seen:
            seen.add(img)
            city_images.append(img)

    # Always have a cover image — fall back to top city if nothing liked yet
    cover_image_url = city_images[0] if city_images else _fallback_city_image()
    profile_image_url = (city_images[1] if len(city_images) > 1
                         else city_images[0] if city_images
                         else cover_image_url)

    skipped_pois = passport.get("skipped_pois", [])
    profile = _compute_traveler_profile(liked_pois, skipped_pois)
    similar_pois = _embedding_recommendations(liked_pois, k=3) if liked_pois else []

    return render(request, "passport/detail.html", {
        "passport": passport,
        "liked_pois": liked_pois,
        "cover_image_url": cover_image_url,
        "profile_image_url": profile_image_url,
        "step": passport.get("step", 0),
        "profile": profile,
        "similar_pois": similar_pois,
        "w66_url": settings.WORLD66_SITE_URL.rstrip("/"),
    })
