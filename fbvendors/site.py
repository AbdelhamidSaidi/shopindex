"""Scraping Moroccan vendor websites directly.

Facebook rate-limits and hides content behind a login; ordinary websites do
neither, and there are far more of them. Each site is a different host, so this
runs at real concurrency without being rude to anyone.
"""

from __future__ import annotations

import asyncio
import re
import urllib.parse as up
from dataclasses import dataclass, field

import httpx
from bs4 import BeautifulSoup

from . import geo, taxonomy
from .kinds import classify_kind, is_moroccan, is_parked, salient, ships_morocco
from .models import Vendor
from .normalize import (
    clean_name, clean_url, find_emails, find_phones, normalize_slug, root_host, whatsapp_from_url,
)
from .price import aggregate
from .score import apply_score

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " \
     "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"

# Pages that carry the contact block, tried after the homepage.
CONTACT_PATHS = ("/contact", "/contact-us", "/contactez-nous", "/nous-contacter",
                 "/a-propos", "/about", "/qui-sommes-nous")

_MA_LINK = re.compile(r"https?://([a-z0-9\-]+\.)*[a-z0-9\-]+\.ma(?:/|$)", re.I)


@dataclass
class SiteResult:
    url: str
    status: str = "ok"
    html: str = ""
    text: str = ""
    title: str = ""
    description: str = ""
    headings: str = ""
    outbound_ma: list[str] = field(default_factory=list)


async def fetch_site(client: httpx.AsyncClient, url: str,
                     follow_contact: bool = True) -> SiteResult:
    res = SiteResult(url=url)
    chunks_html, chunks_text = [], []
    try:
        r = await client.get(url)
    except (httpx.HTTPError, httpx.InvalidURL, UnicodeDecodeError) as e:
        res.status = f"error:{type(e).__name__}"
        return res
    if r.status_code != 200:
        res.status = f"http:{r.status_code}"
        return res

    chunks_html.append(r.text)
    soup = BeautifulSoup(r.text, "html.parser")
    chunks_text.append(soup.get_text(" ", strip=True))
    res.title = (soup.title.get_text() if soup.title else "").strip()
    desc = soup.find("meta", attrs={"name": "description"}) or \
        soup.find("meta", attrs={"property": "og:description"})
    res.description = (desc.get("content") or "").strip() if desc else ""
    res.headings = " | ".join(h.get_text(" ", strip=True)
                              for h in soup.find_all(["h1", "h2"])[:25])

    # Moroccan sites almost always put the phone on a contact page, not the home page.
    if follow_contact:
        base = f"{up.urlsplit(url).scheme}://{up.urlsplit(url).netloc}"
        hrefs = {a["href"] for a in soup.find_all("a", href=True)}
        targets = []
        for h in hrefs:
            path = up.urlsplit(h).path.lower().rstrip("/")
            if any(path.endswith(c) for c in CONTACT_PATHS):
                targets.append(h if h.startswith("http") else up.urljoin(base, h))
        if not targets:
            targets = [base + CONTACT_PATHS[0]]
        for t in targets[:2]:
            try:
                r2 = await client.get(t)
                if r2.status_code == 200:
                    chunks_html.append(r2.text)
                    chunks_text.append(BeautifulSoup(r2.text, "html.parser")
                                       .get_text(" ", strip=True))
            except (httpx.HTTPError, httpx.InvalidURL, UnicodeDecodeError):
                continue

    res.html = "\n".join(chunks_html)
    res.text = "\n".join(chunks_text)
    res.outbound_ma = sorted({m.group(0).rstrip("/") for m in _MA_LINK.finditer(res.html)})
    return res


def parse_site(res: SiteResult, *, hint_name: str = "", hint_city: str = "",
               hint_category: str = "") -> Vendor:
    v = Vendor(source=root_host(res.url) or "web")
    v.website = clean_url(res.url)
    v.fetch_status = res.status
    soup = BeautifulSoup(res.html or "", "html.parser")

    meta = {(t.get("property") or t.get("name") or "").lower(): (t.get("content") or "")
            for t in soup.find_all("meta")}
    v.name = clean_name(meta.get("og:site_name") or meta.get("og:title")
                        or (soup.title.get_text() if soup.title else "") or hint_name)
    if not v.name:
        v.name = hint_name or root_host(res.url)
    v.about = " ".join((meta.get("description") or meta.get("og:description") or "").split())[:400]

    text = res.text or ""
    phones = find_phones(text) or find_phones(res.html[:300_000])
    v.all_phones = phones
    v.phone = phones[0] if phones else ""
    emails = find_emails(text) or find_emails(res.html[:300_000])
    v.email = emails[0] if emails else ""

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not v.whatsapp:
            v.whatsapp = whatsapp_from_url(href)
        if not v.facebook_url and "facebook.com" in href:
            slug = normalize_slug(href)
            if slug:
                v.facebook_url = f"https://www.facebook.com/{slug}"
                v.slug = slug

    v.city, v.region = geo.lookup_city(" | ".join([text[:6000], hint_city, v.name]))
    v.location = geo.format_location(v.city, v.region, hint_city)

    v.category, _ = taxonomy.classify(hint_category, v.name, v.about + " " + text[:4000])
    headline = salient(res.title, res.description, res.headings, "")
    kind, sells, conf = classify_kind(headline, text[:8000], res.html[:400_000])
    v.kind = kind
    v.has_shop = sells
    if conf < 0.5:
        v.notes.append(f"kind confidence {conf}")
    v.delivers = ships_morocco(text)

    stats = aggregate([text[:120_000]])
    summary = stats.summary()
    if summary:
        v.price_min = float(summary["min"]); v.price_max = float(summary["max"])
        v.price_median = float(summary["median"]); v.price_count = int(summary["count"])
        v.currency = str(summary["currency"])
    v.price_signal = stats.signal()

    v.stamp()
    return apply_score(v)


async def crawl_sites(urls: list[str], concurrency: int = 20, timeout: float = 20.0,
                      progress=None) -> tuple[list[Vendor], set[str]]:
    """Fetch and parse many sites. Returns (vendors, newly seen .ma domains)."""
    sem = asyncio.Semaphore(concurrency)
    out: list[Vendor] = []
    discovered: set[str] = set()
    done = 0

    async def one(client: httpx.AsyncClient, url: str) -> None:
        nonlocal done
        async with sem:
            res = await fetch_site(client, url)
            if res.status == "ok":
                # Parked/for-sale domains and non-Moroccan sites are not vendors.
                if is_parked(res.text, res.title):
                    discovered.update(res.outbound_ma)
                    done_inc()
                    return
                v = parse_site(res)
                host = root_host(url)
                if v.name and is_moroccan(res.text, host, v.phone):
                    v.notes.append(res.text[:1500])   # kept for offline re-classification
                    out.append(v)
                discovered.update(res.outbound_ma)
            done_inc()

    def done_inc():
        nonlocal done
        done += 1
        if progress and done % 50 == 0:
            progress(done, len(urls), len(out))

    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=True,
        headers={"User-Agent": UA, "Accept-Language": "fr-FR,fr;q=0.9,ar;q=0.8"},
        limits=httpx.Limits(max_connections=concurrency * 2),
    ) as client:
        await asyncio.gather(*(one(client, u) for u in urls))
    return out, discovered
