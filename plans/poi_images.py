"""On-the-fly inspiration photos for POIs that have no image in world66.

Looked up on Wikipedia/Wikidata and only cached locally (plans/.poi-images.json,
{poi_path: thumbnail url or null}) — hotlinked, never written to world66.
"""
import json
import re
import threading
import urllib.parse
import urllib.request
from pathlib import Path

from django.conf import settings

_CACHE_FILE = Path(settings.BASE_DIR) / "plans" / ".poi-images.json"
_lock = threading.Lock()
_HEADERS = {"User-Agent": "Tabbi/1.0 (https://tab.bi; trip planning inspiration)"}
_THUMB = 640
# Wikipedia searches per request: each is one API call, the rest waits for the next page view
SEARCH_BUDGET = 12


def _load():
    try:
        return json.loads(_CACHE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _save(cache):
    _CACHE_FILE.write_text(json.dumps(cache, indent=1, ensure_ascii=False))


def load_cache():
    """{poi_path: url or None} for every POI looked up so far."""
    return _load()


def _api(host, params):
    params = {"format": "json", "formatversion": 2, **params}
    req = urllib.request.Request(f"https://{host}/w/api.php?{urllib.parse.urlencode(params)}", headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.load(r)


def _words(text):
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}


def _by_wikidata(pages):
    """{poi_path: url} via Wikidata P18 for pages with a wikidata id."""
    ids = {p.meta["wikidata"]: p.path for p in pages if re.match(r"^Q\d+$", str(p.meta.get("wikidata", "")))}
    found = {}
    id_list = list(ids)
    for i in range(0, len(id_list), 50):
        data = _api("www.wikidata.org", {"action": "wbgetentities", "ids": "|".join(id_list[i:i + 50]),
                                         "props": "claims"})
        for qid, entity in data.get("entities", {}).items():
            claims = entity.get("claims", {}).get("P18") or []
            if claims:
                filename = claims[0]["mainsnak"]["datavalue"]["value"]
                found[ids[qid]] = (f"https://commons.wikimedia.org/wiki/Special:FilePath/"
                                   f"{urllib.parse.quote(filename)}?width={_THUMB}")
    return found


def _by_title(pages):
    """{poi_path: url} via the Wikipedia article with exactly the POI's title."""
    by_title = {p.title: p.path for p in pages}
    titles = list(by_title)
    found = {}
    for i in range(0, len(titles), 50):
        q = _api("en.wikipedia.org", {"action": "query", "prop": "pageimages", "piprop": "thumbnail",
                                      "pithumbsize": _THUMB, "redirects": 1,
                                      "titles": "|".join(titles[i:i + 50])})["query"]
        # Map normalized/redirected titles back to what we asked for
        back = {t: t for t in titles[i:i + 50]}
        for hop in q.get("normalized", []) + q.get("redirects", []):
            for orig, cur in list(back.items()):
                if cur == hop["from"]:
                    back[orig] = hop["to"]
        thumbs = {p["title"]: p.get("thumbnail", {}).get("source") for p in q.get("pages", [])}
        for orig, final in back.items():
            if thumbs.get(final):
                found[by_title[orig]] = thumbs[final]
    return found


def _by_search(page, city):
    """Photo of a top Wikipedia search hit for the POI, if it plausibly is the same place."""
    query = page.title if _words(city) <= _words(page.title) else f"{page.title} {city}"
    q = _api("en.wikipedia.org", {"action": "query", "generator": "search", "gsrsearch": query,
                                  "gsrlimit": 3, "prop": "pageimages", "piprop": "thumbnail",
                                  "pithumbsize": _THUMB}).get("query", {})
    hits = sorted((h for h in q.get("pages", []) if h.get("thumbnail")), key=lambda h: h.get("index", 0))
    # Exact title (any case) first, else the first hit sharing a word that isn't the city's name
    own_words = _words(page.title) - _words(city)
    for match in (lambda h: h["title"].casefold() == page.title.casefold(),
                  lambda h: _words(h["title"]) & own_words):
        for hit in hits:
            if match(hit):
                return hit["thumbnail"]["source"]
    return None


def fetch_missing(pages, city=""):
    """Look up photos for pages not in the cache yet. Returns {poi_path: url or None} for all pages."""
    with _lock:
        cache = _load()
        todo = [p for p in pages if p.path not in cache]
        if todo:
            found = {}
            try:
                found.update(_by_wikidata(todo))
                found.update(_by_title([p for p in todo if p.path not in found]))
                searches = 0
                for p in todo:
                    if p.path in found:
                        continue
                    if searches >= SEARCH_BUDGET:
                        break
                    searches += 1
                    found[p.path] = _by_search(p, city)
            except Exception:
                pass  # network trouble: keep what we have, retry the rest next time
            cache.update(found)
            _save(cache)
        return {p.path: cache.get(p.path) for p in pages}
