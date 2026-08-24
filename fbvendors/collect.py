"""End-to-end collection flows.

Each function here is one reproducible stage of the pipeline and is exposed as a
CLI subcommand. Every stage writes its raw payload to `data/` so later stages can
re-run without repeating the network work.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import httpx
from rich.console import Console

from . import osm as osm_mod
from . import wikidata as wd_mod
from .normalize import clean_url, root_host
from .site import crawl_sites
from .store import Store

console = Console()
DATA = Path("data")

MAJESTIC_URL = "https://downloads.majestic.com/majestic_million.csv"


# --------------------------------------------------------------------------
# stage 1 - OpenStreetMap
# --------------------------------------------------------------------------

SUPPLIER_QUERY = """
[out:json][timeout:600];
area["ISO3166-1"="MA"][admin_level=2]->.ma;
(
  nwr(area.ma)["craft"]["name"];
  nwr(area.ma)["shop"="wholesale"]["name"];
  nwr(area.ma)["shop"="trade"]["name"];
  nwr(area.ma)["shop"="agrarian"]["name"];
  nwr(area.ma)["office"="company"]["name"];
  nwr(area.ma)["man_made"="works"]["name"];
  nwr(area.ma)["industrial"]["name"];
  nwr(area.ma)["landuse"="industrial"]["name"];
);
out center tags;
"""

OSM_QUERIES = {
    "osm_raw.json": osm_mod.FB_QUERY,          # already tagged with a Facebook page
    "osm_sites.json": osm_mod.SITES_QUERY,     # businesses that publish a website
    "osm_suppliers.json": SUPPLIER_QUERY,      # craft / industrial / wholesale
}


async def collect_osm(db: str, refresh: bool = False, concurrency: int = 16) -> int:
    """Query Overpass, harvest Facebook links off the websites, queue the pages."""
    DATA.mkdir(parents=True, exist_ok=True)
    merged: dict[str, osm_mod.OsmBusiness] = {}

    for filename, query in OSM_QUERIES.items():
        path = DATA / filename
        if refresh or not path.exists():
            console.print(f"[cyan]Overpass[/] → {filename}")
            payload = await osm_mod.query_overpass(query)
            path.write_text(json.dumps(payload), encoding="utf-8")
        else:
            console.print(f"[dim]reusing {filename}[/]")
            payload = json.loads(path.read_text(encoding="utf-8"))

        for b in osm_mod.parse_elements(payload):
            prev = merged.get(b.osm_id)
            if prev is None:
                merged[b.osm_id] = b
            else:
                for f in ("facebook_slug", "website", "phone", "city", "category", "address"):
                    if not getattr(prev, f) and getattr(b, f):
                        setattr(prev, f, getattr(b, f))

    businesses = list(merged.values())
    tagged = sum(1 for b in businesses if b.facebook_slug)
    console.print(f"{len(businesses)} businesses, {tagged} already Facebook-tagged")

    def progress(done, total, found):
        console.print(f"  [dim]{done}/{total} sites, {found} pages[/]")

    await osm_mod.harvest_facebook_from_sites(businesses, concurrency=concurrency,
                                              progress=progress)
    osm_mod.save(businesses, DATA / "osm_businesses.json")

    added = 0
    with Store(db) as st:
        for b in businesses:
            if b.facebook_slug:
                hint = " | ".join(filter(None, [b.name, b.city, b.raw_tag, b.address]))
                if st.enqueue(b.facebook_slug, source="osm", hint=hint[:120]):
                    added += 1
    total = sum(1 for b in businesses if b.facebook_slug)
    console.print(f"[green]{total}[/] Facebook pages found, [green]+{added}[/] newly queued")
    return added


# --------------------------------------------------------------------------
# stage 2 - Wikidata
# --------------------------------------------------------------------------

def collect_wikidata(db: str, refresh: bool = False) -> int:
    path = DATA / "wikidata.json"
    if refresh or not path.exists():
        console.print("[cyan]Wikidata SPARQL[/] …")
        rows = wd_mod.fetch()
        path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    else:
        console.print("[dim]reusing wikidata.json[/]")
        rows = json.loads(path.read_text(encoding="utf-8"))
    recs = wd_mod.parse(rows)
    added = 0
    with Store(db) as st:
        for r in recs:
            hint = " | ".join(filter(None, [r["name"], r["city"], r["types"]]))
            if st.enqueue(r["slug"], source="wikidata", hint=hint[:120]):
                added += 1
    console.print(f"[green]{len(recs)}[/] entities, [green]+{added}[/] newly queued")
    return added


# --------------------------------------------------------------------------
# stage 3 - vendor websites
# --------------------------------------------------------------------------

def download_majestic(refresh: bool = False) -> list[str]:
    """Top-1M ranking list, filtered to .ma — a free source of Moroccan domains."""
    path = DATA / "ma_domains_majestic.txt"
    if path.exists() and not refresh:
        return [d for d in path.read_text(encoding="utf-8").split("\n") if d]
    console.print("[cyan]Majestic Million[/] (~80 MB) …")
    try:
        r = httpx.get(MAJESTIC_URL, timeout=300, follow_redirects=True,
                      headers={"User-Agent": "fbvendors/0.1"})
        r.raise_for_status()
    except httpx.HTTPError as e:
        console.print(f"[yellow]Majestic unavailable ({type(e).__name__}); skipping[/]")
        return []
    rows = list(csv.DictReader(io.StringIO(r.text)))
    col = "Domain" if rows and "Domain" in rows[0] else list(rows[0])[2]
    ma = [x[col] for x in rows if x[col].endswith(".ma") and not x[col].startswith("google.")]
    path.write_text("\n".join(ma), encoding="utf-8")
    console.print(f"  {len(ma)} .ma domains")
    return ma


def build_site_seeds(db: str, refresh_majestic: bool = False) -> list[str]:
    """Every Moroccan website we know of, one entry per host."""
    seeds: set[str] = set()

    for name in ("osm_businesses.json", "osm_reharvested.json", "osm_sites.json"):
        p = DATA / name
        if not p.exists():
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        items = data if isinstance(data, list) else \
            [b.__dict__ for b in osm_mod.parse_elements(data)]
        for b in items:
            u = clean_url(b.get("website") or "")
            if u:
                seeds.add(u)

    for d in download_majestic(refresh_majestic):
        seeds.add(f"https://{d}/")

    if Path(db).exists():
        with Store(db) as st:
            for (payload,) in st.db.execute("SELECT payload FROM pages"):
                u = clean_url(json.loads(payload).get("website") or "")
                if u:
                    seeds.add(u)

    by_host: dict[str, str] = {}
    for u in sorted(seeds):
        h = root_host(u)
        if h and h not in by_host:
            by_host[h] = u
    out = sorted(by_host.values())
    (DATA / "site_seeds.txt").write_text("\n".join(out), encoding="utf-8")
    return out


async def collect_sites(db: str, rounds: int = 4, concurrency: int = 24,
                        max_new_per_round: int = 2500,
                        refresh_majestic: bool = False) -> int:
    """Crawl vendor websites, snowballing through outbound .ma links."""
    import dataclasses

    seeds = build_site_seeds(db, refresh_majestic)
    console.print(f"[cyan]Seeds:[/] {len(seeds)} hosts")
    seen = {root_host(u) for u in seeds}
    all_vendors = []
    queue = seeds

    def progress(done, total, kept):
        console.print(f"  [dim]{done}/{total} fetched, {kept} vendors[/]")

    for rnd in range(1, rounds + 1):
        console.print(f"[cyan]round {rnd}[/]: {len(queue)} sites")
        vendors, discovered = await crawl_sites(queue, concurrency=concurrency,
                                                progress=progress)
        all_vendors.extend(vendors)
        console.print(f"  round {rnd}: {len(vendors)} vendors, {len(discovered)} outbound links")

        nxt = []
        for url in sorted(discovered):
            h = root_host(url)
            if h and h not in seen:
                seen.add(h)
                nxt.append(f"https://{h}/")
                if len(nxt) >= max_new_per_round:
                    break
        # Snapshot every round so a crash never loses the work.
        (DATA / "site_vendors.json").write_text(
            json.dumps([dataclasses.asdict(v) for v in all_vendors],
                       ensure_ascii=False, default=str), encoding="utf-8")
        if not nxt:
            break
        queue = nxt

    console.print(f"[green]{len(all_vendors)}[/] vendors from websites")
    return len(all_vendors)
