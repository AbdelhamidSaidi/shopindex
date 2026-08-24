import pytest

from fbvendors.normalize import (
    clean_name, clean_url, find_emails, find_phones, is_social,
    normalize_phone, normalize_slug, unwrap_fb_link, whatsapp_from_url,
)


@pytest.mark.parametrize("raw,expected", [
    ("+212 6 61 75 42 48", "661754248"),
    ("00212522961471", "522961471"),
    ("0537777107", "537777107"),
    ("0680-73-44-37", "680734437"),
    ("212 643 935 177", "643935177"),
    ("٠٦٨٠٧٣٤٤٣٧", "680734437"),        # Arabic-Indic digits
    ("06.80.73.44.37", "680734437"),
])
def test_normalize_phone_valid(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["", "12345", "0412345678", "abc", "0033612345678", "2024"])
def test_normalize_phone_rejects(raw):
    assert normalize_phone(raw) == ""


def test_find_phones_dedupes_and_orders():
    text = "Tel 0661754248 / 06 61 75 42 48 ou fixe +212522961471"
    assert find_phones(text) == ["661754248", "522961471"]


def test_unwrap_and_clean_url():
    wrapped = ("https://l.facebook.com/l.php?u=https%3A%2F%2Fforumdiffusion.ma%2F"
               "%3Ffbclid%3DIwAR123&h=AT1")
    assert unwrap_fb_link(wrapped) == "https://forumdiffusion.ma/?fbclid=IwAR123"
    assert clean_url(wrapped) == "https://forumdiffusion.ma/"


def test_clean_url_strips_tracking_and_www():
    assert clean_url("http://www.example.ma/shop/?utm_source=fb&id=7") == "https://example.ma/shop?id=7"


def test_clean_url_rejects_junk():
    assert clean_url("not a url") == ""
    assert clean_url("") == ""


def test_is_social():
    assert is_social("https://instagram.com/x")
    assert is_social("https://wa.me/212661754248")
    assert not is_social("https://goldcheesemorocco.com")


def test_whatsapp_from_url():
    assert whatsapp_from_url("https://wa.me/212680734437") == "680734437"
    assert whatsapp_from_url("https://api.whatsapp.com/send?phone=212661754248") == "661754248"
    assert whatsapp_from_url("https://example.ma") == ""


def test_find_emails_filters_noise():
    text = "Ecrivez a contact@vendeur.ma - pas a noreply@facebook.com"
    assert find_emails(text) == ["contact@vendeur.ma"]


def test_clean_name():
    assert clean_name("(3) Bagha Shoes | Facebook") == "Bagha Shoes"
    assert clean_name("  PROMASTEEL - Home ") == "PROMASTEEL"


@pytest.mark.parametrize("url,slug", [
    ("https://www.facebook.com/BaghaShoes/about/?ref=page_internal", "BaghaShoes"),
    ("https://web.facebook.com/pages/Forum-Diffusion/1234567", "1234567"),
    ("https://fr-fr.facebook.com/profile.php?id=100064821119345", "100064821119345"),
    ("goldcheese.ma", "goldcheese.ma"),
])
def test_normalize_slug(url, slug):
    assert normalize_slug(url) == slug
