# fbvendors

Collects business data on Moroccan vendors from public Facebook Pages and writes
it to one table: who they are, how to reach them, where they are, and what their
posts say about pricing.

Built and verified against live Moroccan Pages — the demo run below is real output.

## Quick start

```bash
python3 -m venv .venv && ./.venv/bin/pip install -e . && ./.venv/bin/playwright install chromium
```

Everything, end to end:

```bash
python run_app.py
```

`run_app.py` is the entry point: it runs all five stages, prints a per-stage
timing and a summary of what landed in the table. Useful flags:

```bash
python run_app.py --refresh          # re-fetch every source instead of reusing data/
python run_app.py --skip facebook    # skip a stage (repeatable)
python run_app.py --only sites --only merge   # run just these
python run_app.py --limit 200        # cap the Facebook stage
python run_app.py --all              # keep every row, not only the ones that sell
```

A stage that fails is reported and the run continues, so one dead source never
throws away the stages that already succeeded. Ctrl-C is safe — state is in
`data/state.sqlite3` and re-running picks up where it stopped.

The same stages are also available individually as `fbvendors <stage>`.

That runs all five stages and writes the table. Each stage is also a command in
its own right, and every one is resumable — state lives in `data/state.sqlite3`
and raw payloads in `data/`, so re-running skips work already done.

| Stage | Command | What it does | Rate limited? |
|---|---|---|---|
| 1 | `fbvendors osm` | Overpass: Moroccan businesses tagged with a Facebook page, plus every business with a website — then harvests the Facebook link off those sites | no |
| 2 | `fbvendors wikidata` | Moroccan entities carrying a Facebook ID (P2013) | no |
| 3 | `fbvendors scrape --anonymous` | fetches and parses the queued Facebook pages | Facebook-side |
| 4 | `fbvendors sites` | crawls Moroccan vendor websites, snowballing outward through `.ma` links | no |
| 5 | `fbvendors merge` | merges websites + Facebook pages into one table, one row per business | no |

Add `--refresh` to any stage to re-fetch its source instead of reusing the cached
payload in `data/`.

### What each stage produced on a real run

| Stage | Output |
|---|---|
| OSM | 812 Facebook pages (163 tagged directly, the rest harvested from 2,284 websites) |
| Wikidata | 230 entities, 197 newly queued |
| Facebook scrape | 778 pages parsed |
| Website crawl | 1,926 vendor sites over 4 snowball rounds, ~9 minutes |
| Merge | 1,947 unique businesses → **422 that sell** |

### The `kind` column

Every row says what it is and how you would buy from it:

`b2b supplier` · `manufacturer` · `producer` · `reseller` · `online shop` ·
`vendor site` · `marketplace` · `facebook page` · `directory` · `service`

`fbvendors merge` keeps only the kinds that sell; `--all` keeps everything.

Strong labels are read from a page's title, meta description and headings — never
from body text, where footers and cookie banners produce nonsense. They also
require phrases (`nous fabriquons`, `notre usine`), not bare vocabulary
(`fabrication`), which appears in any blog post about industry. Cart evidence is
read from markup, since `/checkout` and `add-to-cart` never appear in visible
text; bundled WooCommerce assets are ignored because plenty of agency sites ship
them without selling anything. Parked domains and non-Moroccan sites are dropped.

### Websites are where the prices are

Anonymous Facebook cannot see post text, so `price_signal` stays empty there.
Websites list prices openly, so crawled sites populate it — `MAD 209-1343 (med
549; n=85)` and similar. That is the main reason stage 4 exists.

## Read this before your first real run

**Posts are behind the login wall.** Logged out, Facebook still serves the whole
About panel — name, category, address, phone, email, website, followers, reviews.
It does *not* serve the post feed, and the post feed is where prices live. So:

| Mode | What you get | `price_signal` |
|---|---|---|
| `--anonymous` | 19 of 29 columns: identity, contact, location, audience | empty |
| with a session | all 29 columns | populated |

If you want the price index, log in once:

```bash
fbvendors login
```

That opens a real browser window and waits while **you** sign in by hand. The tool
never sees or stores your password — only the resulting session cookie, saved to
`fb_session.json` (gitignored). Use a throwaway account: automation gets sessions
checkpointed, and you do not want that to be your main login.

## Commands

| Command | Purpose |
|---|---|
| `login` | Sign in once by hand; saves the session for later runs |
| `discover` | Find candidate Pages (search engines, seed file, or Facebook search) |
| `scrape` | Fetch, parse, score and store everything queued |
| `run` | `discover` then `scrape` in one go |
| `export` | Re-write the table from stored results, no refetching |
| `status` | Progress, field coverage, and the best rows so far |
| `categories` | List the taxonomy |

### Discovery

Four sources, usable together:

```bash
# 1. a file of Pages you already have (most precise; also accepts any CSV export)
fbvendors discover --seeds seeds/example.txt

# 2. search engines — keywords crossed with Moroccan cities
fbvendors discover --category "Machinery & Industrial Supplies" --city Casablanca

# 3. your own queries
fbvendors discover --query 'site:facebook.com "quincaillerie" "Marrakech"'

# 4. Facebook's own Pages search (needs a session)
fbvendors discover --fb-search --term "grossiste textile Maroc"
```

`scrape --expand` adds a fifth: it queues the "Pages similaires" rail of every
page it visits, which snowballs from a small seed list into a sector.

### Discovery is the bottleneck, and it is a rate-limit problem

Mapping "a Moroccan business" to "its Facebook page" needs a search index, and
**every free search endpoint rate-limits bulk querying from a single IP.**
Measured, not assumed:

| Engine | Behaviour under a sweep |
|---|---|
| DuckDuckGo | `202` after ~2 queries; still blocked after 8 min of silence |
| Brave (HTML) | `200` once, then `429` + CAPTCHA |
| Bing | Copilot-gated; returns page chrome, not results |
| Mojeek / Ecosia | `403` |
| Public SearXNG | mostly `429` under any concurrency |

The client backs off, reports the throttle plainly, and **never attempts a
CAPTCHA**. A blocked IP takes hours to recover, so pacing your way out of it is
not realistic for a 300+ query sweep.

**Use a search API instead.** This is the only thing that makes a large sweep
work from one machine:

```bash
export FBVENDORS_API_KEY=your_key_here
fbvendors discover --engine brave-api --per-category 4
```

Brave's API free tier is 2,000 queries/month; Serper (`--engine serper`) gives
2,500 free credits against Google's index, which has the deepest coverage of
Moroccan Pages. Both finish a 336-query sweep in about five minutes. The key is
read from `FBVENDORS_API_KEY` so it never has to appear in a command line, and
it is never written to the database or logs.

A rejected key is reported explicitly rather than looking like "no results":

```
Search API error — HTTP 422: the API key was rejected
```

**Or log in.** A session unlocks Facebook's own Pages search (`--fb-search`) and
the "Pages similaires" rail (`scrape --expand`), which snowballs from a small
seed list into a whole sector. Both are Facebook-native and effectively
unlimited. Note the rail does *not* render when logged out — verified.

**Or use seeds.** `--seeds` has no rate limit at all. Any CSV works; the first
column is scanned for facebook.com links. A useful trick when you have a list of
vendor websites: many Moroccan business sites link to their own Page, so
fetching those sites and extracting the link finds Pages without touching a
search engine (~40% hit rate in testing, and every site is a different host).

## Output

29 columns, one row per vendor:

| Group | Columns |
|---|---|
| Identity | `name` `category` `location` |
| Contact | `website` `phone` `whatsapp` `email` `address` |
| Price | `price_signal` `price_min` `price_max` `price_median` `price_count` `currency` |
| Commerce | `has_shop` `delivers` |
| Audience | `followers` `likes` `rating` `reviews` `verified` |
| Activity | `last_post_date` `posts_scanned` |
| Context | `about` `source` `score` `facebook_url` `page_id` `scraped_at` |

Real output from `scrape --anonymous`:

| name | category | location | website | phone | followers | score |
|---|---|---|---|---|---|---|
| In Finé | Food & Beverage | Bouskoura, Casablanca-Settat | https://infine.ma/ | 520300916 | 1400 | 3.6 |
| Les maîtres du marché | Food & Beverage | Casablanca, Casablanca-Settat | https://lesmaitresdumarche.com/ | 522360779 | 8400 | 3.1 |
| Librairie Papeterie Chaymaa | Office Supplies | Rabat, Rabat-Sale-Kenitra | | 537776163 | 933 | 2.8 |

`--format tsv` writes tab-separated instead. `--min-score 3` drops thin rows.

### Phones

Normalised to 9 digits, national form, leading zero stripped — `0537777107` and
`+212 6 61 75 42 48` and `٠٦٨٠٧٣٤٤٣٧` all land as `537777107` / `661754248` /
`680734437`. Numbers found only in post text are captured too; the extra ones
live in the SQLite payload.

### The price signal

Post text is scanned for dirham amounts in the forms Moroccan vendors actually
use — `250dh`, `1 200 DHS`, `12,500.00 MAD`, `الثمن ١٢٠٠ درهم`, `de 180 à 420 DH` —
and reduced to a range plus a median:

```
MAD 45-420 (med 150; n=18) [wholesale]
```

What it deliberately excludes: phone numbers, bare numbers with no currency,
delivery fees (`livraison 30dh` is tracked separately), and values outside
5–2,000,000 MAD. With 8+ observations the reported range is trimmed to the
10th–90th percentile so one outlier post cannot define it. Pages that only say
"prix sur demande" get `on-request` rather than a blank.

### The score

0–5, weighted toward reachability: phone 1.2, website 0.7, price signal 0.7,
resolved city 0.55, category 0.4, WhatsApp and email 0.35 each, plus smaller
amounts for recent posting, audience size, verification and ratings. It ranks
leads; it is not a judgement about the business.

## Configuration worth knowing

| Flag | Default | Why you'd change it |
|---|---|---|
| `--min-delay` / `--max-delay` | 4 / 9 s | Lower is faster and riskier. Don't. |
| `--concurrency` | 2 | 1 is safest for a logged-in session |
| `--scrolls` | 3 | More scrolls = more posts = a better price signal, slower |
| `--no-cache` | off | Raw HTML is cached 7 days; re-parsing is free |
| `--keep-unusable` | off | Keep rows with no contact details |
| `--no-geo-filter` | off | Keep Pages that don't resolve to Morocco |

Consent dialogs are answered with *decline optional cookies*, never accept-all.
Images, video and fonts are blocked at the network layer — only text is fetched.

## How it holds up when Facebook changes

Nothing keys off CSS classes. Four independent layers are tried and merged:

1. **OpenGraph meta tags** — stable for years
2. **About panel, read by label** — `Adresse`, `Mobile`, `E-mail`, `Site web`,
   `Catégories`, in French, English or Arabic. Facebook renders these as
   value-then-label pairs; reading by label survives DOM reshuffling.
3. **JSON payloads in `<script>`** — Relay data, matched on key names
4. **Escape-aware regex on the raw document** — catches the same keys when
   they're nested inside double-encoded JSON strings

If a layer breaks, the others still produce a row. `fetch_status` records what
happened (`ok`, `login_wall`, `unavailable`, `timeout`) so failures are visible
instead of silently becoming empty cells. Five login walls in a row aborts the
run rather than burning the queue against a dead session.

## Tests

```bash
./.venv/bin/python -m pytest tests -q
```

82 tests, no network. They cover phone/URL normalisation, the price grammar
(including Arabic numerals and the phone-vs-price distinction), category
mapping, city lookup, scoring, storage and dedupe, and a full pipeline run
against a fake fetcher. Two of them replay About panels captured from real
Moroccan Pages.

## Limits

- **Anonymous mode has no price signal.** Stated above; it's the main one.
- **Only public Pages.** No groups, no Marketplace, no personal profiles.
- **`category` is inferred** from Facebook's own label plus the name and bio.
  Generic labels like "Entreprise locale" fall through to the more specific
  signal. Spot-check before trusting it as a segmentation key.
- **`location` uses the current 12-region naming**, not the pre-2015 regions.
- **Dedupe is by page id, slug, phone and website.** A vendor running two Pages
  with different numbers will appear twice.
- **Automated collection is against Facebook's Terms of Service**, whatever the
  data's public status. That's your call to make; the pacing defaults are set to
  be unobtrusive, and for Pages you own the Graph API is the sanctioned route.
