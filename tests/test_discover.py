"""Search-result extraction, verified offline against both markup shapes."""
from fbvendors.discover import (
    SearchDiscovery, build_queries, extract_page_slugs, is_vendor_slug, load_seeds,
)

# Shape A: DuckDuckGo's documented result markup.
DDG_CLASSED = """
<div class="result results_links web-result">
  <a class="result__a" href="https://www.facebook.com/gourmandiacheese">
     Fromagerie Gourmandia | Casablanca - Facebook</a>
</div>
<div class="result"><a class="result__a"
   href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.facebook.com%2FBaghaShoes%2F">
   Bagha Shoes | Agadir</a></div>
<div class="footer"><a href="https://www.facebook.com/duckduckgo">Follow us</a></div>
"""

# Shape B: markup we don't recognise - the fallback must still find the results.
DDG_UNKNOWN = """
<section><article>
  <a href="https://www.facebook.com/gourmandiacheese">Fromagerie Gourmandia | Casablanca</a>
  <a href="https://www.facebook.com/librairie.chaymae">Librairie Papeterie Chaymaa | Rabat</a>
  <a href="https://www.facebook.com/login.php?next=x">Se connecter</a>
  <a href="https://www.bing.com/search?q=x">More</a>
</article></section>
"""

BING_CHROME = """
<ol id="b_results">
  <li class="b_algo"><h2><a href="https://www.facebook.com/promasteel.ma">PROMASTEEL Fes</a></h2></li>
</ol>
<footer><a href="https://www.facebook.com/copilotsearch">Copilot on Facebook</a></footer>
"""


def test_extracts_from_known_markup():
    got = SearchDiscovery(engine="ddg").extract_from_html(DDG_CLASSED)
    slugs = [s for s, _ in got]
    assert "gourmandiacheese" in slugs
    assert "BaghaShoes" in slugs          # unwrapped from the uddg redirect
    assert "duckduckgo" not in slugs      # engine's own social link


def test_falls_back_when_markup_unrecognised():
    got = SearchDiscovery(engine="ddg").extract_from_html(DDG_UNKNOWN)
    slugs = [s for s, _ in got]
    assert slugs == ["gourmandiacheese", "librairie.chaymae"]   # login.php dropped


def test_bing_chrome_excluded():
    slugs = [s for s, _ in SearchDiscovery(engine="bing").extract_from_html(BING_CHROME)]
    assert slugs == ["promasteel.ma"]


def test_labels_carry_the_city_hint():
    got = dict(SearchDiscovery(engine="ddg").extract_from_html(DDG_CLASSED))
    assert "Casablanca" in got["gourmandiacheese"]


def test_max_results_respected():
    assert len(SearchDiscovery().extract_from_html(DDG_UNKNOWN, max_results=1)) == 1


def test_dedupes_within_one_page():
    html = DDG_UNKNOWN + DDG_UNKNOWN
    assert len(SearchDiscovery().extract_from_html(html)) == 2


def test_load_seeds_from_urls_and_csv(tmp_path):
    f = tmp_path / "seeds.txt"
    f.write_text(
        "# comment\n"
        "https://www.facebook.com/gourmandiacheese\n"
        "\n"
        "Vendor Name,https://www.facebook.com/librairie.chaymae,Rabat\n"
        "https://www.facebook.com/gourmandiacheese\n",   # duplicate
        encoding="utf-8",
    )
    seeds = load_seeds(f)
    assert [s for s, _ in seeds] == ["gourmandiacheese", "librairie.chaymae"]


def test_build_queries_crosses_terms_and_cities():
    q = build_queries(categories=["Office Supplies"], cities=["Rabat"], per_category=2)
    assert len(q) == 2
    assert all('site:facebook.com' in x and '"Rabat"' in x for x in q)


def test_extract_page_slugs_handles_all_url_forms():
    html = ('<a href="https://www.facebook.com/goldcheese.ma/">a</a>'
            '<a href="https://web.facebook.com/pages/Forum-Diffusion/1234567">b</a>'
            '<a href="https://fr-fr.facebook.com/profile.php?id=100064821119345">c</a>'
            '<a href="https://www.facebook.com/groups/12345">d</a>')
    assert extract_page_slugs(html) == ["goldcheese.ma", "1234567", "100064821119345"]


def test_reserved_slugs_rejected():
    for bad in ("groups", "marketplace", "login.php", "story.php", "ads", "watch"):
        assert not is_vendor_slug(bad)


def test_run_streams_results_incrementally():
    """A long sweep must persist per query, not only at the end."""
    import asyncio

    d = SearchDiscovery(delay=0, engine="ddg")
    seen: list[tuple[str, int]] = []

    async def fake_get(client, query, engine=None):
        return DDG_UNKNOWN if "good" in query else ""

    d._get = fake_get  # type: ignore[method-assign]
    asyncio.run(d.run(["good-1", "empty", "good-2"],
                      on_results=lambda q, hits: seen.append((q, len(hits)))))

    # Called once per query, including the one that returned nothing.
    assert [q for q, _ in seen] == ["good-1", "empty", "good-2"]
    assert seen[0][1] == 2          # first query yields both slugs
    assert seen[1][1] == 0
    assert seen[2][1] == 0          # already seen, deduped across queries


BRAVE_RESULTS = """
<div id="results">
  <a class="h" href="https://www.facebook.com/lebouzrestaurant/">Le Bouz Restaurant Casablanca</a>
  <a class="h" href="https://www.facebook.com/p/Casablanca-Restaurant-Shisha-61565864440334/">
     Casablanca Restaurant Shisha</a>
</div>
<footer><a href="https://www.facebook.com/brave">Brave on Facebook</a></footer>
"""


def test_brave_results_extracted():
    got = SearchDiscovery(engine="brave").extract_from_html(BRAVE_RESULTS, engine="brave")
    slugs = [s for s, _ in got]
    assert "lebouzrestaurant" in slugs
    assert "61565864440334" in slugs      # the /p/Name-<id> form


def test_engine_rotation():
    d = SearchDiscovery(engine="auto")
    picked = [d._pick_engine(i) for i in range(4)]
    assert picked == ["ddg", "brave", "ddg", "brave"]
    assert SearchDiscovery(engine="ddg")._pick_engine(5) == "ddg"


def test_captcha_response_is_not_treated_as_results():
    """A challenge page must count as throttled, never be parsed or solved."""
    import asyncio

    class FakeResp:
        status_code = 200
        text = "<html><body>Please complete the CAPTCHA challenge</body></html>"

    d = SearchDiscovery(delay=0, retries=0, engine="brave")

    async def fake_request(client, engine, query):
        return FakeResp()

    d._request = fake_request  # type: ignore[method-assign]
    out = asyncio.run(d._get(None, "q", engine="brave"))
    assert out == ""
    assert d.engine_stats["brave"]["throttled"] == 1


def test_api_engine_requires_key():
    import pytest
    with pytest.raises(ValueError):
        SearchDiscovery(engine="serper")
    SearchDiscovery(engine="serper", api_key="k")   # fine with a key


def test_serper_json_parsed():
    import asyncio

    class FakeResp:
        status_code = 200
        @staticmethod
        def json():
            return {"organic": [
                {"link": "https://www.facebook.com/promasteel.ma", "title": "PROMASTEEL Fes"},
                {"link": "https://example.ma/not-facebook", "title": "nope"},
                {"link": "https://www.facebook.com/groups/123", "title": "a group"},
            ]}

    class FakeClient:
        async def post(self, *a, **k):
            return FakeResp()

    d = SearchDiscovery(engine="serper", api_key="k")
    got = asyncio.run(d._api_results(FakeClient(), "serper", "q", 25))
    assert got == [("promasteel.ma", "PROMASTEEL Fes")]
    assert d.engine_stats["serper"]["ok"] == 1


def test_rejected_api_key_is_reported_not_silent():
    import asyncio

    class FakeResp:
        status_code = 401
        @staticmethod
        def json():
            return {}

    class FakeClient:
        async def get(self, *a, **k):
            return FakeResp()

    d = SearchDiscovery(engine="brave-api", api_key="bad")  # 401 path
    assert asyncio.run(d._api_results(FakeClient(), "brave-api", "q", 25)) is None
    assert "rejected" in d.api_error


def test_brave_422_is_treated_as_bad_key():
    """Brave returns 422 SUBSCRIPTION_TOKEN_INVALID, not 401, for a bad token."""
    import asyncio

    class FakeResp:
        status_code = 422
        @staticmethod
        def json():
            return {}

    class FakeClient:
        async def get(self, *a, **k):
            return FakeResp()

    d = SearchDiscovery(engine="brave-api", api_key="bad")
    asyncio.run(d._api_results(FakeClient(), "brave-api", "q", 25))
    assert "rejected" in d.api_error
