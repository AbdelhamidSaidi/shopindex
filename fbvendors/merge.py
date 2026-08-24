"""Merging website vendors with Facebook pages into one table."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from .models import Vendor
from .normalize import root_host
from .score import apply_score
from .store import Store
from .supplier import classify_dropship

DATA = Path("data")

# Kinds that describe something you can buy goods from.
SELLING_KINDS = {"b2b supplier", "manufacturer", "producer", "reseller",
                 "online shop", "vendor site", "marketplace"}

_FIELDS = {f.name for f in dataclasses.fields(Vendor)}

MERGE_FIELDS = ["name", "category", "location", "city", "region", "website", "phone",
                "whatsapp", "email", "address", "price_signal", "price_min", "price_max",
                "price_median", "price_count", "currency", "followers", "likes", "rating",
                "reviews", "last_post_date", "about", "facebook_url", "page_id", "slug",
                "raw_category"]


def _load_sites(path: Path) -> list[Vendor]:
    if not path.exists():
        return []
    return [Vendor(**{k: v for k, v in d.items() if k in _FIELDS})
            for d in json.loads(path.read_text(encoding="utf-8"))]


def _load_facebook(db: str) -> list[Vendor]:
    if not Path(db).exists():
        return []
    out = []
    with Store(db) as st:
        for (payload,) in st.db.execute("SELECT payload FROM pages WHERE status='ok'"):
            d = json.loads(payload)
            v = Vendor(**{k: val for k, val in d.items() if k in _FIELDS})
            v.kind = v.kind or "facebook page"
            out.append(v)
    return out


def _keys(v: Vendor) -> list[str]:
    ks = []
    if v.website:
        h = root_host(v.website)
        if h:
            ks.append("web:" + h)
    if v.facebook_url:
        ks.append("fb:" + v.facebook_url.lower().rstrip("/"))
    if v.page_id:
        ks.append("pid:" + v.page_id)
    return ks


def _absorb(into: Vendor, other: Vendor) -> None:
    for f in MERGE_FIELDS:
        if not getattr(into, f) and getattr(other, f):
            setattr(into, f, getattr(other, f))
    into.has_shop = into.has_shop or other.has_shop
    into.delivers = into.delivers or other.delivers
    into.verified = into.verified or other.verified
    # A specific website kind beats the generic facebook label.
    if other.kind and (not into.kind or into.kind == "facebook page"):
        into.kind = other.kind


def merge_all(db: str, sites_path: Path | str = DATA / "site_vendors.json") -> list[Vendor]:
    """One row per business, website data taking precedence over Facebook."""
    index: dict[str, Vendor] = {}
    merged: list[Vendor] = []
    for v in _load_sites(Path(sites_path)) + _load_facebook(db):
        hit = next((index[k] for k in _keys(v) if k in index), None)
        if hit is None:
            merged.append(v)
            for k in _keys(v):
                index.setdefault(k, v)
        else:
            _absorb(hit, v)
            for k in _keys(hit):
                index.setdefault(k, hit)
    for v in merged:
        apply_score(v)
    merged.sort(key=lambda v: -float(v.score or 0))
    return merged


def sells(v: Vendor) -> bool:
    """Whether this row can supply goods."""
    if v.kind in SELLING_KINDS:
        return True
    if v.kind == "facebook page":
        # Scraped anonymously there is no post text, so judge on category/wording.
        ok, _c, _w = classify_dropship(
            name=v.name, category=v.category, raw_category=v.raw_category,
            about=v.about, price_signal=v.price_signal, has_shop=v.has_shop,
            delivers=v.delivers, website=v.website)
        return ok
    return False


def reachable(v: Vendor) -> bool:
    return bool(v.phone or v.email or v.website)
