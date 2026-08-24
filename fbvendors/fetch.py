"""Fetching Facebook Pages with Playwright.

Facebook serves almost nothing useful to an anonymous client, so the normal mode
is: the operator logs in once by hand (`fbvendors login`), the session is saved
to disk, and runs reuse it. Everything here is deliberately slow and serial-ish
-- hammering Facebook gets the account checkpointed, which costs far more time
than the delays do.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

from playwright.async_api import Error as PWError
from playwright.async_api import TimeoutError as PWTimeout
from playwright.async_api import async_playwright

from .normalize import normalize_slug

FB = "https://www.facebook.com"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

# Prefer the privacy-preserving choice on the consent dialog; never "accept all".
_DECLINE_CONSENT = [
    "button:has-text('Decline optional cookies')",
    "button:has-text('Refuser les cookies facultatifs')",
    "button:has-text('Only allow essential cookies')",
    "button:has-text('Uniquement les cookies essentiels')",
    "[aria-label='Decline optional cookies']",
    "[aria-label='Refuser les cookies facultatifs']",
]

_BLOCK_MARKERS = ("/login", "/checkpoint", "/recover", "login.php")


class Blocked(RuntimeError):
    """Facebook redirected us to a login/checkpoint wall."""


@dataclass
class PageFetch:
    slug: str
    url: str
    html: str = ""
    text: str = ""
    posts: list[str] = field(default_factory=list)
    post_dates: list[str] = field(default_factory=list)
    status: str = "ok"
    from_cache: bool = False


class Cache:
    """Raw HTML on disk, so re-parsing never costs another request."""

    def __init__(self, root: Path, ttl_hours: float = 168.0):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl_hours * 3600

    def _path(self, key: str) -> Path:
        h = hashlib.sha1(key.encode("utf-8")).hexdigest()
        return self.root / h[:2] / f"{h}.json"

    def get(self, key: str) -> dict | None:
        """Returns the whole fetch record: html plus the rendered text and posts."""
        p = self._path(key)
        if not p.exists():
            return None
        if self.ttl and (time.time() - p.stat().st_mtime) > self.ttl:
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            return None

    def put(self, key: str, record: dict) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            p.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


class Fetcher:
    def __init__(
        self,
        session_path: Path | str = "fb_session.json",
        cache_dir: Path | str = "data/cache",
        *,
        headless: bool = True,
        concurrency: int = 2,
        delay: tuple[float, float] = (4.0, 9.0),
        timeout_ms: int = 45_000,
        scrolls: int = 3,
        use_cache: bool = True,
        locale: str = "fr-FR",
    ):
        self.session_path = Path(session_path)
        self.cache = Cache(cache_dir)
        self.headless = headless
        self.delay = delay
        self.timeout_ms = timeout_ms
        self.scrolls = scrolls
        self.use_cache = use_cache
        self.locale = locale
        self._sem = asyncio.Semaphore(max(1, concurrency))
        self._last_request = 0.0
        self._lock = asyncio.Lock()
        self._pw = None
        self._browser = None
        self._context = None
        self.blocked_count = 0

    # -- lifecycle ---------------------------------------------------------

    async def __aenter__(self) -> "Fetcher":
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        state = str(self.session_path) if self.session_path.exists() else None
        self._context = await self._browser.new_context(
            storage_state=state,
            user_agent=UA,
            locale=self.locale,
            viewport={"width": 1366, "height": 900},
            timezone_id="Africa/Casablanca",
        )
        self._context.set_default_timeout(self.timeout_ms)
        # Images and fonts are pure bandwidth here; the text is what we want.
        await self._context.route(
            "**/*",
            lambda route: asyncio.ensure_future(
                route.abort()
                if route.request.resource_type in ("image", "media", "font")
                else route.continue_()
            ),
        )
        return self

    async def __aexit__(self, *exc) -> None:
        for closer in (self._context, self._browser):
            if closer:
                try:
                    await closer.close()
                except PWError:
                    pass
        if self._pw:
            await self._pw.stop()

    @property
    def has_session(self) -> bool:
        return self.session_path.exists()

    # -- pacing ------------------------------------------------------------

    async def _pace(self) -> None:
        async with self._lock:
            wait = random.uniform(*self.delay) - (time.monotonic() - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()

    async def _dismiss_consent(self, page) -> None:
        for sel in _DECLINE_CONSENT:
            try:
                btn = page.locator(sel).first
                if await btn.count() and await btn.is_visible():
                    await btn.click(timeout=3000)
                    await page.wait_for_timeout(600)
                    return
            except (PWError, PWTimeout):
                continue

    # -- fetching ----------------------------------------------------------

    async def fetch(self, slug_or_url: str, *, want_posts: bool = True) -> PageFetch:
        slug = normalize_slug(slug_or_url) or slug_or_url.strip("/")
        url = f"{FB}/{slug}"
        res = PageFetch(slug=slug, url=url)

        cached = self.cache.get(url) if self.use_cache else None
        if cached:
            res.html = cached.get("html", "")
            res.text = cached.get("text", "")
            res.posts = cached.get("posts", [])
            res.post_dates = cached.get("post_dates", [])
            res.from_cache = True
            return res

        async with self._sem:
            await self._pace()
            page = await self._context.new_page()
            try:
                await self._goto(page, f"{url}/about", res)
                if res.status != "ok":
                    return res
                about_html = await page.content()
                about_text = await self._inner_text(page)

                posts_html, posts, dates = "", [], []
                if want_posts:
                    await self._pace()
                    await self._goto(page, url, res)
                    if res.status == "ok":
                        await self._scroll(page)
                        posts_html = await page.content()
                        posts, dates = await self._collect_posts(page)

                res.html = about_html + "\n<!--POSTS-->\n" + posts_html
                res.text = about_text
                res.posts, res.post_dates = posts, dates
                if self.use_cache and res.status == "ok":
                    self.cache.put(url, {
                        "html": res.html, "text": res.text,
                        "posts": res.posts, "post_dates": res.post_dates,
                    })
            finally:
                try:
                    await page.close()
                except PWError:
                    pass
        return res

    async def _goto(self, page, url: str, res: PageFetch) -> None:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        except PWTimeout:
            res.status = "timeout"
            return
        except PWError as e:
            res.status = f"error:{type(e).__name__}"
            return
        await self._dismiss_consent(page)
        try:
            await page.wait_for_timeout(1200)
        except PWError:
            pass
        landed = page.url
        if any(marker in landed for marker in _BLOCK_MARKERS):
            self.blocked_count += 1
            res.status = "login_wall"

    async def _inner_text(self, page) -> str:
        try:
            return await page.evaluate("() => document.body ? document.body.innerText : ''")
        except PWError:
            return ""

    async def _scroll(self, page) -> None:
        for _ in range(self.scrolls):
            try:
                await page.mouse.wheel(0, 2400)
                await page.wait_for_timeout(random.randint(900, 1900))
            except PWError:
                break

    async def _collect_posts(self, page) -> tuple[list[str], list[str]]:
        """Post bodies and their timestamp labels, read from the rendered feed."""
        try:
            return await page.evaluate(
                """() => {
                    const arts = Array.from(document.querySelectorAll('[role="article"]'));
                    const texts = [], dates = [];
                    // Logged out, Facebook renders its login prompt as an article too.
                    const junk = /(se connecter|log in to facebook|create new account|cr\u00e9er un nouveau compte|mot de passe oubli|forgotten password)/i;
                    for (const a of arts.slice(0, 40)) {
                        const t = (a.innerText || '').trim();
                        if (t.length > 15 && !junk.test(t.slice(0, 200))) texts.push(t.slice(0, 4000));
                        const link = a.querySelector(
                            'a[href*="/posts/"], a[href*="story_fbid"], a[href*="/videos/"]');
                        if (link) {
                            const lbl = (link.getAttribute('aria-label')
                                      || link.innerText || '').trim();
                            if (lbl) dates.push(lbl.slice(0, 60));
                        }
                    }
                    return [texts, dates];
                }"""
            )
        except PWError:
            return [], []

    # -- one-time interactive login ---------------------------------------

    async def login_interactive(self, wait_seconds: int = 300) -> bool:
        """Open a real window, let the operator sign in, then save the session."""
        page = await self._context.new_page()
        await page.goto(FB, wait_until="domcontentloaded")
        await self._dismiss_consent(page)
        print(f"  A browser window is open. Log in to Facebook there.")
        print(f"  Waiting up to {wait_seconds}s; this session is saved once you land on the feed.")
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            await page.wait_for_timeout(2500)
            try:
                cookies = {c["name"] for c in await self._context.cookies()}
            except PWError:
                continue
            if "c_user" in cookies:
                await self._context.storage_state(path=str(self.session_path))
                await page.close()
                return True
        await page.close()
        return False
