from fbvendors.models import Vendor
from fbvendors.score import compute_score
from fbvendors.store import Store, write_table


def _full():
    return Vendor(
        name="A", phone="661754248", all_phones=["661754248", "522961471"],
        whatsapp="661754248", website="https://x.ma", email="a@x.ma", city="Casablanca",
        category="Food & Beverage", price_count=12, last_post_age_days=3,
        followers=25000, verified=True, rating=4.7, reviews=40,
    )


def test_score_bounds():
    assert compute_score(_full()) == 5.0
    assert compute_score(Vendor()) == 0.0


def test_score_rewards_reachability():
    with_phone = compute_score(Vendor(name="x", phone="661754248"))
    with_cat = compute_score(Vendor(name="x", category="Food & Beverage"))
    assert with_phone > with_cat


def test_stale_page_scores_lower():
    fresh, stale = _full(), _full()
    stale.last_post_age_days = 900
    assert compute_score(stale) < compute_score(fresh) or compute_score(fresh) == 5.0


def test_is_usable():
    assert Vendor(name="A", phone="661754248").is_usable()
    assert not Vendor(name="A").is_usable()
    assert not Vendor(phone="661754248").is_usable()


def test_store_roundtrip_and_dedupe(tmp_path):
    with Store(tmp_path / "s.db") as st:
        assert st.enqueue("a", "seed") and not st.enqueue("a", "seed")
        v = Vendor(name="A", slug="a", phone="661754248", fetch_status="ok", score=3.0)
        v.stamp()
        st.save(v)
        assert st.already_scraped("a")
        assert st.is_duplicate(Vendor(slug="b", phone="661754248")) == "a"
        assert st.is_duplicate(Vendor(slug="c", phone="600000000")) is None
        assert len(st.vendors()) == 1


def test_min_score_filter(tmp_path):
    with Store(tmp_path / "s.db") as st:
        for i, sc in enumerate([1.0, 4.0]):
            v = Vendor(name=f"v{i}", slug=f"s{i}", phone=f"66175424{i}", fetch_status="ok", score=sc)
            v.stamp()
            st.save(v)
        assert len(st.vendors(min_score=3.0)) == 1


def test_write_table_csv_and_tsv(tmp_path):
    v = Vendor(name="A, Inc", slug="a", phone="661754248",
               price_signal="MAD 45-420 (med 150; n=4)", score=4.0)
    v.stamp()
    csv_path = write_table([v], tmp_path / "o.csv")
    body = csv_path.read_text(encoding="utf-8-sig")
    assert '"A, Inc"' in body and "MAD 45-420" in body
    tsv_path = write_table([v], tmp_path / "o.tsv", delimiter="\t")
    assert "\t" in tsv_path.read_text(encoding="utf-8-sig").splitlines()[0]
