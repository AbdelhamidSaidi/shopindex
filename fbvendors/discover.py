"""Finding candidate vendor Pages.

Four independent sources, usable together:

  seeds    - a file of Page URLs/slugs you already have (most precise)
  search   - DuckDuckGo/Bing HTML results for site:facebook.com queries
  fbsearch - Facebook's own Pages search, driven through the logged-in session
  related  - the "Pages similaires" rail on a page you already fetched
"""

from __future__ import annotations

import asyncio
import re
import urllib.parse as up
from collections.abc import Callable
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from .geo import looks_moroccan
from .normalize import normalize_slug
from .taxonomy import CATEGORIES

# Slugs that are Facebook's own surfaces, never a vendor.
_RESERVED = {
    "pages", "groups", "events", "marketplace", "watch", "gaming", "help", "policies",
    "privacy", "settings", "login", "profile.php", "search", "sharer", "share",
    "people", "photo", "photo.php", "story.php", "permalink.php", "hashtag", "bookmarks",
    "business", "ads", "legal", "terms", "l.php", "reel", "video", "story", "media",
    "notes", "places", "directory", "campaign", "recover", "checkpoint", "home.php",
}

_FB_LINK = re.compile(
    r"https?://(?:[a-z0-9\-]+\.)?facebook\.com/(?:pages/)?[A-Za-z0-9._%\-]{2,}"
    r"(?:/\d{5,})?(?:\?id=\d{5,})?", re.I
)

# The search engines link to their own Facebook presence in page chrome.
_ENGINE_CHROME = {
    "copilotsearch", "bing", "microsoft", "duckduckgo", "google", "yahoo", "msn",
}

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

# Moroccan cities worth sweeping, largest commercial centres first.
DEFAULT_CITIES = [
    "Casablanca", "Rabat", "Marrakech", "Tanger", "Fes", "Agadir", "Meknes",
    "Oujda", "Kenitra", "Tetouan", "Sale", "Mohammedia", "El Jadida", "Beni Mellal",
    "Nador", "Safi", "Khouribga", "Settat", "Berrechid", "Laayoune",
]


def is_vendor_slug(slug: str) -> bool:
    s = (slug or "").strip("/").lower()
    if not s or s in _RESERVED or len(s) < 3:
        return False
    if s in _ENGINE_CHROME:
        return False
    if s.endswith(".php") or s.startswith(("tr?", "rsrc")):
        return False   # every facebook.com/*.php path is a platform surface
    if s.isdigit():
        return len(s) >= 5   # numeric page ids are legitimate
    return bool(re.match(r"^[a-z0-9.\-_]+$", s))


def extract_page_slugs(html_or_text: str) -> list[str]:
    """Every distinct Facebook Page slug referenced in a blob of HTML or text."""
    out: list[str] = []
    for m in _FB_LINK.finditer(html_or_text or ""):
        slug = normalize_slug(m.group(0))
        if slug and is_vendor_slug(slug) and slug not in out:
            out.append(slug)
    return out


def build_queries(
    categories: list[str] | None = None,
    cities: list[str] | None = None,
    extra_terms: list[str] | None = None,
    per_category: int = 4,
) -> list[str]:
    """Cross vendor keywords with Moroccan cities into search-engine queries."""
    cats = categories or list(CATEGORIES)
    cities = cities or DEFAULT_CITIES
    queries: list[str] = []
    for cat in cats:
        kws = [k for k in CATEGORIES.get(cat, [cat]) if k.isascii() and " " not in k]
        for kw in kws[:per_category]:
            for city in cities:
                queries.append(f'site:facebook.com "{kw}" "{city}"')
    for term in extra_terms or []:
        for city in cities:
            queries.append(f'site:facebook.com "{term}" "{city}"')
    return queries


def load_seeds(path: Path | str) -> list[tuple[str, str]]:
    """Read slugs/URLs from a .txt (one per line) or any CSV column holding FB links."""
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        hint = line
        found = extract_page_slugs(line)
        if not found:
            cand = normalize_slug(line.split(",")[0].split("\t")[0])
            found = [cand] if cand and is_vendor_slug(cand) else []
        for slug in found:
            if slug not in seen:
                seen.add(slug)
                out.append((slug, hint[:120]))
    return out


class SearchDiscovery:
    """Search-engine discovery. No API keys; HTML endpoints, politely paced."""

    DDG = "https://html.duckduckgo.com/html/"
    BING = "https://www.bing.com/search"
    BRAVE = "https://search.brave.com/search"

    # API-backed engines. These are the only way to run a large sweep from one
    # IP: the free HTML endpoints rate-limit long before a full sweep finishes.
    SERPER_API = "https://google.serper.dev/search"
    BRAVE_API = "https://api.search.brave.com/res/v1/web/search"

    # Every free engine rate-limits sustained querying from one IP. Rotating
    # spreads the load so each sees a fraction of the request rate.
    ROTATION = ("ddg", "brave")

    API_ENGINES = ("serper", "brave-api")

    def __init__(self, delay: float = 8.0, timeout: float = 25.0, engine: str = "ddg",
                 retries: int = 2, api_key: str = ""):
        self.delay = delay
        self.timeout = timeout
        self.engine = engine
        self.retries = retries
        self.throttled = 0   # consecutive throttle responses, surfaced to the caller
        self.api_key = api_key
        self.api_error = ""   # last API failure, so a bad key is not silent
        self.engine_stats: dict[str, dict[str, int]] = {}
        if engine in self.API_ENGINES and not api_key:
            raise ValueError(f"engine '{engine}' needs an API key")

    def _pick_engine(self, index: int) -> str:
        if self.engine != "auto":
            return self.engine
        return self.ROTATION[index % len(self.ROTATION)]

    async def _api_results(self, client: httpx.AsyncClient, engine: str,
                           query: str, limit: int) -> list[tuple[str, str]] | None:
        """Query a paid search API. Returns None if the call failed."""
        try:
            if engine == "serper":
                r = await client.post(
                    self.SERPER_API,
                    headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"},
                    json={"q": query, "num": min(limit, 100), "gl": "ma"},
                )
                items = (r.json() or {}).get("organic", []) if r.status_code == 200 else []
            else:  # brave-api
                r = await client.get(
                    self.BRAVE_API,
                    headers={"X-Subscription-Token": self.api_key,
                             "Accept": "application/json"},
                    params={"q": query, "count": min(limit, 20), "country": "MA"},
                )
                data = (r.json() or {}) if r.status_code == 200 else {}
                items = data.get("web", {}).get("results", [])
        except (httpx.HTTPError, ValueError):
            return None
        if r.status_code != 200:
            self.engine_stats.setdefault(engine, {"ok": 0, "throttled": 0})["throttled"] += 1
            if r.status_code in (401, 403, 422):
                # Brave answers 422 SUBSCRIPTION_TOKEN_INVALID for a bad key.
                self.api_error = f"HTTP {r.status_code}: the API key was rejected"
            elif r.status_code == 429:
                self.api_error = f"HTTP 429: API quota exhausted"
            else:
                self.api_error = f"HTTP {r.status_code}"
            return None
        self.engine_stats.setdefault(engine, {"ok": 0, "throttled": 0})["ok"] += 1

        out: list[tuple[str, str]] = []
        for item in items:
            url = item.get("link") or item.get("url") or ""
            if "facebook.com" not in url:
                continue
            slug = normalize_slug(url)
            if not slug or not is_vendor_slug(slug):
                continue
            if any(slug == existing for existing, _ in out):
                continue
            label = (item.get("title") or item.get("description") or "")[:120]
            out.append((slug, label))
            if len(out) >= limit:
                break
        return out

    async def _request(self, client: httpx.AsyncClient, engine: str, query: str):
        if engine == "bing":
            return await client.get(self.BING, params={"q": query, "count": "30"})
        if engine == "brave":
            return await client.get(self.BRAVE, params={"q": query})
        return await client.post(self.DDG, data={"q": query, "kl": "ma-fr"})

    async def _get(self, client: httpx.AsyncClient, query: str, engine: str | None = None) -> str:
        """One query, with backoff. 202/429/403 all mean 'slow down', not 'no results'.

        A CAPTCHA challenge is treated as a hard stop for that engine - we back off,
        we never try to solve it.
        """
        engine = engine or self.engine
        stats = self.engine_stats.setdefault(engine, {"ok": 0, "throttled": 0})
        backoff = self.delay
        for attempt in range(self.retries + 1):
            try:
                r = await self._request(client, engine, query)
            except (httpx.HTTPError, httpx.InvalidURL):
                return ""
            if r.status_code == 200 and "captcha" not in r.text[:6000].lower():
                self.throttled = 0
                stats["ok"] += 1
                return r.text
            if r.status_code in (202, 429, 403) or "captcha" in r.text[:6000].lower():
                self.throttled += 1
                stats["throttled"] += 1
                if attempt < self.retries:
                    backoff = min(backoff * 3, 180)
                    await asyncio.sleep(backoff)
                    continue
            return ""
        return ""

    @staticmethod
    def _unwrap(href: str) -> str:
        """DuckDuckGo wraps results as //duckduckgo.com/l/?uddg=<encoded>."""
        if "uddg=" in href:
            qs = up.parse_qs(up.urlsplit(href if href.startswith("http") else "https:" + href).query)
            if qs.get("uddg"):
                return up.unquote(qs["uddg"][0])
        return href

    # Result-container selectors keep navigation/chrome links out of the harvest.
    _RESULT_SELECTORS = {
        "ddg": ["a.result__a", ".result__title a", ".web-result a[href]"],
        "bing": ["li.b_algo h2 a", "li.b_algo a[href]", ".b_title a"],
        "brave": ["#results a.h", "#results .snippet a[href]", "[data-type='web'] a[href]"],
    }

    def _result_links(self, soup, engine: str | None = None) -> list:
        for sel in self._RESULT_SELECTORS.get(engine or self.engine, []):
            found = soup.select(sel)
            if found:
                return found
        # Fall back to every link only if the page shape is unrecognised.
        return soup.find_all("a", href=True)

    def extract_from_html(self, html: str, fallback_label: str = "",
                          max_results: int = 25,
                          engine: str | None = None) -> list[tuple[str, str]]:
        """Pull Facebook Page slugs out of one results page. Network-free, so testable."""
        out: list[tuple[str, str]] = []
        soup = BeautifulSoup(html or "", "html.parser")
        for a in self._result_links(soup, engine or self.engine):
            url = self._unwrap(a.get("href") or "")
            if "facebook.com" not in url:
                continue
            slug = normalize_slug(url)
            if not slug or not is_vendor_slug(slug):
                continue
            if any(slug == existing for existing, _ in out):
                continue
            label = " ".join(a.get_text(" ", strip=True).split())[:120]
            out.append((slug, label or fallback_label))
            if len(out) >= max_results:
                break
        return out

    async def run(
        self,
        queries: list[str],
        max_per_query: int = 25,
        on_results: "Callable[[str, list[tuple[str, str]]], None] | None" = None,
    ) -> list[tuple[str, str]]:
        """Run every query. `on_results` is called after each one so a long sweep
        can persist as it goes instead of risking the lot on a single crash."""
        results: list[tuple[str, str]] = []
        seen: set[str] = set()
        headers = {"User-Agent": _UA, "Accept-Language": "fr-FR,fr;q=0.9,ar;q=0.8"}
        async with httpx.AsyncClient(headers=headers, timeout=self.timeout,
                                     follow_redirects=True) as client:
            for i, q in enumerate(queries):
                if i:
                    await asyncio.sleep(self.delay)
                engine = self._pick_engine(i)
                if engine in self.API_ENGINES:
                    hits = await self._api_results(client, engine, q, max_per_query) or []
                else:
                    html = await self._get(client, q, engine=engine)
                    hits = self.extract_from_html(html, q, max_per_query, engine=engine)
                fresh: list[tuple[str, str]] = []
                for slug, label in hits:
                    if slug in seen:
                        continue
                    seen.add(slug)
                    fresh.append((slug, label))
                results.extend(fresh)
                if on_results is not None:
                    on_results(q, fresh)
        return results


async def facebook_search(fetcher, query: str, max_results: int = 30) -> list[tuple[str, str]]:
    """Facebook's own Pages search. Needs a logged-in session."""
    url = f"https://www.facebook.com/search/pages/?q={up.quote(query)}"
    page = await fetcher._context.new_page()
    out: list[tuple[str, str]] = []
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=fetcher.timeout_ms)
        await fetcher._dismiss_consent(page)
        await page.wait_for_timeout(2500)
        for _ in range(3):
            await page.mouse.wheel(0, 2600)
            await page.wait_for_timeout(1600)
        html = await page.content()
    except Exception:
        html = ""
    finally:
        try:
            await page.close()
        except Exception:
            pass
    for slug in extract_page_slugs(html)[:max_results]:
        out.append((slug, f"fbsearch:{query}"))
    return out


def related_pages(html: str, exclude: str = "") -> list[str]:
    """Slugs from the 'Pages similaires' / 'Related pages' rail."""
    slugs = extract_page_slugs(html)
    return [s for s in slugs if s.lower() != (exclude or "").lower()]


def geo_filter(label: str) -> bool:
    """Cheap Morocco check for a search-result snippet."""
    return looks_moroccan(label)
