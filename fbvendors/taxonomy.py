"""Map the free-text Facebook Page category (FR/AR/EN) onto a stable taxonomy."""

from __future__ import annotations

import re
import unicodedata

CATEGORIES: dict[str, list[str]] = {
    "Food & Beverage": [
        "restaurant", "cafe", "café", "patisserie", "pâtisserie", "boulangerie", "traiteur",
        "food", "grocery", "epicerie", "épicerie", "alimentation", "fromage", "cheese",
        "boucherie", "poissonnerie", "glacier", "juice", "jus", "miel", "honey", "huile",
        "olive", "epices", "épices", "spices", "pizzeria", "snack", "fast food", "bakery",
        "beverage", "boisson", "مطعم", "مقهى", "حلويات", "مخبزة", "أغذية", "عسل", "زيت",
    ],
    "Apparel & Textiles": [
        "clothing", "vetement", "vêtement", "habillement", "boutique de mode", "fashion",
        "textile", "tissu", "couture", "tailleur", "caftan", "kaftan", "djellaba", "abaya",
        "lingerie", "sportswear", "streetwear", "pret a porter", "prêt-à-porter",
        "ملابس", "أزياء", "قفطان", "خياطة", "نسيج",
    ],
    "Footwear & Leather": [
        "shoe", "chaussure", "footwear", "sneaker", "babouche", "leather", "cuir",
        "maroquinerie", "sac a main", "handbag", "أحذية", "جلد",
    ],
    "Health & Beauty": [
        "beauty", "beaute", "beauté", "cosmetic", "cosmetique", "cosmétique", "parfum",
        "perfume", "salon de coiffure", "coiffure", "hairdresser", "barber", "spa", "hammam",
        "makeup", "maquillage", "skincare", "pharmacie", "pharmacy", "parapharmacie",
        "herboristerie", "argan", "nail", "ongle", "تجميل", "عطور", "حلاقة", "صيدلية", "أركان",
    ],
    "Health & Medical": [
        "clinic", "clinique", "medical", "medecin", "médecin", "doctor", "dentist",
        "dentiste", "laboratoire d'analyse", "kine", "kiné", "physiother", "optic", "optique",
        "hospital", "hopital", "hôpital", "عيادة", "طبيب", "أسنان",
    ],
    "Arts, Crafts & Gifts": [
        "artisan", "handicraft", "craft", "artisanat", "poterie", "pottery", "ceramic",
        "ceramique", "céramique", "tapis", "carpet", "rug", "zellige", "gift", "cadeau",
        "souvenir", "decoration artisanale", "bijoux", "jewelry", "jewellery", "art gallery",
        "galerie", "calligraph", "صناعة تقليدية", "زربية", "فخار", "مجوهرات", "هدايا",
    ],
    "Home & Furniture": [
        "furniture", "meuble", "ameublement", "menuiserie", "salon marocain", "literie",
        "matelas", "mattress", "decoration interieure", "décoration", "home decor", "rideau",
        "cuisine equipee", "cuisine équipée", "electromenager", "électroménager", "appliance",
        "أثاث", "نجارة", "ديكور", "مطبخ",
    ],
    "Construction & Real Estate": [
        "construction", "batiment", "bâtiment", "btp", "immobilier", "real estate",
        "promoteur", "architecte", "architect", "travaux", "renovation", "rénovation",
        "plomberie", "electricite generale", "électricité générale", "peinture batiment",
        "carrelage", "marbre", "marble", "aluminium", "menuiserie aluminium", "quincaillerie",
        "materiaux de construction", "matériaux", "hardware store", "بناء", "عقارات", "أشغال",
    ],
    "Machinery & Industrial Supplies": [
        "machinery", "machine", "industrial", "industrie", "usine", "factory", "manufactur",
        "fabrication", "metallurgie", "métallurgie", "acier", "steel", "fer forge",
        "soudure", "welding", "chaudronnerie", "outillage", "tooling", "hydraulique",
        "pneumatique", "compresseur", "groupe electrogene", "équipement industriel",
        "صناعة", "معدات", "حديد", "لحام",
    ],
    "Auto & Transport": [
        "auto", "automobile", "car dealer", "concessionnaire", "garage", "mecanique",
        "mécanique", "pieces auto", "pièces auto", "spare part", "pneu", "tire", "carrosserie",
        "moto", "motorcycle", "velo", "vélo", "bicycle", "location de voiture", "car rental",
        "transport", "logistique", "logistics", "transitaire", "freight", "camion", "truck",
        "سيارات", "قطع غيار", "نقل", "شحن",
    ],
    "Electronics & IT": [
        "electronic", "electronique", "électronique", "informatique", "computer", "it service",
        "telephone", "téléphone", "smartphone", "gsm", "mobile phone shop", "reparation telephone",
        "web design", "developpement", "développement", "software", "logiciel", "hosting",
        "camera de surveillance", "surveillance", "domotique", "gaming", "console",
        "إلكترونيات", "معلوميات", "هواتف", "برمجة",
    ],
    "Agriculture": [
        "agricultur", "agricole", "ferme", "farm", "elevage", "élevage", "semence", "seed",
        "engrais", "fertilizer", "irrigation", "pepiniere", "pépinière", "nursery", "plant",
        "aviculture", "apicultur", "peche", "pêche", "fishing", "فلاحة", "مزرعة", "تربية",
    ],
    "Chemicals & Plastics": [
        "chimique", "chemical", "plastique", "plastic", "emballage plastique", "resine",
        "peinture industrielle", "detergent", "détergent", "produit d'entretien", "cosmetic lab",
        "كيماويات", "بلاستيك",
    ],
    "Packaging & Printing": [
        "imprimerie", "printing", "print shop", "serigraphie", "sérigraphie", "flocage",
        "packaging", "emballage", "carton", "etiquette", "étiquette", "label", "signaletique",
        "signalétique", "publicite", "publicité", "advertising", "communication visuelle",
        "طباعة", "تغليف", "إشهار",
    ],
    "Office Supplies": [
        "office supply", "fourniture de bureau", "fournitures scolaires", "papeterie",
        "stationery", "librairie", "bookstore", "mobilier de bureau", "bureautique",
        "cartouche", "toner", "photocopie", "لوازم مكتبية", "مكتبة", "قرطاسية",
    ],
    "Energy & Environment": [
        "energie", "énergie", "solaire", "solar", "photovoltaique", "photovoltaïque",
        "panneau solaire", "eolien", "éolien", "recyclage", "recycling", "traitement de l'eau",
        "environnement", "gaz", "petrol", "station service", "طاقة", "شمسية", "تدوير",
    ],
    "Sports & Leisure": [
        "sport", "fitness", "gym", "salle de sport", "musculation", "yoga", "piscine",
        "camping", "randonnee", "randonnée", "equitation", "équitation", "cheval", "horse",
        "quad", "surf", "jeux", "toy", "jouet", "رياضة", "لياقة", "ألعاب",
    ],
    "Travel & Hospitality": [
        "hotel", "hôtel", "riad", "maison d'hotes", "maison d'hôtes", "guest house", "auberge",
        "agence de voyage", "travel agency", "tour operator", "excursion", "circuit",
        "location de vacances", "camping car", "station balneaire", "station balnéaire",
        "complexe hotelier", "complexe hôtelier", "chambre d'hotes", "chambre d’hôtes",
        "hebergement", "hébergement", "resort", "فندق", "رياض", "سياحة", "أسفار",
    ],
    "Business Services": [
        "consulting", "conseil", "comptable", "comptabilite", "comptabilité", "accounting",
        "juridique", "avocat", "lawyer", "notaire", "assurance", "insurance", "courtier",
        "recrutement", "recruitment", "banque", "bank", "service financier",
        "financial service", "credit", "crédit", "microfinance", "bureau de change",
        "traduction", "translation", "marketing", "agence", "agency",
        "استشارات", "محاسبة", "تكوين", "تأمين",
    ],
    "Security & Protection": [
        "securite", "sécurité", "gardiennage", "alarme", "alarm", "coffre fort", "extincteur",
        "protection incendie", "epi", "equipement de protection", "أمن", "حراسة",
    ],
    "Education & Training": [
        "enseignement", "ecole", "école", "school", "lycee", "lycée", "college", "collège",
        "universite", "université", "university", "institut", "academy", "academie",
        "academié", "formation professionnelle", "centre de formation", "creche", "crèche",
        "maternelle", "تعليم", "مدرسة", "جامعة", "معهد",
    ],
    "Public & Government": [
        "gouvernement", "gouvernemental", "government", "administration", "services publics",
        "ministere", "ministère", "prefecture", "préfecture", "commune", "municipalite",
        "municipalité", "consulat", "ambassade", "embassy", "office national", "agence urbaine",
        "حكومة", "إدارة", "وزارة", "جماعة",
    ],
    "Nonprofit & Community": [
        "but non lucratif", "non lucratif", "nonprofit", "non-profit", "ong",
        "organisation non gouvernementale", "association", "fondation", "foundation",
        "organisation religieuse", "religious organization", "charite", "charité",
        "cooperative", "coopérative", "جمعية", "مؤسسة خيرية", "تعاونية",
    ],
    "Arts & Culture": [
        "musee", "musée", "museum", "cinema", "cinéma", "theatre", "théâtre", "centre culturel",
        "cultural center", "galerie d'art", "bibliotheque", "bibliothèque", "library",
        "salle de concert", "festival", "متحف", "سينما", "مسرح", "ثقافي",
    ],
    "Retail & General Trade": [
        "shopping", "retail", "magasin", "boutique", "store", "supermarche", "supermarché",
        "commerce", "grossiste", "wholesaler", "import export", "distributeur", "bazar",
        "centre commercial", "shopping mall", "mall", "souk",
        "متجر", "تجارة", "جملة", "استيراد",
    ],
}

# Categories so broad that a more specific hit in the page name or bio should win.
_FALLBACK_CATS = {"Retail & General Trade", "Business Services"}

_GENERIC = {
    "local business", "entreprise locale", "product/service", "produit/service",
    "service local", "local service", "centre d'interet", "centre d’intérêt",
    "centre d'intérêt", "communaute", "communauté", "community", "site web",
    "personnage public", "public figure", "organisation", "organization",
    "shopping & retail", "商品", "business", "company", "entreprise", "page",
    "brand", "marque", "commercial", "e-commerce website", "site web",
}

_PATTERNS: list[tuple[str, re.Pattern[str]]] = []


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.lower()).strip()


for _cat, _kws in CATEGORIES.items():
    _alts = sorted({_norm(k) for k in _kws}, key=len, reverse=True)
    _PATTERNS.append((_cat, re.compile("|".join(re.escape(a) for a in _alts))))


def classify(*texts: str) -> tuple[str, str]:
    """Return (category, matched_keyword). Earlier arguments carry more weight.

    Pass the Facebook category first, then the page name, then the about text --
    an explicit category beats a keyword that happens to appear in a bio.
    """
    fallback: tuple[str, str] | None = None
    for text in texts:
        n = _norm(text)
        if not n or n in _GENERIC:
            continue
        best: tuple[int, str, str] | None = None
        for cat, pat in _PATTERNS:
            m = pat.search(n)
            if m and (best is None or len(m.group(0)) > best[0]):
                best = (len(m.group(0)), cat, m.group(0))
        if not best:
            continue
        if best[1] in _FALLBACK_CATS:
            fallback = fallback or (best[1], best[2])
            continue   # keep looking for something more specific
        return best[1], best[2]
    return fallback or ("", "")


def tidy_raw_category(raw: str) -> str:
    """Facebook joins multiple categories with a middot; keep them readable."""
    parts = [p.strip() for p in re.split(r"[·•|]", raw or "") if p.strip()]
    return " / ".join(dict.fromkeys(parts))[:120]
