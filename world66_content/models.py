"""
World66 content loading module for Tabbi.

Reads markdown files with YAML frontmatter from the world66 content directory.
Set WORLD66_CONTENT_DIR in Django settings (Path object) to point at the
cloned world66/content/ directory.
"""

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import frontmatter
from django.conf import settings

CONTENT_DIR = Path(getattr(settings, "WORLD66_CONTENT_DIR", Path(settings.BASE_DIR) / "content"))
TABBI_CONTENT_DIR = Path(getattr(settings, "TABBI_CONTENT_DIR", "")) if getattr(settings, "TABBI_CONTENT_DIR", None) else None

NAV_TYPES = {"section", "section_group", "neighbourhood", "theme"}

DISPLAY_PROPERTIES = {
    "address": "Address",
    "phone": "Phone",
    "url": "Website",
    "email": "Email",
    "opening_hours": "Opening Hours",
    "closing_time": "Closing Time",
    "price": "Price",
    "duration": "Duration",
    "admission": "Admission",
    "isbn": "ISBN",
    "author": "Author",
    "connections": "Connections",
    "getting_there": "Getting There",
    "accessibility": "Accessibility",
    "zipcode": "Zip Code",
    "price_per_night": "Price/Night",
    "booking_url": "Book",
}


def _load_md(path):
    if not path.is_file():
        return None
    try:
        post = frontmatter.load(path)
    except Exception:
        return None
    return post.metadata, post.content


@dataclass
class Page:
    slug: str
    path: str
    title: str = ""
    page_type: str = "location"
    body: str = ""
    meta: dict = field(default_factory=dict)

    def get_absolute_url(self):
        return f"/{self.path}"

    @property
    def properties(self):
        return {
            DISPLAY_PROPERTIES[k]: v
            for k, v in self.meta.items()
            if k in DISPLAY_PROPERTIES
        }

    @property
    def tags(self):
        raw = self.meta.get("tags", [])
        if isinstance(raw, list):
            return raw
        if isinstance(raw, str):
            return [t.strip() for t in raw.split(",") if t.strip()]
        return []

    _CATEGORY_TAGS = {"sight", "museum", "architecture", "neighbourhood", "restaurant", "bar", "market"}

    @property
    def category(self):
        explicit = self.meta.get("category", "")
        if explicit:
            return explicit
        for t in self.tags:
            if t in self._CATEGORY_TAGS:
                return t.replace("_", " ").title()
        return ""

    @property
    def nav_tag(self):
        return self.meta.get("tag", self.slug)

    def breadcrumbs(self):
        crumbs = []
        parts = self.path.split("/")
        for i in range(len(parts)):
            ancestor_path = "/".join(parts[: i + 1])
            ancestor = load_page(ancestor_path)
            if ancestor:
                crumbs.append((ancestor.title, ancestor.path))
            else:
                crumbs.append((parts[i], ancestor_path))
        return crumbs

    def children(self):
        dir_path = CONTENT_DIR / self.path
        if not dir_path.is_dir():
            return [], [], []

        nav_pages = []
        locations = []
        pois = []

        for entry in sorted(dir_path.iterdir()):
            if entry.is_file() and entry.suffix == ".md":
                if entry.stem == self.slug:
                    continue
                if (dir_path / entry.stem).is_dir():
                    continue
                page = _load_page_from_file(entry, self.path + "/" + entry.stem)
                if not page:
                    continue
                if page.page_type in NAV_TYPES:
                    nav_pages.append(page)
                elif page.page_type == "poi":
                    pois.append(page)
                else:
                    locations.append(page)

            elif entry.is_dir():
                child = load_page(self.path + "/" + entry.name)
                if child:
                    if child.page_type == "location":
                        locations.append(child)
                    elif child.page_type in NAV_TYPES:
                        nav_pages.append(child)

        return nav_pages, locations, pois

    def tagged_pois(self, _city_tag_index=None):
        city_path = _find_city_path(self.path)
        if not city_path:
            return []
        tag = self.nav_tag
        by_tag = find_tagged_pois(city_path, tag, _city_tag_index=_city_tag_index)
        legacy = self._legacy_dir_pois()
        seen = {p.path for p in by_tag}
        for p in legacy:
            if p.path not in seen:
                by_tag.append(p)
        return by_tag

    def _legacy_dir_pois(self):
        pois = []
        seen = set()
        roots = [CONTENT_DIR] + ([TABBI_CONTENT_DIR] if TABBI_CONTENT_DIR else [])
        for root in roots:
            dir_path = root / self.path
            if not dir_path.is_dir() and "/" in self.path:
                dir_path = root / self.path.rsplit("/", 1)[0] / self.slug
            if not dir_path.is_dir():
                continue
            for entry in sorted(dir_path.iterdir()):
                if entry.is_file() and entry.suffix == ".md":
                    page = _load_page_from_file(entry, self.path + "/" + entry.stem)
                    if page and page.page_type == "poi" and page.path not in seen:
                        seen.add(page.path)
                        pois.append(page)
        return pois

    def pois(self):
        return self.tagged_pois()


def _find_city_path(path):
    parts = path.split("/")
    for i in range(len(parts) - 1, 0, -1):
        candidate = "/".join(parts[:i])
        page = load_page(candidate)
        if page and page.page_type == "location":
            return candidate
    return None


def build_city_tag_index(city_path):
    index = {}
    seen = set()
    dirs_to_scan = [(CONTENT_DIR, CONTENT_DIR / city_path)]
    if TABBI_CONTENT_DIR:
        dirs_to_scan.append((TABBI_CONTENT_DIR, TABBI_CONTENT_DIR / city_path))
    for root_dir, city_dir in dirs_to_scan:
        if not city_dir.is_dir():
            continue
        for md_file in sorted(city_dir.rglob("*.md")):
            result = _load_md(md_file)
            if not result:
                continue
            meta, _ = result
            if meta.get("type") not in ("poi", "neighbourhood", "theme"):
                continue
            raw_tags = meta.get("tags", [])
            if isinstance(raw_tags, str):
                raw_tags = [t.strip() for t in raw_tags.split(",")]
            if not raw_tags:
                continue
            rel = md_file.relative_to(root_dir)
            parts = list(rel.parts)
            stem = parts[-1][:-3]
            url_path = "/".join(parts[:-1] + [stem])
            if url_path in seen:
                continue
            seen.add(url_path)
            page = _load_page_from_file(md_file, url_path)
            if page:
                for t in raw_tags:
                    index.setdefault(t, []).append(page)
    return index


def find_tagged_pois(city_path, tag, _city_tag_index=None):
    if _city_tag_index is None:
        _city_tag_index = build_city_tag_index(city_path)
    return list(_city_tag_index.get(tag, []))


def _load_page_from_file(file_path, url_path):
    result = _load_md(file_path)
    if not result:
        return None
    meta, body = result
    slug = file_path.stem
    title = meta.get("title", slug)
    page_type = meta.get("type", "location")
    return Page(
        slug=slug, path=url_path, title=title,
        page_type=page_type, body=body, meta=meta,
    )


def load_page(path):
    slug = path.rsplit("/", 1)[-1] if "/" in path else path

    if TABBI_CONTENT_DIR:
        for md_file in [
            TABBI_CONTENT_DIR / path / f"{slug}.md",
            TABBI_CONTENT_DIR / f"{path}.md",
        ]:
            if md_file.is_file():
                return _load_page_from_file(md_file, path)

    for md_file in [
        CONTENT_DIR / path / f"{slug}.md",
        CONTENT_DIR / f"{path}.md",
    ]:
        if md_file.is_file():
            return _load_page_from_file(md_file, path)

    if "/" in path:
        parent_path, slug = path.rsplit("/", 1)
        md_file = CONTENT_DIR / parent_path / f"{slug}.md"
        if md_file.is_file():
            return _load_page_from_file(md_file, path)

    return None


@lru_cache(maxsize=1)
def _location_index():
    """Build {normalized_name: [content_path, ...]} for all location-type pages."""
    index = {}
    if not CONTENT_DIR.is_dir():
        return index
    for md_file in sorted(CONTENT_DIR.rglob("*.md")):
        result = _load_md(md_file)
        if not result:
            continue
        meta, _ = result
        if meta.get("type", "location") != "location":
            continue
        rel = str(md_file.relative_to(CONTENT_DIR).with_suffix(""))
        stem = md_file.stem
        title = meta.get("title", stem)
        for key in {stem.lower(), title.lower()}:
            index.setdefault(key, []).append(rel)
    return index


def resolve_location_name(name, hint=""):
    """Return the best-matching content path for a location name, or None.

    hint — free text (e.g. plan title + other stop paths) used to prefer
    candidates whose continent/country appears in the hint.
    """
    if not name or not name.strip():
        return None
    candidates = _location_index().get(name.lower().strip(), [])
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    if hint:
        hint_words = set(re.sub(r"[^a-z0-9]", " ", hint.lower()).split())
        def _score(path):
            parts = set(path.replace("_", " ").replace("-", " ").split("/")[:-1])
            return len(parts & hint_words)
        ranked = sorted(candidates, key=_score, reverse=True)
        if _score(ranked[0]) > _score(ranked[1]):
            return ranked[0]
    # Prefer Europe/Asia/Africa over North America as a tiebreak for common city names
    _prefer = ("europe/", "asia/", "africa/", "south-america/", "oceania/", "middle-east/")
    for c in candidates:
        if any(c.startswith(p) for p in _prefer):
            return c
    return candidates[0]
