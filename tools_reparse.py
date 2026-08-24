"""Re-run parsing and scoring over the fetch cache. No network."""
import sys
sys.path.insert(0, ".")
from fbvendors.fetch import Cache
from fbvendors.parse import parse_page
from fbvendors.score import apply_score
from fbvendors.store import Store
from fbvendors.geo import format_location, lookup_city

cache = Cache("data/cache", ttl_hours=0)   # ttl 0 = never expire for a re-parse
store = Store("data/morocco.sqlite3")

rows = list(store.db.execute("SELECT slug, hint FROM queue WHERE done=1"))
print(f"re-parsing {len(rows)} cached pages …", flush=True)

store.db.execute("DELETE FROM pages")
store.db.execute("DELETE FROM dedupe")
store.db.commit()

ok = skipped = dupes = 0
for i, (slug, hint) in enumerate(rows, 1):
    rec = cache.get(f"https://www.facebook.com/{slug}")
    if not rec:
        skipped += 1
        continue
    v = parse_page(rec.get("html", ""), url=f"https://www.facebook.com/{slug}",
                   rendered_text=rec.get("text", ""),
                   post_texts=rec.get("posts", []), post_dates=rec.get("post_dates", []))
    if not v.slug:
        v.slug = slug
    if not v.city and hint:
        v.city, v.region = lookup_city(hint)
        if v.city:
            v.location = format_location(v.city, v.region, v.location)
    apply_score(v)
    if v.fetch_status != "ok" or not v.is_usable():
        skipped += 1
        continue
    if store.is_duplicate(v):
        dupes += 1
        continue
    store.save(v)
    ok += 1
    if i % 150 == 0:
        print(f"  {i}/{len(rows)} … kept {ok}", flush=True)

print(f"\nkept {ok} | skipped {skipped} | dupes {dupes}")
store.close()
