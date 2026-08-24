"""OpenStreetMap as a discovery source.

Search engines rate-limit bulk querying; Overpass does not. OSM carries Moroccan
businesses tagged with `contact:facebook` outright, and a much larger set tagged
with a `website` -- and Moroccan business sites very often link their own Page.
Those sites are thousands of *different* hosts, so fetching them has none of the
single-endpoint rate-limit problem that kills search-engine discovery.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from . import geo
from .discover import extract_page_slugs, is_vendor_slug
from .normalize import clean_url, normalize_phone, normalize_slug

UA = "fbvendors/0.1 (business research; https://github.com/local)"

ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
)

# Tags that mark a feature as a business rather than a road or a boundary.
BUSINESS_KEYS = ("shop", "office", "craft", "amenity", "tourism", "healthcare", "industrial")

FB_QUERY = """
[out:json][timeout:300];
area["ISO3166-1"="MA"][admin_level=2]->.ma;
(
  nwr(area.ma)["contact:facebook"];
  nwr(area.ma)["facebook"];
);
out center tags;
"""

SITES_QUERY = """
[out:json][timeout:600];
area["ISO3166-1"="MA"][admin_level=2]->.ma;
(
  nwr(area.ma)["website"][~"^(shop|office|craft|amenity|tourism|healthcare|industrial)$"~"."];
  nwr(area.ma)["contact:website"][~"^(shop|office|craft|amenity|tourism|healthcare|industrial)$"~"."];
);
out center tags;
"""

# OSM tag -> the project taxonomy.
OSM_CATEGORY = {
    "bakery": "Food & Beverage", "butcher": "Food & Beverage", "cheese": "Food & Beverage",
    "confectionery": "Food & Beverage", "greengrocer": "Food & Beverage",
    "supermarket": "Retail & General Trade", "convenience": "Retail & General Trade",
    "restaurant": "Food & Beverage", "cafe": "Food & Beverage", "fast_food": "Food & Beverage",
    "bar": "Food & Beverage", "pub": "Food & Beverage", "ice_cream": "Food & Beverage",
    "clothes": "Apparel & Textiles", "boutique": "Apparel & Textiles",
    "fabric": "Apparel & Textiles", "tailor": "Apparel & Textiles",
    "shoes": "Footwear & Leather", "bag": "Footwear & Leather", "leather": "Footwear & Leather",
    "hairdresser": "Health & Beauty", "beauty": "Health & Beauty", "cosmetics": "Health & Beauty",
    "perfumery": "Health & Beauty", "pharmacy": "Health & Beauty", "chemist": "Health & Beauty",
    "doctors": "Health & Medical", "dentist": "Health & Medical", "clinic": "Health & Medical",
    "hospital": "Health & Medical", "optician": "Health & Medical",
    "hotel": "Travel & Hospitality", "guest_house": "Travel & Hospitality",
    "hostel": "Travel & Hospitality", "motel": "Travel & Hospitality",
    "camp_site": "Travel & Hospitality", "travel_agency": "Travel & Hospitality",
    "apartment": "Travel & Hospitality", "chalet": "Travel & Hospitality",
    "car": "Auto & Transport", "car_repair": "Auto & Transport", "car_parts": "Auto & Transport",
    "car_rental": "Auto & Transport", "motorcycle": "Auto & Transport",
    "tyres": "Auto & Transport", "bicycle": "Auto & Transport", "fuel": "Energy & Environment",
    "furniture": "Home & Furniture", "interior_decoration": "Home & Furniture",
    "kitchen": "Home & Furniture", "houseware": "Home & Furniture", "bed": "Home & Furniture",
    "doityourself": "Construction & Real Estate", "hardware": "Construction & Real Estate",
    "building_materials": "Construction & Real Estate", "paint": "Construction & Real Estate",
    "estate_agent": "Construction & Real Estate", "trade": "Construction & Real Estate",
    "electronics": "Electronics & IT", "computer": "Electronics & IT",
    "mobile_phone": "Electronics & IT", "hifi": "Electronics & IT",
    "it": "Electronics & IT", "telecommunication": "Electronics & IT",
    "stationery": "Office Supplies", "books": "Office Supplies", "copyshop": "Office Supplies",
    "newsagent": "Office Supplies",
    "jewelry": "Arts, Crafts & Gifts", "art": "Arts, Crafts & Gifts",
    "craft": "Arts, Crafts & Gifts", "pottery": "Arts, Crafts & Gifts",
    "carpet": "Arts, Crafts & Gifts", "gift": "Arts, Crafts & Gifts",
    "antiques": "Arts, Crafts & Gifts", "handicraft": "Arts, Crafts & Gifts",
    "school": "Business Services", "university": "Business Services",
    "college": "Business Services", "language_school": "Business Services",
    "driving_school": "Business Services", "bank": "Business Services",
    "insurance": "Business Services", "lawyer": "Business Services",
    "accountant": "Business Services", "company": "Business Services",
    "consulting": "Business Services", "advertising_agency": "Packaging & Printing",
    "printer": "Packaging & Printing", "printing": "Packaging & Printing",
    "gym": "Sports & Leisure", "fitness_centre": "Sports & Leisure",
    "sports": "Sports & Leisure", "toys": "Sports & Leisure",
    "florist": "Agriculture", "garden_centre": "Agriculture", "agrarian": "Agriculture",
    "farm": "Agriculture",
}


@dataclass
class OsmBusiness:
    osm_id: str = ""
    name: str = ""
    category: str = ""
    raw_tag: str = ""
    city: str = ""
    region: str = ""
    address: str = ""
    phone: str = ""
    website: str = ""
    facebook_slug: str = ""
    lat: float | None = None
    lon: float | None = None
    tags: dict[str, str] = field(default_factory=dict)


async def query_overpass(query: str, timeout: float = 620.0) -> dict:
    """Run an Overpass query, trying mirrors in turn."""
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": UA}) as client:
        last = ""
        for endpoint in ENDPOINTS:
            try:
                r = await client.post(endpoint, content=query.encode(),
                                      headers={"Content-Type": "text/plain"})
            except httpx.HTTPError as e:
                last = f"{type(e).__name__} at {endpoint}"
                continue
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError:
                    last = f"bad JSON from {endpoint}"
                    continue
            last = f"HTTP {r.status_code} from {endpoint}"
        raise RuntimeError(f"all Overpass endpoints failed ({last})")


def _address(tags: dict[str, str]) -> str:
    parts = [
        " ".join(filter(None, [tags.get("addr:housenumber"), tags.get("addr:street")])),
        tags.get("addr:city") or tags.get("addr:town") or tags.get("addr:village") or "",
        tags.get("addr:postcode") or "",
    ]
    return ", ".join(p for p in parts if p).strip(", ")


def parse_elements(payload: dict) -> list[OsmBusiness]:
    out: list[OsmBusiness] = []
    for el in payload.get("elements", []):
        tags = el.get("tags") or {}
        name = (tags.get("name:fr") or tags.get("name") or tags.get("name:en")
                or tags.get("name:ar") or "").strip()
        if not name:
            continue

        raw_tag = ""
        for key in BUSINESS_KEYS:
            if tags.get(key):
                raw_tag = f"{key}={tags[key]}"
                break

        fb_raw = tags.get("contact:facebook") or tags.get("facebook") or ""
        fb_slug = normalize_slug(fb_raw) if "facebook.com" in fb_raw else ""
        if not fb_slug and fb_raw and "/" not in fb_raw and is_vendor_slug(fb_raw):
            fb_slug = fb_raw   # bare handle, e.g. "salonbleutanger"

        city_src = " | ".join(filter(None, [
            tags.get("addr:city", ""), tags.get("addr:town", ""), tags.get("addr:village", ""),
            _address(tags), name,
        ]))
        city, region = geo.lookup_city(city_src)

        b = OsmBusiness(
            osm_id=f"{el.get('type','')}/{el.get('id','')}",
            name=name,
            raw_tag=raw_tag,
            category=OSM_CATEGORY.get(raw_tag.split("=", 1)[-1], ""),
            city=city,
            region=region,
            address=_address(tags),
            phone=normalize_phone(tags.get("contact:phone") or tags.get("phone") or ""),
            website=clean_url(tags.get("website") or tags.get("contact:website") or ""),
            facebook_slug=fb_slug,
            lat=el.get("lat") or (el.get("center") or {}).get("lat"),
            lon=el.get("lon") or (el.get("center") or {}).get("lon"),
            tags=tags,
        )
        out.append(b)
    return out


# Facebook pages belonging to platforms, themes and partners rather than to the
# business whose site links them.
PLATFORM_SLUGS = {
    "airbnb", "airbnbfrance", "booking", "bookingcom", "tripadvisor", "expedia",
    "qodeinteractive", "envato", "themeforest", "wordpress", "wix", "shopify",
    "woocommerce", "elementor", "google", "meta", "instagram", "whatsapp",
    "youtube", "tiktok", "twitter", "linkedin", "pinterest", "orange", "inwi",
    "maroctelecom", "amorino", "security", "public", "government-nonprofits",
    "gettyimages", "mailchimp", "stripe", "paypal", "visa", "mastercard",
    "orangemaroc", "amorinogelato", "airbnbfrance", "qodeinteractive",
}


def _slug_score(slug: str, business_name: str) -> float:
    """How much a Facebook slug looks like it belongs to this business."""
    s_norm = re.sub(r"[^a-z0-9]", "", slug.lower())
    if not s_norm or slug.lower() in PLATFORM_SLUGS:
        return -1.0
    if slug.isdigit():
        return 0.30            # numeric page ids are anonymous but usually genuine
    n_norm = re.sub(r"[^a-z0-9]", "", (business_name or "").lower())
    if not n_norm:
        return 0.25
    if n_norm in s_norm or s_norm in n_norm:
        return 1.0
    ratio = difflib.SequenceMatcher(None, s_norm, n_norm).ratio()
    # Any shared distinctive word is strong evidence.
    words = {w for w in re.split(r"[^a-z0-9]+", (business_name or "").lower()) if len(w) > 3}
    if any(w in s_norm for w in words):
        ratio = max(ratio, 0.75)
    return ratio


def pick_best_slug(slugs: list[str], business_name: str, host: str = "") -> str:
    """Choose the Facebook page most likely to be this business's own."""
    best, best_score = "", 0.0
    host_root = re.sub(r"[^a-z0-9]", "", (host or "").split(".")[0].lower())
    for slug in slugs:
        score = _slug_score(slug, business_name)
        if host_root and re.sub(r"[^a-z0-9]", "", slug.lower()).find(host_root) >= 0:
            score = max(score, 0.9)     # slug matches the site's own domain
        if score > best_score:
            best, best_score = slug, score
    return best if best_score >= 0.30 else ""


async def harvest_facebook_from_sites(
    businesses: list[OsmBusiness],
    concurrency: int = 16,
    timeout: float = 15.0,
    progress=None,
) -> list[OsmBusiness]:
    """Fetch each business website and pull the Facebook Page it links to.

    These are thousands of separate hosts, so concurrency here is not the rude
    kind - no single server sees more than one or two requests.
    """
    targets = [b for b in businesses if b.website and not b.facebook_slug]
    sem = asyncio.Semaphore(concurrency)
    done = 0
    found = 0

    async def one(client: httpx.AsyncClient, b: OsmBusiness) -> None:
        nonlocal done, found
        async with sem:
            try:
                r = await client.get(b.website)
                if r.status_code == 200:
                    slugs = extract_page_slugs(r.text)
                    host = httpx.URL(b.website).host or ""
                    chosen = pick_best_slug(slugs, b.name, host)
                    if chosen:
                        b.facebook_slug = chosen
                        found += 1
            except (httpx.HTTPError, httpx.InvalidURL, UnicodeDecodeError):
                pass
            finally:
                done += 1
                if progress and done % 25 == 0:
                    progress(done, len(targets), found)

    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (compatible; fbvendors/0.1)"},
        limits=httpx.Limits(max_connections=concurrency * 2),
    ) as client:
        await asyncio.gather(*(one(client, b) for b in targets))
    return businesses


def save(businesses: list[OsmBusiness], path: Path | str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {k: v for k, v in b.__dict__.items() if k != "tags"} for b in businesses
    ]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return path
