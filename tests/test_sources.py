"""OSM and Wikidata discovery sources, tested against real payload shapes."""

from fbvendors.osm import OSM_CATEGORY, parse_elements
from fbvendors.wikidata import parse as wd_parse

OVERPASS = {
    "elements": [
        {   # facebook tagged as a full URL
            "type": "node", "id": 1, "lat": 33.6, "lon": -7.6,
            "tags": {
                "name": "Fromagerie Atlas", "shop": "cheese",
                "contact:facebook": "https://www.facebook.com/FromagerieAtlas/",
                "contact:phone": "+212 522-961471",
                "addr:street": "Rue Ibn Sina", "addr:housenumber": "12",
                "addr:city": "Casablanca", "addr:postcode": "20250",
                "website": "https://atlas.ma",
            },
        },
        {   # facebook tagged as a bare handle
            "type": "way", "id": 2, "center": {"lat": 35.7, "lon": -5.8},
            "tags": {"name": "Le Salon Bleu", "amenity": "restaurant",
                     "facebook": "salonbleutanger", "addr:city": "Tanger"},
        },
        {   # no facebook, but a website worth harvesting
            "type": "node", "id": 3, "lat": 31.6, "lon": -8.0,
            "tags": {"name": "Riad Karmela", "tourism": "guest_house",
                     "website": "https://riadkarmela.com", "phone": "0524387937"},
        },
        {"type": "node", "id": 4, "tags": {"amenity": "restaurant"}},   # unnamed -> dropped
    ]
}


def test_parse_overpass_elements():
    bs = parse_elements(OVERPASS)
    assert len(bs) == 3                       # the unnamed one is dropped
    atlas = bs[0]
    assert atlas.name == "Fromagerie Atlas"
    assert atlas.facebook_slug == "FromagerieAtlas"
    assert atlas.category == "Food & Beverage"
    assert atlas.phone == "522961471"
    assert atlas.city == "Casablanca"
    assert atlas.address == "12 Rue Ibn Sina, Casablanca, 20250"
    assert atlas.website == "https://atlas.ma/"


def test_bare_facebook_handle_accepted():
    assert parse_elements(OVERPASS)[1].facebook_slug == "salonbleutanger"


def test_way_center_used_for_coordinates():
    assert parse_elements(OVERPASS)[1].lat == 35.7


def test_business_without_facebook_is_kept_for_harvesting():
    riad = parse_elements(OVERPASS)[2]
    assert riad.facebook_slug == ""
    assert riad.website == "https://riadkarmela.com/"
    assert riad.category == "Travel & Hospitality"


def test_osm_category_map_covers_common_moroccan_trades():
    for tag in ("bakery", "carpet", "hotel", "car_repair", "pharmacy", "stationery"):
        assert OSM_CATEGORY[tag]


WIKIDATA = [
    {"itemLabel": {"value": "Marjane Group"}, "fb": {"value": "MarjaneOfficiel"},
     "typeLabel": {"value": "entreprise"}, "cityLabel": {"value": "Casablanca"}},
    {"itemLabel": {"value": "Marjane Group"}, "fb": {"value": "MarjaneOfficiel"},
     "typeLabel": {"value": "chaîne de magasins"}, "website": {"value": "https://marjane.ma"}},
    {"itemLabel": {"value": "ministère du Transport"}, "fb": {"value": "ministeretransport"},
     "typeLabel": {"value": "ministère marocain"}},
    {"itemLabel": {"value": "Ittihad de Tanger"}, "fb": {"value": "clubirt"},
     "typeLabel": {"value": "club de football"}},
]


def test_wikidata_collapses_duplicate_type_rows():
    recs = wd_parse(WIKIDATA)
    assert len(recs) == 1                     # ministry and football club filtered out
    m = recs[0]
    assert m["name"] == "Marjane Group"
    assert m["slug"] == "MarjaneOfficiel"
    assert m["website"] == "https://marjane.ma/"      # merged from the second row
    assert m["city"] == "Casablanca"                  # merged from the first
    assert "entreprise" in m["types"]


def test_wikidata_excludes_non_commercial_entities():
    slugs = {r["slug"] for r in wd_parse(WIKIDATA)}
    assert "ministeretransport" not in slugs
    assert "clubirt" not in slugs


def test_wikidata_handles_full_facebook_urls():
    rows = [{"itemLabel": {"value": "Inwi"},
             "fb": {"value": "https://www.facebook.com/inwi.ma/"},
             "typeLabel": {"value": "entreprise"}}]
    assert wd_parse(rows)[0]["slug"] == "inwi.ma"


# --- supplier classification -------------------------------------------------
from fbvendors.supplier import classify_supplier


def _yes(**kw):
    return classify_supplier(**kw)[0]


def test_producers_and_wholesalers_are_suppliers():
    assert _yes(name="Achibest FOOD", category="Food & Beverage",
                raw_category="Grossiste alimentaire")
    assert _yes(name="Textile Pro", category="Apparel & Textiles", about="grossiste tissu")
    assert _yes(name="Coop Argan", category="Health & Beauty", about="cooperative huile d'argan")


def test_consumer_venues_are_not_suppliers():
    for name, cat, raw in [("Riad Karmela", "Travel & Hospitality", "Chambre d'hôtes"),
                           ("La Grillardiere", "Food & Beverage", "Restaurant"),
                           ("Al Akhawayn", "Education & Training", "Enseignement supérieur")]:
        assert not _yes(name=name, category=cat, raw_category=raw)


def test_service_businesses_are_not_goods_suppliers():
    """Car rental and bus operators are real businesses but supply no stock."""
    for name, cat, raw in [("Krini Car", "Auto & Transport", "Location de voitures"),
                           ("Casabus", "Auto & Transport", "Société de transport"),
                           ("Anfaplace Mall", "Retail & General Trade", "Centre commercial")]:
        assert not _yes(name=name, category=cat, raw_category=raw)


def test_wholesale_wording_beats_a_service_category():
    assert _yes(name="Pieces Auto Gros", category="Auto & Transport",
                raw_category="Grossiste pièces auto")


def test_substring_traps_do_not_trigger():
    """'GymFactory' and 'British Workshop' must not read as manufacturing."""
    assert not _yes(name="The GymFactory", category="Sports & Leisure",
                    raw_category="Salle de sport")
    assert not _yes(name="British Workshop", category="Education & Training",
                    raw_category="École")


# --- picking the right Facebook page off a business website -------------------
from fbvendors.osm import pick_best_slug


def test_picks_business_page_over_platform_badge():
    assert pick_best_slug(["AirbnbFrance", "riadkarmela"], "Riad Karmela",
                          "riadkarmela.com") == "riadkarmela"
    assert pick_best_slug(["QodeInteractive", "natusmarrakechofficiel"],
                          "Natus Marrakech", "natusmarrakech.com") == "natusmarrakechofficiel"


def test_rejects_when_only_platform_pages_present():
    """A theme vendor's page is not the business's page."""
    assert pick_best_slug(["AirbnbFrance"], "Dar Something", "darsomething.com") == ""
    assert pick_best_slug(["security", "Meta", "public"], "Some Shop", "someshop.ma") == ""


def test_numeric_page_ids_are_accepted():
    assert pick_best_slug(["100057237588595"], "Garage Sahara", "garagesahara.com") \
        == "100057237588595"


def test_domain_match_wins():
    assert pick_best_slug(["randompage", "massinart"], "Unrelated Name",
                          "massinart.ma") == "massinart"
