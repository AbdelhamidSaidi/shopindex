from fbvendors.price import extract_prices, aggregate


def test_simple_price():
    s = extract_prices("Prix: 250dh seulement")
    assert s.values == [250.0]


def test_thousands_separators():
    assert extract_prices("4 500 DH").values == [4500.0]
    assert extract_prices("14.000 dh").values == [14000.0]
    assert extract_prices("12,500.00 MAD").values == [12500.0]


def test_decimal_price():
    assert extract_prices("89,50 dhs").values == [89.5]


def test_range():
    s = extract_prices("de 180 à 420 DH")
    assert s.values == [180.0, 420.0]


def test_arabic_currency_and_digits():
    s = extract_prices("الثمن ١٢٠٠ درهم")
    assert s.values == [1200.0]


def test_phone_number_is_not_a_price():
    assert extract_prices("Appelez 0661754248 dh").values == []


def test_bare_number_is_not_a_price():
    assert extract_prices("Lot de 250 pieces disponibles").values == []


def test_delivery_fee_separated():
    s = extract_prices("Sac 300dh, livraison 30dh")
    assert s.values == [300.0]
    assert s.delivery_fees == [30.0]


def test_implausible_values_dropped():
    assert extract_prices("promo 2 dh").values == []          # below floor
    assert extract_prices("9999999999 dh").values == []       # above ceiling


def test_on_request_signal():
    s = extract_prices("Prix sur demande, contactez-nous en MP")
    assert s.on_request and s.signal() == "on-request"


def test_commerce_flags():
    s = extract_prices("Livraison partout au Maroc, pour commander appelez-nous, prix de gros")
    assert s.delivers and s.has_shop and s.wholesale


def test_aggregate_summary_and_signal():
    agg = aggregate(["Gouda 45dh", "Plateau 180 DH", "Roquefort 90dh", "Brie 120 dhs"])
    summary = agg.summary()
    assert summary["count"] == 4
    assert summary["min"] == 45.0 and summary["max"] == 180.0
    assert summary["median"] == 105.0
    assert "MAD 45-180" in agg.signal()


def test_robust_range_trims_outlier():
    texts = [f"{v} dh" for v in [50, 55, 60, 62, 65, 70, 75, 80, 900000]]
    agg = aggregate(texts)
    assert agg.summary(robust=True)["max"] < 900000


def test_empty_signal():
    assert aggregate(["bonjour tout le monde"]).signal() == ""
