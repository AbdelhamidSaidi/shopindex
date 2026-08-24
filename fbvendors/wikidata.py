"""Wikidata as a discovery source.

Moroccan entities carrying a Facebook ID (P2013). The SPARQL endpoint is free
and unmetered, so this costs one query. Wikidata skews institutional, so
non-commercial entity types are filtered out to keep the table about vendors.
"""

from __future__ import annotations

import re

import httpx

from . import geo
from .normalize import clean_url, normalize_phone, normalize_slug
from .discover import is_vendor_slug

ENDPOINT = "https://query.wikidata.org/sparql"
UA = "fbvendors/0.1 (business research)"

QUERY = """
SELECT ?item ?itemLabel ?fb ?website ?phone ?cityLabel ?typeLabel WHERE {
  ?item wdt:P17 wd:Q1028 .
  ?item wdt:P2013 ?fb .
  OPTIONAL { ?item wdt:P856 ?website. }
  OPTIONAL { ?item wdt:P1329 ?phone. }
  OPTIONAL { ?item wdt:P131 ?city. }
  OPTIONAL { ?item wdt:P31 ?type. }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "fr,en,ar". }
}
LIMIT 5000
"""

# Entity types that are not vendors or providers in any useful sense.
_EXCLUDE = re.compile(
    r"(ministère|ministry|parti politique|political party|club de football|football club|"
    r"équipe|commune|province|préfecture|région|ville|city|town|village|municipalit|"
    r"parlement|ambassade|embassy|organisation internationale|aéroport|airport|"
    r"stade|stadium|mosquée|mosque|monument|musée|museum|site archéologique|"
    r"personnalité|human|être humain|chaîne de télévision|station de radio)",
    re.IGNORECASE,
)


def fetch(timeout: float = 120.0) -> list[dict]:
    r = httpx.get(
        ENDPOINT,
        params={"query": QUERY, "format": "json"},
        headers={"User-Agent": UA, "Accept": "application/sparql-results+json"},
        timeout=timeout,
        follow_redirects=True,
    )
    r.raise_for_status()
    return r.json()["results"]["bindings"]


def parse(rows: list[dict]) -> list[dict]:
    """Collapse the one-row-per-type result set into one record per entity."""
    by_fb: dict[str, dict] = {}
    for row in rows:
        def val(key: str) -> str:
            return (row.get(key) or {}).get("value", "").strip()

        fb_raw = val("fb")
        slug = normalize_slug(fb_raw) if "facebook.com" in fb_raw else fb_raw
        if not slug or not is_vendor_slug(slug):
            continue

        rec = by_fb.setdefault(slug, {
            "slug": slug, "name": val("itemLabel"), "website": clean_url(val("website")),
            "phone": normalize_phone(val("phone")), "city": "", "region": "", "types": set(),
        })
        if val("typeLabel"):
            rec["types"].add(val("typeLabel"))
        for field, key in (("website", "website"), ("phone", "phone")):
            if not rec[field] and val(key):
                rec[field] = clean_url(val(key)) if field == "website" else normalize_phone(val(key))
        if not rec["city"]:
            rec["city"], rec["region"] = geo.lookup_city(
                " | ".join(filter(None, [val("cityLabel"), rec["name"]]))
            )

    out = []
    for rec in by_fb.values():
        types = " / ".join(sorted(rec.pop("types")))
        if _EXCLUDE.search(types) or _EXCLUDE.search(rec["name"]):
            continue
        rec["types"] = types
        out.append(rec)
    return out
