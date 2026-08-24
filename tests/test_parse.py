from datetime import date, timedelta

from fbvendors.parse import parse_page, parse_timestamp_text
from make_fixture import build

POSTS = [
    "Nouveau: Gouda 250g — 45dh la pièce. Livraison 30dh",
    "Plateau fête: de 180 à 420 DH. Pour commander 0661-75-42-48",
    "الثمن 120 درهم للجملة. Whatsapp 0680734437",
]
RENDERED = "18,4 K followers · 17 110 j'aime · contact@goldcheesemorocco.com"


def parsed():
    return parse_page(
        build(),
        url="https://www.facebook.com/goldcheese.ma/",
        rendered_text=RENDERED,
        post_texts=POSTS,
        post_dates=["2 j", "Il y a 5 jours"],
    )


def test_identity():
    v = parsed()
    assert v.name == "Fromagerie Gold Cheese Morocco"
    assert v.page_id == "100064821119345"
    assert v.facebook_url == "https://www.facebook.com/goldcheese.ma"
    assert v.fetch_status == "ok"


def test_category_and_location():
    v = parsed()
    assert v.category == "Food & Beverage"
    assert v.city == "Casablanca"
    assert v.location == "Casablanca, Casablanca-Settat"
    assert "Ain Sebaa" in v.address


def test_contact_fields():
    v = parsed()
    assert v.phone == "522961471"
    assert v.website == "https://goldcheesemorocco.com/"     # unwrapped from l.php
    assert v.email == "contact@goldcheesemorocco.com"
    assert v.whatsapp == "661754248"                          # from the wa.me link
    assert "680734437" in v.all_phones                        # only published in a post


def test_website_ignores_social_links():
    assert "instagram" not in parsed().website


def test_price_signal():
    v = parsed()
    assert v.currency == "MAD"
    assert v.price_count == 4
    assert v.price_min == 45.0 and v.price_max == 420.0
    assert v.delivers and v.has_shop


def test_audience_fields():
    v = parsed()
    assert v.followers == 18432
    assert v.likes == 17110
    assert v.rating == 4.6 and v.reviews == 87
    assert v.verified is True


def test_login_wall_detected():
    v = parse_page("<html><body>You must log in to continue</body></html>",
                   url="https://www.facebook.com/x")
    assert v.fetch_status == "login_wall"


def test_counts_from_text_when_json_missing():
    v = parse_page("<html><body></body></html>", url="https://www.facebook.com/x",
                   rendered_text="12,4 K followers · 900 j'aime")
    assert v.followers == 12400 and v.likes == 900


def test_parse_relative_timestamps():
    today = date(2026, 8, 23)
    assert parse_timestamp_text("2 j", today) == today - timedelta(days=2)
    assert parse_timestamp_text("Il y a 3 jours", today) == today - timedelta(days=3)
    assert parse_timestamp_text("5h", today) == today
    assert parse_timestamp_text("Hier", today) == today - timedelta(days=1)
    assert parse_timestamp_text("2 sem", today) == today - timedelta(days=14)
    assert parse_timestamp_text("12 juillet", today) == date(2026, 7, 12)
    assert parse_timestamp_text("3 janvier 2025", today) == date(2025, 1, 3)
    assert parse_timestamp_text("nothing here", today) is None


def test_future_bare_date_rolls_back_a_year():
    assert parse_timestamp_text("25 décembre", date(2026, 8, 23)) == date(2025, 12, 25)


def test_facebook_bundle_names_rejected():
    """Facebook's JS bundles sit under the same JSON "name" key as page names."""
    from fbvendors.parse import _plausible_page_name

    for junk in ("FileHashWorkerBundle", "CometRouteLoader", "ServiceWorkerModule",
                 "IntlPolyfillRuntime", "XAsyncRequestConfig"):
        assert not _plausible_page_name(junk)
    for real in ("Fromagerie Gourmandia", "PROMASTEEL", "In Finé",
                 "Librairie Papeterie Chaymaa", "116 Agadir"):
        assert _plausible_page_name(real)


def test_name_falls_back_past_junk():
    html = ('<html><head></head><body>'
            '<script type="application/json">{"name":"FileHashWorkerBundle"}</script>'
            '</body></html>')
    v = parse_page(html, url="https://www.facebook.com/x",
                   rendered_text="Naranj Restaurant\n1 200 followers")
    assert v.name != "FileHashWorkerBundle"
