"""Deciding whether a page is a *supplier* — someone an e-commerce store could buy from.

A restaurant, a hotel, a school and a clinic are all legitimate Moroccan
businesses and all useless as a supplier. What matters here is whether the page
sells goods wholesale, makes them, grows them, or imports them.
"""

from __future__ import annotations

import re
import unicodedata

# Categories whose members can plausibly supply goods to an online store.
SUPPLIER_CATEGORIES = {
    "Apparel & Textiles", "Footwear & Leather", "Arts, Crafts & Gifts",
    "Health & Beauty", "Electronics & IT", "Home & Furniture",
    "Machinery & Industrial Supplies", "Chemicals & Plastics",
    "Packaging & Printing", "Office Supplies", "Agriculture",
    "Food & Beverage", "Retail & General Trade", "Auto & Transport",
    "Construction & Real Estate", "Energy & Environment",
}

# Never a supplier, whatever else the page says.
EXCLUDED_CATEGORIES = {
    "Travel & Hospitality", "Education & Training", "Public & Government",
    "Nonprofit & Community", "Arts & Culture", "Health & Medical",
    "Business Services", "Sports & Leisure", "Security & Protection",
}

# Unambiguous B2B wording. Only these can rescue a page from an excluded
# category, because they have no innocent consumer reading.
STRONG_CORE = re.compile(
    r"(\bgrossiste\w*|\bdemi[- ]gros\b|\bvente en gros\b|\bprix de gros\b|"
    r"\bwholesale\w*|\bfabricant\w*|\bfabrication\b|\bmanufactur\w*|"
    r"\busine\b|\bproducteur\w*|\bdistributeur\w*|\bimportateur\w*|"
    r"\bimport[- ]export\b|\bimportation\b|\bexportation\b|\bfournisseur\w*|"
    r"\bsupplier\w*|\btannerie\b|\bfilature\b|\bminoterie\b|\bconserverie\b|"
    r"\braffinerie\b|\bconfection\b|"
    r"بالجملة|مصنع|مورد|تصدير|استيراد)",
    re.IGNORECASE,
)

# Suggestive but not conclusive - "workshop" and "factory" show up in gym and
# school names, so these only add confidence, never rescue.
STRONG_WEAK = re.compile(
    r"(\batelier\b|\bworkshop\b|\bfactory\b|\bcooperative\b|\bcoopérative\b|"
    r"\bproduction\b|\bdistribution\b|\bexport\b|\bimport\b|"
    r"جملة|منتج|تعاونية)",
    re.IGNORECASE,
)

STRONG = re.compile(f"{STRONG_CORE.pattern}|{STRONG_WEAK.pattern}", re.IGNORECASE)

# Consumer-facing formats that are buyers, not suppliers.
NEGATIVE = re.compile(
    r"(restaurant|caf[ée]|snack|pizzeria|fast[- ]food|traiteur|"
    r"h[ôo]tel|riad|auberge|maison d'h[ôo]tes|guest house|hostel|camping|"
    r"[ée]cole|lyc[ée]e|universit|institut|formation|acad[ée]mie|cr[èe]che|"
    r"clinique|cabinet m[ée]dical|dentiste|pharmacie|h[ôo]pital|"
    r"salle de sport|gym|fitness|spa|hammam|salon de coiffure|"
    r"agence de voyage|tour operator|mus[ée]e|cin[ée]ma|th[ée][âa]tre|"
    r"association|fondation|minist[èe]re|ambassade|banque|assurance)",
    re.IGNORECASE,
)


# Sells a service or rents things out - useful businesses, but nothing an
# online store can buy stock from.
SERVICE_ONLY = re.compile(
    r"(location de voiture\w*|car rental|rent a car|location de v[ée]hicule\w*|"
    r"\btaxi\b|\bbus\b|autobus|autocar|transport urbain|transport de personnes|"
    r"transport de voyageur\w*|transport public|compagnie de transport|"
    r"soci[ée]t[ée] de transport|entreprise de transport|d[ée]m[ée]nagement|"
    r"\btramway\b|\ba[ée]roport\b|compagnie a[ée]rienne|"
    r"centre commercial|shopping mall|\bmall\b|hypermarch[ée]|supermarch[ée]|"
    r"station[- ]service|parking|auto[- ]?[ée]cole|driving school|"
    r"agence immobili[èe]re|real estate agency|syndic)",
    re.IGNORECASE,
)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def classify_supplier(
    name: str = "", category: str = "", raw_category: str = "", about: str = "",
    price_signal: str = "", has_shop: bool = False, delivers: bool = False,
) -> tuple[bool, float, str]:
    """Return (is_supplier, confidence 0-1, reason).

    Confidence is about how sure we are it supplies goods, not how good a lead
    it is — `score` in the main table already covers reachability.
    """
    blob = _norm(" ".join([name, raw_category, about]))
    reasons: list[str] = []
    conf = 0.0

    if category in EXCLUDED_CATEGORIES:
        # Only unambiguous B2B wording rescues a consumer-facing category, and
        # not when the page also reads as a shopfront.
        core = STRONG_CORE.search(blob)
        if core and not NEGATIVE.search(blob):
            conf, reasons = 0.55, [f"says '{core.group(0)}' despite category '{category}'"]
        else:
            return False, 0.0, f"category '{category}' is not a supply-side business"
    elif category in SUPPLIER_CATEGORIES:
        conf += 0.45
        reasons.append(f"category '{category}'")
    else:
        conf += 0.15

    core = STRONG_CORE.search(blob)
    weak = STRONG_WEAK.search(blob)
    if core:
        conf += 0.40
        reasons.append(f"says '{core.group(0)}'")
    elif weak:
        conf += 0.18
        reasons.append(f"mentions '{weak.group(0)}'")

    svc = SERVICE_ONLY.search(blob)
    if svc and not core:
        conf -= 0.55
        reasons.append(f"service business, not a goods supplier ('{svc.group(0)}')")

    if NEGATIVE.search(blob) and not core:
        conf -= 0.45
        hit = NEGATIVE.search(blob)
        reasons.append(f"looks consumer-facing ('{hit.group(0)}')" if hit else "consumer-facing")

    if price_signal:
        conf += 0.08
        reasons.append("posts prices")
    if has_shop:
        conf += 0.05
    if delivers:
        conf += 0.05
        reasons.append("delivers")

    conf = max(0.0, min(1.0, conf))
    return conf >= 0.45, round(conf, 2), "; ".join(reasons) or "no strong signal"


# Search terms that find Moroccan suppliers rather than shopfronts.
SUPPLIER_TERMS = [
    "grossiste", "vente en gros", "demi-gros", "fournisseur", "fabricant",
    "usine", "manufacture", "producteur", "distributeur", "importateur",
    "import export", "atelier", "cooperative", "confection", "maroquinerie",
    "artisanat", "huile d'argan", "cosmetique naturel", "emballage", "textile",
    "ceramique", "poterie", "tapis", "epices", "agroalimentaire", "plasturgie",
]

# Cities that matter for sourcing, including the artisan and industrial hubs.
SUPPLIER_CITIES = [
    "Casablanca", "Marrakech", "Fes", "Tanger", "Agadir", "Rabat", "Sale",
    "Meknes", "Tetouan", "Oujda", "Safi", "Essaouira", "Berrechid", "Settat",
    "Mohammedia", "Kenitra", "Taroudant", "Ouarzazate",
]


def build_supplier_queries(terms=None, cities=None, national: bool = True) -> list[str]:
    terms = terms or SUPPLIER_TERMS
    cities = cities or SUPPLIER_CITIES
    out = []
    if national:
        out += [f'site:facebook.com "{t}" "Maroc"' for t in terms]
    out += [f'site:facebook.com "{t}" "{c}"' for t in terms for c in cities]
    return out


# ---------------------------------------------------------------------------
# Dropshipping fitness
#
# Broader than `classify_supplier`: a reseller or a small Facebook shop is fine
# to dropship from, as long as it sells physical goods, ships inside Morocco,
# and is not a pure service. In Morocco the giveaway phrases are "livraison
# partout au Maroc" and "paiement a la livraison" (cash on delivery).
# ---------------------------------------------------------------------------

# Categories whose members sell physical goods.
GOODS_CATEGORIES = {
    "Apparel & Textiles", "Footwear & Leather", "Arts, Crafts & Gifts",
    "Health & Beauty", "Electronics & IT", "Home & Furniture",
    "Machinery & Industrial Supplies", "Chemicals & Plastics",
    "Packaging & Printing", "Office Supplies", "Agriculture",
    "Food & Beverage", "Retail & General Trade", "Sports & Leisure",
    "Construction & Real Estate", "Auto & Transport",
}

RESELLER = re.compile(
    r"(revendeur\w*|reseller|dropship\w*|drop shipping|d[ée]positaire|"
    r"boutique en ligne|online shop|e[- ]?commerce|store|\bshop\b|magasin|"
    r"vente en ligne|concept store|showroom|بائع|متجر|بيع)",
    re.IGNORECASE,
)

# Shipping and cash-on-delivery: the clearest sign a page already fulfils orders.
FULFILMENT = re.compile(
    r"(livraison partout au maroc|livraison dans tout le maroc|livraison au maroc|"
    r"livraison gratuite|nous livrons|paiement [àa] la livraison|"
    r"cash on delivery|\bcod\b|livraison 24h|livraison rapide|"
    r"التوصيل لجميع المدن|الدفع عند الاستلام|توصيل مجاني)",
    re.IGNORECASE,
)

ORDERING = re.compile(
    r"(commande[rz]?\b|pour commander|bon de commande|passez votre commande|"
    r"order now|add to cart|panier|whatsapp pour commander|"
    r"للطلب|اطلب الآن|الطلب عبر)",
    re.IGNORECASE,
)


def classify_dropship(
    name: str = "", category: str = "", raw_category: str = "", about: str = "",
    price_signal: str = "", has_shop: bool = False, delivers: bool = False,
    website: str = "",
) -> tuple[bool, float, str]:
    """Return (usable_for_dropshipping, confidence 0-1, reason)."""
    blob = _norm(" ".join([name, raw_category, about]))
    reasons: list[str] = []
    conf = 0.0

    if category in EXCLUDED_CATEGORIES and not STRONG_CORE.search(blob):
        return False, 0.0, f"category '{category}' does not sell goods"

    svc = SERVICE_ONLY.search(blob)
    if svc and not STRONG_CORE.search(blob) and not RESELLER.search(blob):
        return False, 0.0, f"service business ('{svc.group(0)}')"

    if NEGATIVE.search(blob) and not (STRONG_CORE.search(blob) or FULFILMENT.search(blob)):
        hit = NEGATIVE.search(blob)
        return False, 0.0, f"consumer venue ('{hit.group(0)}')"

    if category in GOODS_CATEGORIES:
        conf += 0.40
        reasons.append(f"sells goods ({category})")
    elif category:
        conf += 0.10

    core = STRONG_CORE.search(blob)
    if core:
        conf += 0.30
        reasons.append(f"wholesale/producer: '{core.group(0)}'")
    resell = RESELLER.search(blob)
    if resell:
        conf += 0.18
        reasons.append(f"retail/reseller: '{resell.group(0)}'")

    ful = FULFILMENT.search(blob)
    if ful or delivers:
        conf += 0.22
        reasons.append("ships in Morocco" if not ful else f"'{ful.group(0)}'")
    if ORDERING.search(blob) or has_shop:
        conf += 0.12
        reasons.append("takes orders")
    if price_signal:
        conf += 0.10
        reasons.append("posts prices")
    if website:
        conf += 0.05

    conf = max(0.0, min(1.0, conf))
    return conf >= 0.45, round(conf, 2), "; ".join(reasons) or "no goods signal"


# Extra search terms aimed at dropship-ready Moroccan sellers.
DROPSHIP_TERMS = [
    "livraison partout au maroc", "paiement a la livraison", "revendeur",
    "dropshipping", "boutique en ligne", "vente en ligne", "commande whatsapp",
    "gros et detail", "demi gros", "destockage", "arrivage",
]


def build_dropship_queries(cities=None) -> list[str]:
    cities = cities or SUPPLIER_CITIES
    out = [f'site:facebook.com "{t}"' for t in DROPSHIP_TERMS]
    out += [f'site:facebook.com "{t}" "{c}"' for t in DROPSHIP_TERMS for c in cities]
    return out
