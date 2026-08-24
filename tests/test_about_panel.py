"""Regression tests against About panels captured from real Moroccan Pages."""
from pathlib import Path

import pytest

from fbvendors.parse import parse_about_panel

FIXTURES = Path(__file__).parent / "fixtures"


def _panel(name: str) -> dict[str, str]:
    return parse_about_panel((FIXTURES / name).read_text(encoding="utf-8"))


def test_gourmandia_panel():
    p = _panel("about_gourmandiacheese.txt")
    assert p["address"] == "Casablanca, Morocco, 20250"
    assert p["phone"] == "0675-845505"
    assert p["email"] == "fromagerie.gourmandia@gmail.com"
    assert p["categories"] == "Entreprise locale"
    assert p["reviews"] == "5"


def test_chaymae_panel():
    p = _panel("about_librairie_chaymae.txt")
    assert p["address"].startswith("N°2 PLACE ABOU BAKR")
    assert p["phone"] == "05377-76163"
    assert p["service_area"].startswith("Rabat")
    assert p["reviews"] == "0"


@pytest.mark.parametrize("text,field,expected", [
    ("12 Rue X\nAdresse", "address", "12 Rue X"),
    ("0661754248\nMobile", "phone", "0661754248"),
    ("a@b.ma\nE-mail", "email", "a@b.ma"),
    ("https://x.ma\nSite web", "website", "https://x.ma"),
    ("Catégories\nBoulangerie", "categories", "Boulangerie"),
])
def test_label_directions(text, field, expected):
    assert parse_about_panel(text).get(field) == expected


def test_empty_panel():
    assert parse_about_panel("") == {}
