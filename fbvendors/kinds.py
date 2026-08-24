"""What kind of thing a row is, and whether it actually sells goods.

Two lessons are baked in here. First, matching keywords across a whole page
catches cookie banners, footers and blog archives -- so the strong signals are
only read from the parts of a page that describe the business (title, meta
description, headings, first screenful). Second, a bare word like "fabrication"
means nothing; "nous fabriquons" and "notre usine" mean something. The patterns
are phrases, not vocabulary.
"""

from __future__ import annotations

import re
import unicodedata

KINDS = ["b2b supplier", "manufacturer", "producer", "reseller", "online shop",
         "vendor site", "facebook page", "marketplace", "directory", "service"]

# Phrase-level evidence. Each must be something a business says about itself.
_STRONG = {
    "b2b supplier": [
        r"vente en gros", r"prix de gros", r"tarifs? (?:de )?gros", r"demi[- ]gros",
        r"grossiste\w*", r"wholesale", r"revendeurs? bienvenus",
        r"r[ée]serv[ée] aux professionnels", r"tarif professionnel",
        r"minimum de commande", r"commande minimum", r"\bb2b\b", r"بالجملة",
    ],
    "manufacturer": [
        r"nous fabriquons", r"notre usine", r"notre atelier de fabrication",
        r"fabricant (?:de|d')", r"unit[ée] de production", r"soci[ée]t[ée] de fabrication",
        r"manufacturer of", r"we manufacture", r"atelier de confection",
        r"fabriqu[ée] (?:au|dans notre)", r"مصنع", r"نصنع",
    ],
    "producer": [
        r"notre ferme", r"notre domaine", r"coop[ée]rative agricole",
        r"producteur (?:de|d')", r"nous produisons", r"notre huilerie",
        r"r[ée]colt[ée] (?:par|dans)", r"تعاونية", r"مزرعتنا",
    ],
    "reseller": [
        r"distributeur (?:exclusif|officiel|agr[ée][ée])", r"revendeur (?:officiel|agr[ée][ée])",
        r"importateur (?:exclusif|officiel)?", r"agent (?:officiel|exclusif)",
        r"concessionnaire", r"d[ée]positaire", r"موزع معتمد",
    ],
    "marketplace": [
        r"place de march[ée]", r"devenez vendeur", r"vendez sur", r"nos vendeurs",
        r"multi[- ]vendeurs?", r"marketplace",
    ],
    "directory": [
        r"annuaire des entreprises", r"r[ée]pertoire des soci[ée]t[ée]s",
        r"liste des entreprises", r"business directory",
    ],
}
STRONG = {k: re.compile("|".join(v), re.IGNORECASE) for k, v in _STRONG.items()}

# A real cart, not the word "panier" in a blog post.
# Explicit checkout evidence. Bare "woocommerce" is excluded: plenty of
# WordPress themes ship its assets without ever selling anything.
CART = re.compile(
    r"(ajouter au panier|add to cart|add-to-cart|/checkout|/panier|/cart\b|"
    r"data-product_id|prestashop|cdn\.shopify|shopify\.com/s/|"
    r"proc[ée]der au paiement|passer (?:la|ma|votre) commande|"
    r"woocommerce-page|wc-add-to-cart|أضف إلى السلة)",
    re.IGNORECASE,
)
# Selling language, still fairly loose - used only as a weak fallback.
SELLS = re.compile(
    r"(nos produits|our products|notre catalogue|notre boutique|en stock|"
    r"prix (?:unitaire|ttc|ht)|commande[rz]|acheter en ligne|منتجاتنا|للبيع)",
    re.IGNORECASE,
)
COD = re.compile(
    r"(paiement [àa] la livraison|cash on delivery|الدفع عند الاستلام|"
    r"livraison (?:partout|dans tout) (?:au|le) maroc|livraison au maroc|"
    r"livraison gratuite)",
    re.IGNORECASE,
)
# Checked against the headline only: a service business says so in its title or
# strapline. Looking for these in body text matches any passing mention.
SERVICE = re.compile(
    r"\b(nos prestations|cabinet (?:d[e']|m[ée]dical|dentaire)|clinique|"
    r"h[ôo]tel\b|riad\b|maison d'h[ôo]tes|restaurant\b|caf[ée]\b|"
    r"[ée]cole\b|universit[ée]|institut de formation|centre de formation|"
    r"agence (?:de voyage|immobili[èe]re|de communication|web|digitale?)|"
    r"agence seo|r[ée]f[ée]rencement|consulting|cabinet conseil)\b",
    re.IGNORECASE,
)

# Parked, expired or for-sale domains.
PARKED = re.compile(
    r"(hugedomains|domain (?:is )?for sale|buy this domain|domaine [àa] vendre|"
    r"parked (?:free )?courtesy|godaddy|sedo\.com|namecheap parking|"
    r"this domain may be for sale|under construction|site en construction|"
    r"coming soon|wordpress\.com|default web page)",
    re.IGNORECASE,
)

MOROCCO = re.compile(
    r"(maroc|morocco|marocain|المغرب|\+\s?212|\b00212|casablanca|rabat|marrakech|"
    r"tanger|agadir|f[èe]s|meknes|oujda|k[ée]nitra|t[ée]touan)",
    re.IGNORECASE,
)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def salient(title: str = "", description: str = "", headings: str = "",
            body: str = "", body_chars: int = 6000) -> str:
    """The parts of a page where a business describes itself."""
    return _norm(" \n ".join([title, description, headings, (body or "")[:body_chars]]))


def is_parked(text: str, title: str = "") -> bool:
    return bool(PARKED.search(_norm(title + " " + (text or "")[:4000])))


def is_moroccan(text: str, host: str = "", phone: str = "") -> bool:
    if host.endswith(".ma"):
        return True
    if phone:
        return True
    return bool(MOROCCO.search(_norm((text or "")[:20_000])))


def classify_kind(headline: str, body: str = "", html: str = "", *,
                  is_facebook: bool = False) -> tuple[str, bool, float]:
    """Return (kind, sells_goods, confidence).

    `headline` is title + meta description + headings; `body` the first screenful
    of visible text; `html` the raw markup. Cart evidence lives in markup (class
    names, /cart URLs, platform scripts), never in visible text -- so it is read
    from `html`. Service wording is read from `headline` only.
    """
    head = _norm(headline)
    s = head + " \n " + _norm(body)

    has_cart = bool(CART.search(html or "")) or bool(CART.search(s))
    sells_words = bool(SELLS.search(s))

    for kind in ("b2b supplier", "manufacturer", "producer", "reseller"):
        if STRONG[kind].search(s):
            return kind, True, 0.9 if (has_cart or sells_words) else 0.75
    if STRONG["marketplace"].search(s):
        return "marketplace", True, 0.75
    if STRONG["directory"].search(s) and not has_cart:
        return "directory", False, 0.7
    # An explicit service headline outranks a stray cart script.
    if SERVICE.search(head):
        return "service", False, 0.65
    if has_cart:
        return "online shop", True, 0.85
    if is_facebook:
        return "facebook page", sells_words, 0.6 if sells_words else 0.4
    if sells_words and not SERVICE.search(head):
        return "vendor site", True, 0.55
    if SERVICE.search(head):
        return "service", False, 0.6
    return "", False, 0.2


def ships_morocco(text: str) -> bool:
    return bool(COD.search(_norm(text)))
