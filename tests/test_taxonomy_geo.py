from fbvendors.geo import format_location, lookup_city, looks_moroccan
from fbvendors.taxonomy import classify, tidy_raw_category


def test_lookup_city_french_and_arabic():
    assert lookup_city("Bd Zerktouni, Casablanca")[0] == "Casablanca"
    assert lookup_city("شارع محمد الخامس مراكش") == ("Marrakech", "Marrakech-Safi")


def test_lookup_city_variants():
    assert lookup_city("Tangier")[0] == "Tanger"
    assert lookup_city("Fès")[0] == "Fes"


def test_lookup_city_none():
    assert lookup_city("Paris, France") == ("", "")


def test_format_location():
    assert format_location("Rabat", "Rabat-Sale-Kenitra") == "Rabat, Rabat-Sale-Kenitra"
    assert format_location("", "", "12 Rue X") == "12 Rue X"


def test_looks_moroccan():
    assert looks_moroccan("Livraison partout au Maroc")
    assert looks_moroccan("+212 661754248")
    assert not looks_moroccan("Barcelona shop")


def test_classify_prefers_explicit_category():
    assert classify("Papeterie", "Forum Diffusion", "")[0] == "Office Supplies"


def test_classify_falls_through_generic_category():
    # "Local business" carries no signal, so the name/bio must decide.
    assert classify("Local business", "Fromagerie Gold", "vente de fromage")[0] == "Food & Beverage"


def test_classify_arabic():
    assert classify("", "صناعة تقليدية مراكش", "")[0] == "Arts, Crafts & Gifts"


def test_classify_unknown():
    assert classify("Local business", "Xyz", "abc") == ("", "")


def test_tidy_raw_category():
    assert tidy_raw_category("Papeterie · Librairie · Papeterie") == "Papeterie / Librairie"


def test_generic_retail_defers_to_specific_name():
    # "Commerce de detail" is too broad; "Librairie Papeterie" is the real signal.
    assert classify("Commerce de détail", "Librairie Papeterie Chaymaa", "")[0] == "Office Supplies"


def test_generic_retail_kept_when_nothing_better():
    assert classify("Commerce de détail", "Chez Ahmed", "")[0] == "Retail & General Trade"
