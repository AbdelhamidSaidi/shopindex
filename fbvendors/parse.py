"""Turn a fetched Facebook Page into a Vendor record.

Facebook's markup changes constantly, so nothing here depends on CSS classes.
Three independent layers are tried and merged, most reliable first:

  1. OpenGraph/meta tags        - stable for years
  2. JSON payloads in <script>  - Relay data; key *names* are far more stable
                                  than the shapes around them
  3. escape-aware regex on the  - catches the same keys when they are nested
     raw HTML + visible text      inside double-encoded JSON strings
"""

from __future__ import annotations

import html as html_mod
import json
import re
from datetime import date, datetime, timedelta

from bs4 import BeautifulSoup

from . import geo, taxonomy
from .models import Vendor
from .normalize import (
    clean_name,
    clean_url,
    find_emails,
    find_phones,
    is_social,
    normalize_phone,
    normalize_slug,
    page_url,
    root_host,
    whatsapp_from_url,
)
from .price import aggregate

LOGIN_WALL = re.compile(
    r"(you must log in to continue|vous devez vous connecter|log into facebook|"
    r"connectez-vous à facebook|sign up for facebook|يجب تسجيل الدخول)",
    re.IGNORECASE,
)
NOT_AVAILABLE = re.compile(
    r"(this content isn't available|ce contenu n'est pas disponible|"
    r"page isn't available|contenu introuvable)",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------
# low level helpers
# --------------------------------------------------------------------------

def _unescape(s: str) -> str:
    """Undo the JSON/HTML escaping Facebook applies, sometimes twice."""
    if not s:
        return ""
    out = s
    for _ in range(2):
        if "\\u" in out or "\\/" in out or '\\"' in out:
            try:
                out = json.loads(f'"{out.replace(chr(34), chr(92) + chr(34))}"')
            except (json.JSONDecodeError, ValueError):
                out = out.replace("\\/", "/").replace('\\"', '"')
                out = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), out)
    return html_mod.unescape(out).strip()


def _raw_str(html: str, *keys: str, limit: int = 6) -> list[str]:
    """Find "<key>":"<value>" anywhere in the raw document, escaping and all."""
    out: list[str] = []
    for key in keys:
        pat = re.compile(rf'\\?"{re.escape(key)}\\?"\s*:\s*\\?"((?:[^"\\]|\\.){{1,400}}?)\\?"')
        for m in pat.finditer(html):
            val = _unescape(m.group(1))
            if val and val.lower() not in ("null", "none") and val not in out:
                out.append(val)
                if len(out) >= limit:
                    return out
    return out


def _raw_num(html: str, *keys: str) -> float | None:
    for key in keys:
        m = re.search(rf'\\?"{re.escape(key)}\\?"\s*:\s*"?(\d+(?:\.\d+)?)"?', html)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                continue
    return None


def _raw_bool(html: str, *keys: str) -> bool:
    for key in keys:
        if re.search(rf'\\?"{re.escape(key)}\\?"\s*:\s*true', html):
            return True
    return False


def iter_json_blobs(soup: BeautifulSoup):
    """Yield every parsed <script type="application/json"> / ld+json payload."""
    for tag in soup.find_all("script"):
        stype = (tag.get("type") or "").lower()
        if stype not in ("application/json", "application/ld+json"):
            continue
        raw = tag.string or tag.get_text() or ""
        raw = raw.strip()
        if not raw.startswith(("{", "[")):
            continue
        try:
            yield json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue


def walk(obj, _depth: int = 0):
    """Depth-first (key, value) over nested dicts/lists."""
    if _depth > 40:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k, v
            yield from walk(v, _depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            yield from walk(item, _depth + 1)


def collect(soup: BeautifulSoup, keys: set[str]) -> dict[str, list]:
    found: dict[str, list] = {k: [] for k in keys}
    for blob in iter_json_blobs(soup):
        for k, v in walk(blob):
            if k in keys and v not in (None, "", [], {}) and v not in found[k]:
                found[k].append(v)
    return found


# --------------------------------------------------------------------------
# relative dates ("2 h", "Il y a 3 jours", "5d", "23 août")
# --------------------------------------------------------------------------

_MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("january", "janvier", "jan", "janv", "يناير"),
            ("february", "février", "fevrier", "feb", "fév", "fev", "févr", "فبراير"),
            ("march", "mars", "mar", "مارس"),
            ("april", "avril", "apr", "avr", "أبريل"),
            ("may", "mai", "ماي", "مايو"),
            ("june", "juin", "jun", "يونيو"),
            ("july", "juillet", "jul", "juil", "يوليوز", "يوليو"),
            ("august", "août", "aout", "aug", "غشت", "أغسطس"),
            ("september", "septembre", "sep", "sept", "شتنبر", "سبتمبر"),
            ("october", "octobre", "oct", "أكتوبر"),
            ("november", "novembre", "nov", "نونبر", "نوفمبر"),
            ("december", "décembre", "decembre", "dec", "déc", "دجنبر", "ديسمبر"),
        ],
        start=1,
    )
    for m in names
}

_REL = re.compile(
    r"(?:il y a\s*)?(\d{1,3})\s*(min|mins|minutes?|h|hrs?|hours?|heures?|d|j|days?|jours?|"
    r"w|sem|weeks?|semaines?|mo|months?|mois|y|yrs?|years?|ans?|"
    r"د|س|ساعة|يوم|أيام|أسبوع|شهر|سنة)\b",
    re.IGNORECASE,
)
_UNIT_DAYS = {
    "min": 0, "mins": 0, "minute": 0, "minutes": 0, "د": 0,
    "h": 0, "hr": 0, "hrs": 0, "hour": 0, "hours": 0, "heure": 0, "heures": 0, "س": 0, "ساعة": 0,
    "d": 1, "j": 1, "day": 1, "days": 1, "jour": 1, "jours": 1, "يوم": 1, "أيام": 1,
    "w": 7, "sem": 7, "week": 7, "weeks": 7, "semaine": 7, "semaines": 7, "أسبوع": 7,
    "mo": 30, "month": 30, "months": 30, "mois": 30, "شهر": 30,
    "y": 365, "yr": 365, "yrs": 365, "year": 365, "years": 365, "an": 365, "ans": 365, "سنة": 365,
}
_ABS = re.compile(r"\b(\d{1,2})\s+([A-Za-zÀ-ÿ؀-ۿ]{3,12})\.?\s*(\d{4})?\b")
_TODAY_WORDS = re.compile(r"\b(just now|à l'instant|a l'instant|now|maintenant|الآن)\b", re.I)
_YESTERDAY = re.compile(r"\b(yesterday|hier|أمس|البارحة)\b", re.I)


def parse_timestamp_text(text: str, today: date | None = None) -> date | None:
    """Best-effort date from a Facebook post timestamp label."""
    if not text:
        return None
    today = today or date.today()
    t = text.strip()
    if _TODAY_WORDS.search(t):
        return today
    if _YESTERDAY.search(t):
        return today - timedelta(days=1)
    m = _REL.search(t)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        return today - timedelta(days=n * _UNIT_DAYS.get(unit, 0))
    m = _ABS.search(t)
    if m:
        day, mon_word, year = m.group(1), m.group(2).lower().strip("."), m.group(3)
        mon = _MONTHS.get(mon_word)
        if mon:
            y = int(year) if year else today.year
            try:
                d = date(y, mon, int(day))
            except ValueError:
                return None
            if not year and d > today:
                d = d.replace(year=y - 1)
            return d
    return None


def _epoch_dates(html: str) -> list[date]:
    """Post creation times often survive as raw unix seconds in the payload."""
    out: list[date] = []
    now = datetime.now().timestamp()
    floor = now - 10 * 365 * 86400
    for m in re.finditer(r'\\?"(?:creation_time|publish_time|created_time)\\?"\s*:\s*(\d{10})', html):
        ts = int(m.group(1))
        if floor <= ts <= now + 86400:
            out.append(datetime.fromtimestamp(ts).date())
    return out


# --------------------------------------------------------------------------
# main entry point
# --------------------------------------------------------------------------

_JSON_KEYS = {
    "name", "category_name", "page_category", "categories", "single_line_address",
    "street_address", "address", "phone_number", "formatted_phone", "phone", "website",
    "email", "follower_count", "followers_count", "like_count", "overall_star_rating",
    "rating_count", "is_verified", "page_intro", "about", "description", "city_name",
}


def parse_page(
    html: str,
    *,
    url: str = "",
    rendered_text: str = "",
    post_texts: list[str] | None = None,
    post_dates: list[str] | None = None,
) -> Vendor:
    """Build a Vendor from a page's HTML plus anything the fetcher rendered."""
    v = Vendor()
    v.slug = normalize_slug(url)
    v.facebook_url = page_url(v.slug) or url
    post_texts = post_texts or []
    soup = BeautifulSoup(html or "", "html.parser")
    text_blob = rendered_text or soup.get_text(" ", strip=True)

    if LOGIN_WALL.search(text_blob[:4000]):
        v.fetch_status = "login_wall"
    elif NOT_AVAILABLE.search(text_blob[:4000]):
        v.fetch_status = "unavailable"
    else:
        v.fetch_status = "ok"

    panel = parse_about_panel(rendered_text)

    meta = {
        (t.get("property") or t.get("name") or "").lower(): (t.get("content") or "")
        for t in soup.find_all("meta")
    }
    js = collect(soup, _JSON_KEYS)

    def first_js(*keys: str) -> str:
        for k in keys:
            for val in js.get(k, []):
                if isinstance(val, str) and val.strip():
                    return val.strip()
                if isinstance(val, dict):
                    for sub in ("text", "name", "value"):
                        if isinstance(val.get(sub), str) and val[sub].strip():
                            return val[sub].strip()
                if isinstance(val, list) and val:
                    head = val[0]
                    if isinstance(head, str):
                        return head
                    if isinstance(head, dict):
                        for sub in ("text", "name"):
                            if isinstance(head.get(sub), str):
                                return head[sub]
        return ""

    # ---- name -------------------------------------------------------------
    # og:title and <title> are trustworthy; the raw-JSON fallback is not, because
    # Facebook's own bundle names sit under the same "name" key.
    name_candidates = [
        meta.get("og:title", ""),
        soup.title.get_text() if soup.title else "",
        first_js("name"),
        *_raw_str(html, "page_name", "name", limit=4),
    ]
    v.name = ""
    for cand in name_candidates:
        cleaned = _strip_city_suffix(clean_name(cand))
        if _plausible_page_name(cleaned):
            v.name = cleaned
            break

    # ---- page id ----------------------------------------------------------
    pid = _raw_num(html, "pageID", "page_id", "delegate_page_id", "profile_id")
    v.page_id = str(int(pid)) if pid else ""
    if not v.page_id:
        m = re.search(r'"entity_id"\s*:\s*"(\d{6,})"', html)
        v.page_id = m.group(1) if m else ""
    if not v.slug and v.page_id:
        v.slug = v.page_id
        v.facebook_url = page_url(v.page_id)

    # ---- category ---------------------------------------------------------
    raw_cat = (
        panel.get("categories")
        or first_js("category_name", "page_category", "categories")
        or " ".join(_raw_str(html, "category_name", "page_category", limit=3))
    )
    v.raw_category = taxonomy.tidy_raw_category(raw_cat)
    about_text = (
        first_js("page_intro", "about", "description")
        or meta.get("og:description", "")
        or (_raw_str(html, "page_intro", "best_description") or [""])[0]
    )
    v.about = _clean_about(about_text)
    v.category, _kw = taxonomy.classify(v.raw_category, v.name, v.about)
    if not v.category and v.raw_category:
        v.category = v.raw_category.split(" / ")[0]

    # ---- address / location ----------------------------------------------
    v.address = (
        panel.get("address")
        or first_js("single_line_address", "street_address", "address")
        or (_raw_str(html, "single_line_address", "street_address") or [""])[0]
    )[:200]
    city_src = " | ".join(filter(None, [v.address, panel.get("service_area", ""),
                                        first_js("city_name"), v.about, v.name]))
    v.city, v.region = geo.lookup_city(city_src)
    v.location = geo.format_location(v.city, v.region, v.address)

    # ---- contact ----------------------------------------------------------
    phone_candidates: list[str] = []
    for raw in (
        panel.get("phone", ""),
        first_js("phone_number", "formatted_phone", "phone"),
        *_raw_str(html, "phone_number", "formatted_phone", "mobile_phone", limit=4),
    ):
        n = normalize_phone(raw)
        if n and n not in phone_candidates:
            phone_candidates.append(n)
    # Anything the About panel rendered, then the raw payload as a last resort.
    post_blob = "\n".join(post_texts)
    for n in (find_phones(rendered_text) + find_phones(post_blob) + find_phones(v.about)
              + find_phones(text_blob[:200_000])):
        if n not in phone_candidates:
            phone_candidates.append(n)
    v.all_phones = phone_candidates
    v.phone = phone_candidates[0] if phone_candidates else ""

    links: list[str] = []
    for a in soup.find_all("a", href=True):
        links.append(a["href"])
    links += _raw_str(html, "website", "url", "external_url", limit=40)
    links += re.findall(r'https?://(?:l\.)?facebook\.com/l\.php\?u=[^"\'\s\\]+', html)

    site, wa = "", ""
    for raw_link in links:
        u = clean_url(raw_link)
        if not u:
            continue
        if not wa:
            wa = whatsapp_from_url(raw_link)
        if not site and not is_social(u) and root_host(u):
            if not re.search(r"\.(png|jpe?g|gif|svg|css|js|ico|webp)$", u, re.I):
                site = u
    v.website = clean_url(panel.get("website", "")) or site
    if not wa:
        wa = _whatsapp_near_mention(rendered_text) or _whatsapp_near_mention(post_blob)
    v.whatsapp = wa

    emails = find_emails(panel.get("email", "")) or find_emails(rendered_text) \
        or find_emails(v.about) or find_emails(html[:400_000])
    v.email = emails[0] if emails else ""

    # ---- audience ---------------------------------------------------------
    v.followers = int(_raw_num(html, "follower_count", "followers_count") or 0)
    v.likes = int(_raw_num(html, "like_count", "page_likers_count") or 0)
    if not (v.followers or v.likes):
        v.followers, v.likes = _counts_from_text(rendered_text or meta.get("og:description", ""))
    rating = _raw_num(html, "overall_star_rating", "average_star_rating")
    v.rating = round(rating, 2) if rating and 0 < rating <= 5 else None
    v.reviews = int(panel.get("reviews") or _raw_num(html, "rating_count", "review_count") or 0)
    if not v.rating and panel.get("rating"):
        try:
            v.rating = float(panel["rating"])
        except ValueError:
            pass
    v.verified = _raw_bool(html, "is_verified", "blue_verified", "is_verified_page")

    # ---- price signal from posts -----------------------------------------
    sources = post_texts or ([rendered_text] if rendered_text else [])
    stats = aggregate(sources)
    v.posts_scanned = len(post_texts)
    summary = stats.summary()
    if summary:
        v.price_min = float(summary["min"])
        v.price_max = float(summary["max"])
        v.price_median = float(summary["median"])
        v.price_count = int(summary["count"])
        v.currency = str(summary["currency"])
    v.price_signal = stats.signal()
    v.has_shop = stats.has_shop
    v.delivers = stats.delivers or bool(panel.get("service_area"))

    # ---- freshness --------------------------------------------------------
    dates = [d for d in (parse_timestamp_text(t) for t in (post_dates or [])) if d]
    dates += _epoch_dates(html)
    if dates:
        newest = max(dates)
        v.last_post_date = newest.isoformat()
        v.last_post_age_days = max((date.today() - newest).days, 0)

    v.stamp()
    return v


_COUNT_RE = re.compile(
    r"([\d][\d\s.,]*)\s*(K|M|k|m)?\s*(followers|abonnés|abonnes|j'aime|likes|مُتابع|متابع|إعجاب)",
    re.IGNORECASE,
)


def _counts_from_text(text: str) -> tuple[int, int]:
    """'12,4 K followers · 11 890 likes' -> (12400, 11890)."""
    followers = likes = 0
    for m in _COUNT_RE.finditer(text or ""):
        num = m.group(1).replace(" ", "").replace(" ", "").replace(",", ".").strip(".")
        try:
            val = float(num)
        except ValueError:
            continue
        mult = {"k": 1_000, "m": 1_000_000}.get((m.group(2) or "").lower(), 1)
        val = int(val * mult)
        word = m.group(3).lower()
        if word.startswith(("follow", "abonn")) or "متابع" in word:
            followers = max(followers, val)
        else:
            likes = max(likes, val)
    return followers, likes


_WA_MENTION = re.compile(
    r"(whats\s?app|واتساب|wtsp)\D{0,30}((?:\+?212|0)[\s\-./]?\d[\d\s\-./]{7,12})",
    re.IGNORECASE,
)


def _whatsapp_near_mention(text: str) -> str:
    """A number written right after the word WhatsApp is a WhatsApp number."""
    for m in _WA_MENTION.finditer(text or ""):
        n = normalize_phone(m.group(2))
        if n:
            return n
    return ""


# ---------------------------------------------------------------------------
# About panel
#
# Facebook renders the contact block as value-then-label pairs:
#
#     Coordonnees
#     12 Rue Ibn Sina, Casablanca      <- value
#     Adresse                          <- label
#     0675-845505
#     Mobile
#
# "Categories" is the exception: its value follows the label. Reading by label
# rather than by markup is what keeps this working when the DOM is reshuffled.
# ---------------------------------------------------------------------------

_LABELS_VALUE_BEFORE = {
    "address": {"adresse", "address", "العنوان"},
    "phone": {"mobile", "telephone", "téléphone", "phone", "numero de telephone",
              "numéro de téléphone", "phone number", "الهاتف", "رقم الهاتف"},
    "email": {"e-mail", "email", "adresse e-mail", "courriel", "البريد الإلكتروني"},
    "website": {"site web", "website", "site internet", "الموقع الإلكتروني", "الموقع"},
    "service_area": {"zone de service", "service area", "منطقة الخدمة"},
}
_LABELS_VALUE_AFTER = {
    "categories": {"categories", "catégories", "categorie", "catégorie", "الفئات", "الفئة"},
}

_REVIEWS_RE = re.compile(r"\((\d[\d\s.,]*)\s*(avis|reviews?|تقييم|مراجعة)\)", re.I)
_STARS_RE = re.compile(r"(\d(?:[.,]\d)?)\s*(?:sur|/|out of)\s*5", re.I)

# Boilerplate Facebook shows instead of a real bio.
_ABOUT_JUNK = re.compile(
    r"(ce problème vient généralement|this content isn't available|"
    r"le propriétaire ne l|log in or sign up|connectez-vous ou inscrivez|"
    r"voir plus sur facebook|see more on facebook|créer un nouveau compte|"
    r"mot de passe|informations de compte)",
    re.IGNORECASE,
)


def _norm_label(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower().rstrip(":"))


def parse_about_panel(text: str) -> dict[str, str]:
    """Read the rendered About panel by its field labels."""
    out: dict[str, str] = {}
    lines = [ln.strip() for ln in (text or "").splitlines()]
    lines = [ln for ln in lines if ln]
    for i, line in enumerate(lines):
        label = _norm_label(line)
        for field, names in _LABELS_VALUE_BEFORE.items():
            if label in names and i > 0 and field not in out:
                value = lines[i - 1].strip()
                if value and _norm_label(value) not in names:
                    out[field] = value
        for field, names in _LABELS_VALUE_AFTER.items():
            if label in names and i + 1 < len(lines) and field not in out:
                out[field] = lines[i + 1].strip()

    joined = "\n".join(lines)
    m = _REVIEWS_RE.search(joined)
    if m:
        out["reviews"] = re.sub(r"\D", "", m.group(1))
    m = _STARS_RE.search(joined)
    if m:
        out["rating"] = m.group(1).replace(",", ".")
    return out


def _clean_about(text: str) -> str:
    """Drop Facebook's own boilerplate so the bio column stays meaningful."""
    t = re.sub(r"\s+", " ", text or "").strip()
    if len(t) < 8 or _ABOUT_JUNK.search(t):
        return ""
    return t[:400]


_CITY_SUFFIX = re.compile(r"\s*[|\-–·]\s*([^|\-–·]{3,30})$")


def _strip_city_suffix(name: str) -> str:
    """og:title is often 'Vendor Name | Casablanca'; the city is not part of the name."""
    m = _CITY_SUFFIX.search(name or "")
    if m and geo.lookup_city(m.group(1))[0]:
        return name[: m.start()].strip()
    return name


# Facebook's client bundles are exposed under the same JSON keys as page names.
_FB_INTERNAL = re.compile(
    r"(Bundle|Worker|Loader|Config|Module|Provider|Container|Polyfill|Runtime|"
    r"Shim|Chunk|Manifest|Sprite|Resource)$"
)
_CODE_IDENT = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
# All-lowercase, slash- or underscore-joined: "rti/web_rs_transport_selecting_client"
_PATH_LIKE = re.compile(r"^[a-z0-9]+([/_][a-z0-9]+)+$")
_JS_WORDS = {
    "method", "default", "exports", "prototype", "constructor", "module",
    "function", "object", "string", "number", "boolean", "undefined", "null",
    "props", "state", "render", "value", "type", "data", "result", "config",
}


def _plausible_page_name(name: str) -> bool:
    """Reject values that are code identifiers rather than business names."""
    n = (name or "").strip()
    if len(n) < 2 or len(n) > 120:
        return False
    if _FB_INTERNAL.search(n):
        return False
    # Bilingual page names legitimately contain "/" ("GREPOM/BirdLife Morocco"),
    # so only reject values shaped like a code path or module name.
    if _PATH_LIKE.match(n) or n.lower() in _JS_WORDS:
        return False
    if " " in n:
        return True          # a space means it reads as a name, not an identifier
    # Single token: camelCase with no spaces is far more likely to be code here.
    return not (_CODE_IDENT.match(n) and re.search(r"[a-z][A-Z]", n))
