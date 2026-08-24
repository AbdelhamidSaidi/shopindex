#!/usr/bin/env python3
"""Run the whole Moroccan vendor collection pipeline.

    python run_app.py                     # every stage, reusing cached payloads
    python run_app.py --refresh           # re-fetch every source from scratch
    python run_app.py --skip facebook     # skip a stage (repeatable)
    python run_app.py --limit 200         # cap the Facebook stage

Five stages. Only the Facebook one talks to a rate-limited service; the rest are
free to re-run. Every stage caches its raw payload under data/, so a second run
skips work already done unless --refresh is given.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rich.console import Console
from rich.table import Table

from fbvendors.collect import collect_osm, collect_sites, collect_wikidata
from fbvendors.fetch import Fetcher
from fbvendors.merge import merge_all, reachable, sells
from fbvendors.pipeline import Pipeline
from fbvendors.store import Store, write_table

console = Console()
STAGES = ("osm", "wikidata", "facebook", "sites", "merge")


def banner(n: int, total: int, title: str) -> None:
    console.rule(f"[bold cyan]{n}/{total}  {title}")


async def stage_facebook(args) -> None:
    """Fetch and parse every queued Facebook page."""
    fetcher = Fetcher(
        session_path=args.session,
        cache_dir=args.cache_dir,
        headless=True,
        concurrency=args.fb_concurrency,
        delay=(args.min_delay, args.max_delay),
        # Logged out the feed is a login wall, so don't spend a request on it.
        scrolls=0,
        use_cache=not args.no_cache,
    )
    if not fetcher.has_session:
        console.print("[yellow]No saved session[/] — running anonymously. "
                      "Contact details still come through; post text and prices do not. "
                      "Run `fbvendors login` first if you want them.")
    with Store(args.db) as store:
        pipe = Pipeline(store, require_morocco=True, want_posts=fetcher.has_session)
        async with fetcher as f:
            stats = await pipe.scrape(f, limit=args.limit)
    console.print(f"[green]Facebook:[/] {stats.render()}")


def stage_merge(args) -> int:
    rows = merge_all(args.db)
    keep = [v for v in rows if reachable(v)]
    if not args.all:
        keep = [v for v in keep if sells(v)]
    delim = "\t" if args.format == "tsv" else ","
    path = write_table(keep, args.out, delimiter=delim)

    t = Table(title=f"{len(keep)} rows → {path}")
    t.add_column("kind"); t.add_column("rows", justify="right")
    for kind, n in Counter(v.kind or "(unclassified)" for v in keep).most_common():
        t.add_row(kind, str(n))
    console.print(t)

    cov = Table(title="field coverage")
    cov.add_column("field"); cov.add_column("filled", justify="right")
    n = max(len(keep), 1)
    for f in ("phone", "email", "website", "location", "category",
              "price_signal", "facebook_url"):
        filled = sum(1 for v in keep if getattr(v, f))
        cov.add_row(f, f"{filled}/{len(keep)}  ({100 * filled // n}%)")
    console.print(cov)
    return len(keep)


async def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default="data/state.sqlite3")
    p.add_argument("--session", default="fb_session.json")
    p.add_argument("--cache-dir", default="data/cache")
    p.add_argument("--out", default="data/morocco_vendors.csv")
    p.add_argument("--format", choices=["csv", "tsv"], default="csv")
    p.add_argument("--all", action="store_true",
                   help="keep every row, not just the ones that sell")
    p.add_argument("--refresh", action="store_true",
                   help="re-fetch every source instead of reusing data/")
    p.add_argument("--skip", action="append", choices=STAGES, default=[],
                   help="skip a stage (repeatable)")
    p.add_argument("--only", action="append", choices=STAGES, default=[],
                   help="run only these stages (repeatable)")
    p.add_argument("--limit", type=int, help="max Facebook pages this run")
    p.add_argument("--rounds", type=int, default=4, help="website snowball rounds")
    p.add_argument("--site-concurrency", type=int, default=24)
    p.add_argument("--osm-concurrency", type=int, default=16)
    p.add_argument("--fb-concurrency", type=int, default=3)
    p.add_argument("--min-delay", type=float, default=2.0)
    p.add_argument("--max-delay", type=float, default=5.0)
    p.add_argument("--no-cache", action="store_true")
    args = p.parse_args(argv)

    wanted = [s for s in STAGES
              if (not args.only or s in args.only) and s not in args.skip]
    if not wanted:
        console.print("[red]No stages selected.[/] --skip removed everything --only asked for.")
        return 2
    for label, target in (("--db", Path(args.db).parent), ("--out", Path(args.out).parent)):
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            console.print(f"[red]Cannot use {label} path[/] {target}: {e.strerror}")
            return 2

    console.print(f"[bold]Stages:[/] {' → '.join(wanted)}"
                  f"{'  [dim](refresh)[/]' if args.refresh else ''}")
    started = time.monotonic()
    total = len(wanted)
    rows = 0

    for i, stage in enumerate(wanted, 1):
        t0 = time.monotonic()
        try:
            if stage == "osm":
                banner(i, total, "OpenStreetMap — businesses and their Facebook pages")
                await collect_osm(args.db, refresh=args.refresh,
                                  concurrency=args.osm_concurrency)
            elif stage == "wikidata":
                banner(i, total, "Wikidata — Moroccan entities with a Facebook ID")
                collect_wikidata(args.db, refresh=args.refresh)
            elif stage == "facebook":
                banner(i, total, "Facebook — fetch and parse the queued pages")
                await stage_facebook(args)
            elif stage == "sites":
                banner(i, total, "Websites — crawl and snowball through .ma links")
                await collect_sites(args.db, rounds=args.rounds,
                                    concurrency=args.site_concurrency,
                                    refresh_majestic=args.refresh)
            elif stage == "merge":
                banner(i, total, "Merge — one row per business")
                rows = stage_merge(args)
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted.[/] Progress is saved; re-run to continue.")
            return 130
        except Exception as e:                       # noqa: BLE001
            # One failing source must not throw away the stages that already ran.
            console.print(f"[red]Stage '{stage}' failed:[/] {type(e).__name__}: {e}")
            console.print("[dim]continuing with the remaining stages[/]")
            continue
        console.print(f"[dim]{stage} took {time.monotonic() - t0:.0f}s[/]")

    console.rule("[bold green]done")
    console.print(f"{rows} rows in {args.out} · total {time.monotonic() - started:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
