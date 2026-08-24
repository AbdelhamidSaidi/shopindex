import asyncio

from fbvendors.fetch import PageFetch
from fbvendors.pipeline import Pipeline
from fbvendors.store import Store
from make_fixture import build

POSTS = ["Gouda 45dh la piece", "Plateau de 180 à 420 DH, pour commander 0661754248"]


class FakeFetcher:
    """Stands in for Playwright: returns canned pages, records what was asked for."""

    def __init__(self, pages: dict[str, PageFetch]):
        self.pages = pages
        self.calls: list[str] = []
        self.timeout_ms = 1000

    async def fetch(self, slug: str, want_posts: bool = True) -> PageFetch:
        self.calls.append(slug)
        if slug not in self.pages:
            return PageFetch(slug=slug, url=f"https://www.facebook.com/{slug}",
                             status="unavailable", html="", text="")
        return self.pages[slug]


def _page(slug: str) -> PageFetch:
    return PageFetch(
        slug=slug,
        url=f"https://www.facebook.com/{slug}",
        html=build(),
        text="18,4 K followers · contact@goldcheesemorocco.com",
        posts=POSTS,
        post_dates=["2 j"],
    )


def test_pipeline_scrapes_and_stores(tmp_path):
    with Store(tmp_path / "s.db") as st:
        st.enqueue("goldcheese.ma", "seed")
        pipe = Pipeline(st)
        f = FakeFetcher({"goldcheese.ma": _page("goldcheese.ma")})
        stats = asyncio.run(pipe.scrape(f))

        assert stats.fetched == 1 and stats.saved == 1
        rows = st.vendors()
        assert len(rows) == 1
        v = rows[0]
        assert v.name == "Fromagerie Gold Cheese Morocco"
        assert v.phone == "522961471"
        assert v.category == "Food & Beverage"
        assert v.price_count > 0
        assert v.score > 3.0
        assert st.queue_counts() == (0, 1)


def test_pipeline_skips_duplicate_page(tmp_path):
    with Store(tmp_path / "s.db") as st:
        st.enqueue("goldcheese.ma", "seed")
        st.enqueue("goldcheese-casa", "seed")
        pipe = Pipeline(st)
        f = FakeFetcher({s: _page(s) for s in ("goldcheese.ma", "goldcheese-casa")})
        stats = asyncio.run(pipe.scrape(f))
        assert stats.saved == 1 and stats.skipped_dupe == 1


def test_pipeline_records_unavailable_pages(tmp_path):
    with Store(tmp_path / "s.db") as st:
        st.enqueue("ghost", "seed")
        stats = asyncio.run(Pipeline(st).scrape(FakeFetcher({})))
        assert stats.saved == 0
        assert st.stats().get("unavailable") == 1


def test_login_wall_run_aborts(tmp_path):
    with Store(tmp_path / "s.db") as st:
        for i in range(8):
            st.enqueue(f"page{i}", "seed")
        walled = {f"page{i}": PageFetch(slug=f"page{i}", url="u", status="login_wall")
                  for i in range(8)}
        stats = asyncio.run(Pipeline(st, block_backoff=0).scrape(FakeFetcher(walled)))
        # Stops at the threshold instead of burning the whole queue.
        assert stats.blocked == 5 and stats.saved == 0


def test_geo_filter_drops_non_morocco(tmp_path):
    foreign = PageFetch(
        slug="paris-shop", url="https://www.facebook.com/paris-shop",
        html='<html><head><meta property="og:title" content="Paris Shop"/></head>'
             '<body>Boutique a Paris, France</body></html>',
        text="Boutique a Paris, France", posts=[], post_dates=[],
    )
    with Store(tmp_path / "s.db") as st:
        st.enqueue("paris-shop", "seed")
        stats = asyncio.run(Pipeline(st, require_morocco=True).scrape(
            FakeFetcher({"paris-shop": foreign})))
        assert stats.saved == 0


def test_expand_related_queues_more(tmp_path):
    with Store(tmp_path / "s.db") as st:
        st.enqueue("goldcheese.ma", "seed")
        pipe = Pipeline(st, expand_related=True)
        asyncio.run(pipe.scrape(FakeFetcher({"goldcheese.ma": _page("goldcheese.ma")})))
        pending, _ = st.queue_counts()
        assert pending >= 1   # the fixture links out to other pages
