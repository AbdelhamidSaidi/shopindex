"""Classifying what a row is, and whether it sells goods."""
from fbvendors.kinds import classify_kind, is_moroccan, is_parked, salient, ships_morocco


def kind(title="", desc="", headings="", body="", html=""):
    return classify_kind(salient(title, desc, headings, ""), body, html)[0]


def test_wholesale_wording_wins():
    assert kind("Textile Pro", "Vente en gros, revendeurs bienvenus") == "b2b supplier"
    assert kind("Souk Gros", "Prix de gros pour professionnels") == "b2b supplier"


def test_manufacturer_needs_a_phrase_not_a_word():
    assert kind("Babouche Co", "Nous fabriquons dans notre atelier") == "manufacturer"
    # A passing mention of "fabrication" is not a claim to manufacture.
    assert kind("Blog Deco", "Article sur la fabrication du zellige",
                body="fabrication") != "manufacturer"


def test_producer():
    assert kind("Tazota", "Notre ferme pédagogique") == "producer"
    assert kind("Coop Argan", "Coopérative agricole d'huile d'argan") == "producer"


def test_reseller_requires_official_wording():
    assert kind("Distrib SA", "Distributeur exclusif au Maroc") == "reseller"


def test_cart_markup_makes_an_online_shop():
    assert kind("Shop", "Nos produits", html='<a class="add-to-cart">x</a>') == "online shop"
    assert kind("Shop", "Nos produits", html='<link href="/checkout">') == "online shop"


def test_bundled_woocommerce_assets_are_not_a_shop():
    """Plenty of themes ship WooCommerce without selling anything."""
    assert kind("Agence Web", "Création de sites",
                html="<script src='woocommerce.min.js'>") != "online shop"


def test_service_headline_beats_a_stray_cart():
    assert kind("SEOCOM", "Agence SEO et référencement",
                html='<a class="add-to-cart">x</a>') == "service"
    assert kind("Restaurant Luigi", "Restaurant italien") == "service"


def test_directory_and_marketplace():
    assert kind("Annuaire MA", "Annuaire des entreprises marocaines") == "directory"
    assert kind("SoukPlace", "Devenez vendeur sur notre place de marché") == "marketplace"


def test_parked_domains_detected():
    assert is_parked("opheon.com is for sale", "HugeDomains")
    assert is_parked("Site en construction", "")
    assert not is_parked("Boutique en ligne de savon", "Flourish Soap")


def test_morocco_gate():
    assert is_moroccan("Boutique à Casablanca", "shop.com")
    assert is_moroccan("", "vendeur.ma")
    assert is_moroccan("anything", "shop.com", phone="661754248")
    assert not is_moroccan("Paris boutique", "shop.fr")


def test_ships_morocco():
    assert ships_morocco("Livraison partout au Maroc")
    assert ships_morocco("paiement à la livraison")
    assert not ships_morocco("retrait en magasin uniquement")
