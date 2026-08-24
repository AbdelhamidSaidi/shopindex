"""Build a synthetic Page document shaped like the real thing (escaped JSON and all)."""
import json

def build() -> str:
    relay = {
        "require": [["RelayPrefetchedStreamCache", "next", [], [{
            "__bbox": {"result": {"data": {"page": {
                "id": "100064821119345",
                "name": "Fromagerie Gold Cheese Morocco",
                "category_name": "Fromagerie · Produits alimentaires",
                "single_line_address": "12 Rue Ibn Sina, Ain Sebaa, Casablanca 20250",
                "phone_number": "+212 522-961471",
                "website": "https://goldcheesemorocco.com/",
                "email": "contact@goldcheesemorocco.com",
                "follower_count": 18432,
                "like_count": 17110,
                "overall_star_rating": 4.6,
                "rating_count": 87,
                "is_verified": True,
                "page_intro": "Producteur de fromages à Casablanca. Livraison partout au Maroc.",
            }}}}
        }]]]
    }
    posts = [
        {"creation_time": 1755820800, "message": "Nouveau: Gouda 250g — 45dh la pièce. Livraison 30dh"},
        {"creation_time": 1755561600, "message": "Plateau fête: de 180 à 420 DH. Pour commander 0661-75-42-48"},
        {"creation_time": 1755302400, "message": "الثمن 120 درهم للجملة"},
    ]
    inner = json.dumps(relay)
    escaped_posts = json.dumps(json.dumps(posts))  # double-encoded, like real payloads
    return f"""<!DOCTYPE html><html><head>
<title>(2) Fromagerie Gold Cheese Morocco | Facebook</title>
<meta property="og:title" content="Fromagerie Gold Cheese Morocco" />
<meta property="og:url" content="https://www.facebook.com/goldcheese.ma/" />
<meta property="og:description" content="18,4 K followers · Producteur de fromages à Casablanca." />
</head><body>
<script type="application/json" data-sjs>{inner}</script>
<script>window.__d=({escaped_posts});"pageID":"100064821119345"</script>
<a href="https://l.facebook.com/l.php?u=https%3A%2F%2Fgoldcheesemorocco.com%2F%3Ffbclid%3Dabc&amp;h=AT0">goldcheesemorocco.com</a>
<a href="https://wa.me/212661754248">WhatsApp</a>
<a href="https://www.instagram.com/goldcheese.ma">Instagram</a>
<div aria-label="Pages similaires">
<a href="https://www.facebook.com/fromagerie.atlas">Fromagerie Atlas</a>
<a href="https://www.facebook.com/pages/Cremerie-Rif/998877665">Cremerie Rif</a>
</div>
</body></html>"""

if __name__ == "__main__":
    print(build())
