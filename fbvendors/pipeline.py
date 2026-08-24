"""Wires discovery, fetching, parsing, scoring and storage into one run."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from rich.console import Console

from . import discover as disc
from .fetch import Fetcher, PageFetch
from .models import Vendor
from .parse import parse_page
from .score import apply_score
from .store import Store

console = Console()

# Consecutive login walls that mean "the session is dead, stop wasting requests".
BLOCK_ABORT_THRESHOLD = 5


@dataclass
class RunStats:
    fetched: int = 0
    saved: int = 0
    skipped_dupe: int = 0
    skipped_unusable: int = 0
    blocked: int = 0
    errors: int = 0
    discovered: int = 0

    def render(self) -> str:
        return (
            f"fetched={self.fetched} saved={self.saved} dupes={self.skipped_dupe} "
            f"thin={self.skipped_unusable} blocked={self.blocked} errors={self.errors}"
        )


class Pipeline:
    def __init__(
        self,
        store: Store,
        *,
        min_score: float = 0.0,
        expand_related: bool = False,
        keep_unusable: bool = False,
        require_morocco: bool = True,
        block_backoff: float = 5.0,
        want_posts: bool = True,
    ):
        self.store = store
        self.min_score = min_score
        self.expand_related = expand_related
        self.keep_unusable = keep_unusable
        self.require_morocco = require_morocco
        self.block_backoff = block_backoff
        self.want_posts = want_posts
        self.stats = RunStats()

    # -- discovery ---------------------------------------------------------

    def add_seeds(self, path: str) -> int:
        added = 0
        for slug, hint in disc.load_seeds(path):
            if self.store.enqueue(slug, source="seed", hint=hint):
                added += 1
        self.stats.discovered += added
        return added

    async def add_from_search(
        self, queries: list[str], engine: str = "ddg", delay: float = 8.0,
        max_per_query: int = 25, api_key: str = "",
    ) -> int:
        finder = disc.SearchDiscovery(delay=delay, engine=engine, api_key=api_key)
        console.print(f"[cyan]Searching[/] {len(queries)} queries via {engine} …")
        added = 0
        done = 0

        def absorb(query: str, hits: list[tuple[str, str]]) -> None:
            """Persist each query's results immediately - a 300-query sweep must not
            lose everything to one crash at the end."""
            nonlocal added, done
            done += 1
            new_here = 0
            for slug, label in hits:
                if self.store.enqueue(slug, source=f"search:{engine}", hint=label):
                    added += 1
                    new_here += 1
            if new_here or done % 20 == 0:
                console.print(
                    f"  [dim]{done}/{len(queries)}[/] +{new_here} "
                    f"[dim](total {added})[/] {query[:58]}"
                )

        await finder.run(queries, max_per_query=max_per_query, on_results=absorb)
        if getattr(finder, "api_error", "") and not added:
            console.print(f"[red]Search API error[/] — {finder.api_error}")
        elif finder.throttled and not added:
            console.print(
                f"[red]{engine} is throttling us[/] (no results for {len(queries)} queries). "
                "Wait a few minutes, raise --search-delay, or use --seeds / --fb-search instead."
            )
        self.stats.discovered += added
        console.print(f"[green]+{added}[/] new candidates queued")
        return added

    async def add_from_facebook_search(self, fetcher: Fetcher, queries: list[str]) -> int:
        added = 0
        for q in queries:
            for slug, label in await disc.facebook_search(fetcher, q):
                if self.store.enqueue(slug, source="fbsearch", hint=label):
                    added += 1
            await asyncio.sleep(2.0)
        self.stats.discovered += added
        return added

    # -- scraping ----------------------------------------------------------

    async def scrape(self, fetcher: Fetcher, limit: int | None = None) -> RunStats:
        pending = self.store.pending(limit)
        if not pending:
            console.print("[yellow]Queue is empty.[/] Run `discover` first.")
            return self.stats

        console.print(f"[cyan]Scraping[/] {len(pending)} pages …")
        consecutive_blocks = 0

        for i, row in enumerate(pending, 1):
            slug = row["slug"]
            if self.store.already_scraped(slug):
                self.store.mark_done(slug)
                continue
            try:
                res = await fetcher.fetch(slug, want_posts=self.want_posts)
            except Exception as e:                       # noqa: BLE001 - one bad page must not kill the run
                self.stats.errors += 1
                console.print(f"  [red]![/] {slug}: {type(e).__name__}: {e}")
                self.store.mark_done(slug)
                continue

            self.stats.fetched += 1
            if res.status == "login_wall":
                consecutive_blocks += 1
                self.stats.blocked += 1
                console.print(f"  [red]⛔[/] {slug}: login wall")
                if consecutive_blocks >= BLOCK_ABORT_THRESHOLD:
                    console.print(
                        f"[red bold]Aborting:[/] {consecutive_blocks} login walls in a row. "
                        "Re-run `fbvendors login` to refresh the session."
                    )
                    break
                if self.block_backoff:
                    await asyncio.sleep(min(60.0, self.block_backoff * consecutive_blocks))
                continue
            consecutive_blocks = 0

            vendor = self._build(res, hint=row["hint"] or "")
            self._commit(vendor, i, len(pending))
            self.store.mark_done(slug)

            if self.expand_related and res.html:
                for rel in disc.related_pages(res.html, exclude=slug)[:12]:
                    if self.store.enqueue(rel, source=f"related:{slug}"):
                        self.stats.discovered += 1

        return self.stats

    def _build(self, res: PageFetch, hint: str = "") -> Vendor:
        v = parse_page(
            res.html,
            url=res.url,
            rendered_text=res.text,
            post_texts=res.posts,
            post_dates=res.post_dates,
        )
        if not v.slug:
            v.slug = res.slug
        if res.status != "ok" and v.fetch_status == "ok":
            v.fetch_status = res.status
        # A search snippet often names the city when the About panel does not.
        if not v.city and hint:
            from .geo import format_location, lookup_city

            v.city, v.region = lookup_city(hint)
            if v.city:
                v.location = format_location(v.city, v.region, v.location)
                v.notes.append("city inferred from discovery hint")
        return apply_score(v)

    def _commit(self, v: Vendor, i: int, total: int) -> None:
        tag = f"[dim]{i}/{total}[/]"
        if v.fetch_status != "ok":
            console.print(f"  {tag} [yellow]~[/] {v.slug}: {v.fetch_status}")
            self.store.save(v)
            return
        if self.require_morocco and not (v.city or _mentions_morocco(v)):
            console.print(f"  {tag} [dim]-[/] {v.name or v.slug}: not Morocco, skipped")
            self.store.mark_done(v.slug)
            return
        if not v.is_usable() and not self.keep_unusable:
            self.stats.skipped_unusable += 1
            console.print(f"  {tag} [dim]-[/] {v.name or v.slug}: no contact details")
            self.store.save(v)
            return
        dupe = self.store.is_duplicate(v)
        if dupe:
            self.stats.skipped_dupe += 1
            console.print(f"  {tag} [dim]=[/] {v.name or v.slug}: duplicate of {dupe}")
            return
        self.store.save(v)
        self.stats.saved += 1
        bits = " ".join(filter(None, [v.phone, v.website, v.price_signal]))
        console.print(f"  {tag} [green]✓[/] {v.name} [dim]({v.score}) {bits}[/]")


def _mentions_morocco(v: Vendor) -> bool:
    from .geo import looks_moroccan

    return looks_moroccan(" ".join([v.about, v.address, v.name])) or bool(v.phone)
