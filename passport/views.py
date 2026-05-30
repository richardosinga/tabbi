import hashlib
import json
import re
import secrets
from functools import wraps
from pathlib import Path

from django.conf import settings
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import render
from django.views.decorators.http import require_POST

PASSPORTS_DIR = Path(settings.BASE_DIR) / "passports"
_PASSWORDS_FILE = PASSPORTS_DIR / ".passwords.json"


# ── Auth helpers (mirrors plans) ──────────────────────────────────────────────

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
    PASSPORTS_DIR.mkdir(exist_ok=True)
    data = _load_passwords()
    data[slug] = _hash_password(password)
    _PASSWORDS_FILE.write_text(json.dumps(data))


def _passport_authenticated(request, slug):
    return slug in request.session.get("authenticated_passports", [])


def _mark_passport_authenticated(request, slug):
    slugs = request.session.get("authenticated_passports", [])
    if slug not in slugs:
        request.session["authenticated_passports"] = slugs + [slug]


def _require_passport_auth(view_fn):
    @wraps(view_fn)
    def wrapper(request, slug, *args, **kwargs):
        passwords = _load_passwords()
        if slug not in passwords:
            return HttpResponseRedirect(f"/passport/signup/{slug}/")
        if not _passport_authenticated(request, slug):
            return HttpResponseRedirect(f"/passport/login/{slug}/?next={request.path}")
        return view_fn(request, slug, *args, **kwargs)
    return wrapper


_PASSPHRASE_WORDS = [
    "canyon", "delta", "fjord", "glacier", "harbor", "lagoon", "meadow", "mesa",
    "oasis", "rapids", "reef", "ridge", "steppe", "summit", "tundra", "valley",
    "atlas", "compass", "ferry", "lantern", "passage", "pilgrim", "rover", "voyage",
    "amber", "birch", "cedar", "cobalt", "coral", "crimson", "dusk", "ember",
    "falcon", "fern", "flint", "heron", "indigo", "jasper", "lemon", "lotus",
    "maple", "marigold", "mist", "moonrise", "mossy", "ochre", "onyx", "pebble",
    "pine", "pollen", "quartz", "saffron", "sage", "scarlet", "sienna", "slate",
    "spruce", "sterling", "talon", "thistle", "thorn", "topaz", "umber", "wren",
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
    return "-".join(random.sample(_PASSPHRASE_WORDS, 3)) + f"-{secrets.token_hex(2)}"


# ── Passport data helpers ─────────────────────────────────────────────────────

def _load_passport(slug):
    import frontmatter as fm
    path = PASSPORTS_DIR / f"{slug}.md"
    if not path.is_file():
        return None
    post = fm.load(path)
    return {
        "slug": slug,
        "name": post.metadata.get("name", ""),
        "nationalities": post.metadata.get("nationalities") or [],
        "home_city": post.metadata.get("home_city", ""),
        "visited": post.metadata.get("visited") or [],
        "interests": post.metadata.get("interests") or [],
    }


def _save_passport_data(slug, data):
    import frontmatter as fm
    PASSPORTS_DIR.mkdir(exist_ok=True)
    path = PASSPORTS_DIR / f"{slug}.md"
    if path.is_file():
        post = fm.load(path)
    else:
        post = fm.Post("")
    for key in ("name", "nationalities", "home_city", "visited", "interests"):
        if key in data:
            post.metadata[key] = data[key]
    with open(path, "w", encoding="utf-8") as fh:
        fm.dump(post, fh)


def get_session_passport(request):
    """Return the passport dict for the first passport in session, or None."""
    slugs = request.session.get("authenticated_passports", [])
    for slug in slugs:
        p = _load_passport(slug)
        if p:
            return p
    return None


# ── Views ─────────────────────────────────────────────────────────────────────

def passport_new(request):
    error = None
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        if not name:
            error = "Please enter your name or a nickname."
        else:
            import frontmatter as fm
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            if not slug:
                slug = "passport"
            path = PASSPORTS_DIR / f"{slug}.md"
            # Make slug unique if needed
            base = slug
            n = 2
            while path.exists():
                slug = f"{base}-{n}"
                path = PASSPORTS_DIR / f"{slug}.md"
                n += 1

            passphrase = _generate_passphrase()
            PASSPORTS_DIR.mkdir(exist_ok=True)
            interests_raw = request.POST.get("interests", "").strip()
            interests = [i.strip() for i in re.split(r"[,;]+", interests_raw) if i.strip()] if interests_raw else []
            home_city = request.POST.get("home_city", "").strip()
            nationalities_raw = request.POST.get("nationalities", "").strip()
            nationalities = [n.strip() for n in re.split(r"[,;]+", nationalities_raw) if n.strip()] if nationalities_raw else []

            meta = {"name": name, "passphrase": passphrase}
            if home_city:
                meta["home_city"] = home_city
            if interests:
                meta["interests"] = interests
            if nationalities:
                meta["nationalities"] = nationalities
            post = fm.Post("", **meta)
            with open(path, "w", encoding="utf-8") as fh:
                fm.dump(post, fh)
            _save_password(slug, passphrase)
            _mark_passport_authenticated(request, slug)
            request.session["new_passport_passphrase"] = passphrase
            return HttpResponseRedirect(f"/passport/{slug}/created/")
    return render(request, "passport/passport_new.html", {"error": error})


def passport_created(request, slug):
    if not _passport_authenticated(request, slug):
        return HttpResponseRedirect(f"/passport/login/{slug}/")
    passphrase = request.session.pop("new_passport_passphrase", None)
    passport = _load_passport(slug)
    if not passport:
        raise Http404
    return render(request, "passport/passport_created.html", {
        "passport": passport,
        "passphrase": passphrase,
    })


def passport_join(request):
    if request.method != "POST":
        return HttpResponseRedirect("/passport/new/")
    pw = request.POST.get("password", "")
    passwords = _load_passwords()
    for slug, hashed in passwords.items():
        if _check_password(pw, hashed):
            _mark_passport_authenticated(request, slug)
            return HttpResponseRedirect(f"/passport/{slug}/")
    return render(request, "passport/passport_new.html", {
        "error": "No passport found with that passphrase.",
        "join_mode": True,
    })


@_require_passport_auth
def passport_detail(request, slug):
    passport = _load_passport(slug)
    if not passport:
        raise Http404
    return render(request, "passport/passport_detail.html", {"passport": passport})


@_require_passport_auth
@require_POST
def passport_edit(request, slug):
    passport = _load_passport(slug)
    if not passport:
        raise Http404
    name = request.POST.get("name", "").strip() or passport["name"]
    home_city = request.POST.get("home_city", "").strip()
    interests_raw = request.POST.get("interests", "").strip()
    interests = [i.strip() for i in re.split(r"[,;]+", interests_raw) if i.strip()]
    nationalities_raw = request.POST.get("nationalities", "").strip()
    nationalities = [n.strip() for n in re.split(r"[,;]+", nationalities_raw) if n.strip()]
    _save_passport_data(slug, {
        "name": name,
        "home_city": home_city,
        "interests": interests,
        "nationalities": nationalities,
    })
    return HttpResponseRedirect(f"/passport/{slug}/")


def passport_login(request, slug):
    passwords = _load_passwords()
    if slug not in passwords:
        return HttpResponseRedirect("/passport/new/")
    error = None
    if request.method == "POST":
        pw = request.POST.get("password", "")
        if _check_password(pw, passwords[slug]):
            _mark_passport_authenticated(request, slug)
            next_url = request.GET.get("next", f"/passport/{slug}/")
            return HttpResponseRedirect(next_url)
        error = "Incorrect passphrase. Try again."
    passport = _load_passport(slug)
    return render(request, "passport/passport_login.html", {
        "slug": slug,
        "passport_name": passport["name"] if passport else slug,
        "error": error,
    })


def passport_signup(request, slug):
    return render(request, "passport/passport_new.html", {})
