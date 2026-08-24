"""Command line entry point: login / discover / scrape / export / status / run."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from . import discover as disc
from .fetch import Fetcher
from .pipeline import Pipeline
from .merge import merge_all, reachable, sells
from .store import Store, write_table
from .taxonomy import CATEGORIES

console = Console()
DEFAULT_DB = "data/state.sqlite3"
DEFAULT_SESSION = "fb_session.json"


def _fetcher(args) -> Fetcher:
    return Fetcher(
        session_path=args.session,
        cache_dir=args.cache_dir,
        headless=not args.show_browser,
        concurrency=args.concurrency,
        delay=(args.min_delay, args.max_delay),
        scrolls=args.scrolls,
        use_cache=not args.no_cache,
    )


def _queries_from_args(args) -> list[str]:
    if getattr(args, "preset", None) == "suppliers":
        from .supplier import build_supplier_queries
        return build_supplier_queries(cities=args.city or None)
    if getattr(args, "preset", None) == "dropship":
        from .supplier import build_dropship_queries
        return build_dropship_queries(cities=args.city or None)
    if args.query:
        return list(args.query)
    cats = args.category or None
    if cats:
        unknown = [c for c in cats if c not in CATEGORIES]
        if unknown:
            console.print(f"[red]Unknown category:[/] {', '.join(unknown)}")
            console.print(f"[dim]Available: {', '.join(CATEGORIES)}[/]")
            raise SystemExit(2)
    return disc.build_queries(
        categories=cats,
        cities=args.city or None,
        extra_terms=args.term or None,
        per_category=args.per_category,
    )


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

async def cmd_login(args) -> int:
    async with Fetcher(session_path=args.session, headless=False) as f:
        ok = await f.login_interactive(wait_seconds=args.wait)
    if ok:
        console.print(f"[green]Session saved[/] to {args.session}")
        return 0
    console.print("[red]Login not detected.[/] Nothing was saved.")
    return 1


async def cmd_discover(args) -> int:
    with Store(args.db) as store:
        pipe = Pipeline(store)
        if args.seeds:
            n = pipe.add_seeds(args.seeds)
            console.print(f"[green]+{n}[/] candidates from {args.seeds}")
        if args.fb_search:
            queries = args.query or [f"{t} Maroc" for t in (args.term or ["vendeur"])]
            async with _fetcher(args) as f:
                if not f.has_session:
                    console.print("[red]Facebook search needs a session.[/] Run `login` first.")
                    return 2
                n = await pipe.add_from_facebook_search(f, queries)
            console.print(f"[green]+{n}[/] candidates from Facebook search")
        elif not args.seeds or args.query or args.category or args.term:
            queries = _queries_from_args(args)
            if args.max_queries:
                queries = queries[: args.max_queries]
            api_key = args.api_key or os.environ.get("FBVENDORS_API_KEY", "")
            if args.engine in ("serper", "brave-api") and not api_key:
                console.print(
                    f"[red]--engine {args.engine} needs a key.[/] "
                    "Pass --api-key or set FBVENDORS_API_KEY."
                )
                return 2
            # An API has no scraping rate limit worth pacing around.
            delay = 0.5 if args.engine in ("serper", "brave-api") else args.search_delay
            await pipe.add_from_search(
                queries, engine=args.engine, delay=delay,
                max_per_query=args.max_per_query, api_key=api_key,
            )
        pending, done = store.queue_counts()
        console.print(f"[bold]Queue:[/] {pending} pending, {done} done")
    return 0


async def cmd_scrape(args) -> int:
    with Store(args.db) as store:
        pipe = Pipeline(
            store,
            expand_related=args.expand,
            keep_unusable=args.keep_unusable,
            require_morocco=not args.no_geo_filter,
            # Without a session the feed is login-walled; fetching it just
            # doubles the request count for no data.
            want_posts=Fetcher(session_path=args.session).has_session,
        )
        async with _fetcher(args) as f:
            if not f.has_session and not args.anonymous:
                console.print(
                    "[yellow]No saved session.[/] Facebook shows very little to logged-out "
                    "clients — run `fbvendors login` first, or pass --anonymous to try anyway."
                )
                return 2
            stats = await pipe.scrape(f, limit=args.limit)
        console.print(f"[bold]Done:[/] {stats.render()}")
        if args.out:
            _export(store, args.out, args.min_score, args.format, args.keep_unusable,
                    getattr(args, "suppliers_only", False), getattr(args, "dropship", False))
    return 0


async def cmd_run(args) -> int:
    rc = await cmd_discover(args)
    if rc:
        return rc
    return await cmd_scrape(args)


def cmd_export(args) -> int:
    with Store(args.db) as store:
        _export(store, args.out, args.min_score, args.format, args.keep_unusable,
                getattr(args, "suppliers_only", False), getattr(args, "dropship", False))
    return 0


def _export(store: Store, out: str, min_score: float, fmt: str, keep_unusable: bool,
            suppliers_only: bool = False, dropship: bool = False) -> None:
    vendors = store.vendors(min_score=min_score, only_usable=not keep_unusable)
    if suppliers_only or dropship:
        from .supplier import classify_dropship, classify_supplier
        fn = classify_dropship if dropship else classify_supplier
        label = "dropship" if dropship else "supplier"
        kept = []
        for v in vendors:
            kwargs = dict(name=v.name, category=v.category, raw_category=v.raw_category,
                          about=v.about, price_signal=v.price_signal,
                          has_shop=v.has_shop, delivers=v.delivers)
            if dropship:
                kwargs["website"] = v.website
            ok, conf, why = fn(**kwargs)
            if ok:
                v.notes.append(f"{label} {conf}: {why}")
                kept.append(v)
        console.print(f"[dim]{label} filter: kept {len(kept)} of {len(vendors)}[/]")
        vendors = kept
    delim = "\t" if fmt == "tsv" else ","
    path = write_table(vendors, out, delimiter=delim)
    console.print(f"[green]Wrote[/] {len(vendors)} rows -> {path}")


def cmd_status(args) -> int:
    with Store(args.db) as store:
        pending, done = store.queue_counts()
        vendors = store.vendors(only_usable=False)
        usable = [v for v in vendors if v.is_usable()]

        t = Table(title="fbvendors", show_header=False, box=None)
        t.add_row("database", str(args.db))
        t.add_row("queue", f"{pending} pending / {done} done")
        t.add_row("pages stored", str(len(vendors)))
        t.add_row("usable rows", str(len(usable)))
        if usable:
            t.add_row("avg score", f"{sum(v.score for v in usable) / len(usable):.2f}")
            t.add_row("with phone", str(sum(1 for v in usable if v.phone)))
            t.add_row("with website", str(sum(1 for v in usable if v.website)))
            t.add_row("with price signal", str(sum(1 for v in usable if v.price_signal)))
        for status, n in sorted(store.stats().items()):
            t.add_row(f"  status:{status}", str(n))
        console.print(t)

        top = sorted(usable, key=lambda v: -v.score)[:10]
        if top:
            tt = Table(title="top rows")
            for col in ("name", "category", "location", "phone", "price_signal", "score"):
                tt.add_column(col, overflow="fold")
            for v in top:
                tt.add_row(v.name[:30], v.category[:22], v.location[:26],
                           v.phone, v.price_signal[:26], f"{v.score:.1f}")
            console.print(tt)
    return 0


async def cmd_osm(args) -> int:
    from .collect import collect_osm
    await collect_osm(args.db, refresh=args.refresh, concurrency=args.concurrency)
    return 0


def cmd_wikidata(args) -> int:
    from .collect import collect_wikidata
    collect_wikidata(args.db, refresh=args.refresh)
    return 0


async def cmd_sites(args) -> int:
    from .collect import collect_sites
    await collect_sites(args.db, rounds=args.rounds, concurrency=args.concurrency,
                        refresh_majestic=args.refresh)
    return 0


def cmd_merge(args) -> int:
    rows = merge_all(args.db)
    keep = [v for v in rows if reachable(v)]
    if not args.all:
        keep = [v for v in keep if sells(v)]
    delim = "\t" if args.format == "tsv" else ","
    path = write_table(keep, args.out, delimiter=delim)
    console.print(f"[green]Wrote[/] {len(keep)} rows -> {path}")
    from collections import Counter
    for k, n in Counter(v.kind or "(unclassified)" for v in keep).most_common():
        console.print(f"   [dim]{k:18}[/] {n}")
    return 0


async def cmd_collect(args) -> int:
    """Every stage, in order."""
    from .collect import collect_osm, collect_sites, collect_wikidata
    console.print("[bold]1/5 OpenStreetMap[/]")
    await collect_osm(args.db, refresh=args.refresh, concurrency=args.concurrency)
    console.print("[bold]2/5 Wikidata[/]")
    collect_wikidata(args.db, refresh=args.refresh)
    console.print("[bold]3/5 Facebook pages[/]")
    with Store(args.db) as store:
        pipe = Pipeline(store, require_morocco=True,
                        want_posts=Fetcher(session_path=args.session).has_session)
        async with _fetcher(args) as f:
            await pipe.scrape(f, limit=args.limit)
    console.print("[bold]4/5 vendor websites[/]")
    await collect_sites(args.db, rounds=args.rounds, concurrency=args.site_concurrency,
                        refresh_majestic=args.refresh)
    console.print("[bold]5/5 merge and export[/]")
    return cmd_merge(args)


def cmd_categories(args) -> int:
    for cat, kws in CATEGORIES.items():
        console.print(f"[bold]{cat}[/] [dim]{', '.join(kws[:6])}…[/]")
    return 0


# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fbvendors",
        description="Collect Moroccan vendor business data from public Facebook Pages.",
    )
    p.add_argument("--db", default=DEFAULT_DB, help="SQLite state file")
    p.add_argument("--session", default=DEFAULT_SESSION, help="saved Facebook session")
    p.add_argument("--cache-dir", default="data/cache")
    sub = p.add_subparsers(dest="cmd", required=True)

    def net_opts(sp):
        sp.add_argument("--min-delay", type=float, default=4.0,
                        help="minimum seconds between requests (default 4)")
        sp.add_argument("--max-delay", type=float, default=9.0)
        sp.add_argument("--concurrency", type=int, default=2)
        sp.add_argument("--scrolls", type=int, default=3, help="feed scrolls per page")
        sp.add_argument("--show-browser", action="store_true", help="run headed")
        sp.add_argument("--no-cache", action="store_true")

    def disc_opts(sp):
        sp.add_argument("--seeds", help="file of Page URLs/slugs, one per line or a CSV column")
        sp.add_argument("--query", action="append", help="raw search query (repeatable)")
        sp.add_argument("--category", action="append", help="taxonomy category (repeatable)")
        sp.add_argument("--city", action="append", help="Moroccan city (repeatable)")
        sp.add_argument("--term", action="append", help="extra keyword (repeatable)")
        sp.add_argument("--engine",
                        choices=["auto", "ddg", "brave", "bing", "serper", "brave-api"],
                        default="auto",
                        help="auto rotates free engines; serper/brave-api need --api-key "
                             "and are the only option that sustains a large sweep")
        sp.add_argument("--api-key", default="",
                        help="search API key (or set FBVENDORS_API_KEY)")
        sp.add_argument("--per-category", type=int, default=4)
        sp.add_argument("--max-queries", type=int, default=40)
        sp.add_argument("--max-per-query", type=int, default=25)
        sp.add_argument("--search-delay", type=float, default=8.0)
        sp.add_argument("--preset", choices=["suppliers", "dropship"],
                        help="use a curated query set; 'suppliers' targets wholesalers, "
                             "manufacturers, producers and artisan workshops")
        sp.add_argument("--fb-search", action="store_true",
                        help="use Facebook's own Pages search (needs a session)")

    def out_opts(sp):
        sp.add_argument("--out", help="write the table here when finished")
        sp.add_argument("--format", choices=["csv", "tsv"], default="csv")
        sp.add_argument("--min-score", type=float, default=0.0)
        sp.add_argument("--keep-unusable", action="store_true",
                        help="keep rows with no contact details")
        sp.add_argument("--suppliers-only", action="store_true",
                        help="keep only supply-side businesses (wholesalers, makers, "
                             "producers) - drops hotels, schools, clinics, restaurants")
        sp.add_argument("--dropship", action="store_true",
                        help="keep anything you could dropship from: wholesalers, "
                             "resellers and Facebook shops that ship inside Morocco")

    sp = sub.add_parser("login", help="sign in once and save the session")
    sp.add_argument("--wait", type=int, default=300)
    sp.set_defaults(func=cmd_login, is_async=True)

    sp = sub.add_parser("discover", help="find candidate pages and queue them")
    disc_opts(sp); net_opts(sp)
    sp.set_defaults(func=cmd_discover, is_async=True)

    sp = sub.add_parser("scrape", help="fetch and parse everything in the queue")
    sp.add_argument("--limit", type=int, help="max pages this run")
    sp.add_argument("--expand", action="store_true", help="queue related pages as we go")
    sp.add_argument("--anonymous", action="store_true", help="run without a session")
    sp.add_argument("--no-geo-filter", action="store_true", help="keep non-Morocco pages")
    net_opts(sp); out_opts(sp)
    sp.set_defaults(func=cmd_scrape, is_async=True)

    sp = sub.add_parser("run", help="discover then scrape in one go")
    disc_opts(sp); net_opts(sp); out_opts(sp)
    sp.add_argument("--limit", type=int)
    sp.add_argument("--expand", action="store_true")
    sp.add_argument("--anonymous", action="store_true")
    sp.add_argument("--no-geo-filter", action="store_true")
    sp.set_defaults(func=cmd_run, is_async=True)

    sp = sub.add_parser("export", help="write the table from stored results")
    sp.add_argument("--out", default="data/vendors.csv")
    sp.add_argument("--format", choices=["csv", "tsv"], default="csv")
    sp.add_argument("--min-score", type=float, default=0.0)
    sp.add_argument("--keep-unusable", action="store_true")
    sp.add_argument("--suppliers-only", action="store_true",
                    help="keep only supply-side businesses")
    sp.add_argument("--dropship", action="store_true",
                    help="keep anything dropship-usable (wholesalers, resellers, FB shops)")
    sp.set_defaults(func=cmd_export, is_async=False)

    sp = sub.add_parser("status", help="show run progress and the best rows")
    sp.set_defaults(func=cmd_status, is_async=False)

    sp = sub.add_parser("osm", help="query OpenStreetMap and queue the pages it knows")
    sp.add_argument("--refresh", action="store_true", help="re-run the Overpass queries")
    sp.add_argument("--concurrency", type=int, default=16)
    sp.set_defaults(func=cmd_osm, is_async=True)

    sp = sub.add_parser("wikidata", help="queue Moroccan entities that carry a Facebook ID")
    sp.add_argument("--refresh", action="store_true")
    sp.set_defaults(func=cmd_wikidata, is_async=False)

    sp = sub.add_parser("sites", help="crawl Moroccan vendor websites (no rate limits)")
    sp.add_argument("--rounds", type=int, default=4, help="snowball rounds through .ma links")
    sp.add_argument("--concurrency", type=int, default=24)
    sp.add_argument("--refresh", action="store_true", help="re-download the domain list")
    sp.set_defaults(func=cmd_sites, is_async=True)

    sp = sub.add_parser("merge", help="merge websites + Facebook pages into one table")
    sp.add_argument("--out", default="data/morocco_vendors.csv")
    sp.add_argument("--format", choices=["csv", "tsv"], default="csv")
    sp.add_argument("--all", action="store_true",
                    help="keep every row, not just the ones that sell")
    sp.set_defaults(func=cmd_merge, is_async=False)

    sp = sub.add_parser("collect", help="run every stage end to end")
    sp.add_argument("--refresh", action="store_true")
    sp.add_argument("--rounds", type=int, default=4)
    sp.add_argument("--site-concurrency", type=int, default=24)
    sp.add_argument("--limit", type=int, help="max Facebook pages to scrape")
    sp.add_argument("--out", default="data/morocco_vendors.csv")
    sp.add_argument("--format", choices=["csv", "tsv"], default="csv")
    sp.add_argument("--all", action="store_true")
    net_opts(sp)
    sp.set_defaults(func=cmd_collect, is_async=True)

    sp = sub.add_parser("categories", help="list the taxonomy")
    sp.set_defaults(func=cmd_categories, is_async=False)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    try:
        if getattr(args, "is_async", False):
            return asyncio.run(args.func(args))
        return args.func(args)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/] Progress is saved; re-run to continue.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
