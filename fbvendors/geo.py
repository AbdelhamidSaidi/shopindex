"""Morocco city -> region lookup, with the spelling variants that show up on Facebook."""

from __future__ import annotations

import re
import unicodedata

# Current (post-2015) 12-region naming. The legacy b2bmap export uses the older
# region names; we do not try to reproduce those.
CITY_REGION: dict[str, tuple[str, str]] = {}


def _add(canonical: str, region: str, *variants: str) -> None:
    for v in (canonical, *variants):
        CITY_REGION[_key(v)] = (canonical, region)


def _key(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("'", " ").replace("-", " ").replace("_", " ")
    return re.sub(r"\s+", " ", s).strip()


_CAS = "Casablanca-Settat"
_RSK = "Rabat-Sale-Kenitra"
_MS = "Marrakech-Safi"
_FM = "Fes-Meknes"
_TTA = "Tanger-Tetouan-Al Hoceima"
_SM = "Souss-Massa"
_ORI = "L'Oriental"
_BMK = "Beni Mellal-Khenifra"
_DT = "Draa-Tafilalet"
_GON = "Guelmim-Oued Noun"
_LSH = "Laayoune-Sakia El Hamra"
_DOD = "Dakhla-Oued Ed-Dahab"

_add("Casablanca", _CAS, "casa", "dar el beida", "ad-dar-al-bayda", "الدار البيضاء", "casablanca maroc")
_add("Mohammedia", _CAS, "المحمدية")
_add("El Jadida", _CAS, "el jadida", "الجديدة", "mazagan")
_add("Settat", _CAS, "سطات")
_add("Berrechid", _CAS, "برشيد")
_add("Benslimane", _CAS, "ben slimane")
_add("Bouskoura", _CAS)
_add("Nouaceur", _CAS)
_add("Sidi Bennour", _CAS)
_add("Rabat", _RSK, "ar-ribat", "الرباط")
_add("Sale", _RSK, "salé", "سلا")
_add("Temara", _RSK, "temara", "تمارة", "tamesna")
_add("Kenitra", _RSK, "القنيطرة")
_add("Skhirat", _RSK)
_add("Khemisset", _RSK, "الخميسات")
_add("Sidi Kacem", _RSK)
_add("Sidi Slimane", _RSK)
_add("Marrakech", _MS, "marrakesh", "مراكش", "marrakech menara")
_add("Safi", _MS, "asfi", "اسفي")
_add("Essaouira", _MS, "الصويرة", "mogador")
_add("El Kelaa des Sraghna", _MS, "el kelaa", "kelaa sraghna")
_add("Youssoufia", _MS)
_add("Chichaoua", _MS)
_add("Fes", _FM, "fès", "fez", "فاس", "fas")
_add("Meknes", _FM, "meknès", "مكناس")
_add("Ifrane", _FM, "إفران")
_add("Taza", _FM, "تازة")
_add("Sefrou", _FM)
_add("El Hajeb", _FM)
_add("Tanger", _TTA, "tangier", "tangiers", "طنجة")
_add("Tetouan", _TTA, "tétouan", "تطوان")
_add("Al Hoceima", _TTA, "alhoceima", "الحسيمة")
_add("Larache", _TTA, "العرائش")
_add("Chefchaouen", _TTA, "chaouen", "شفشاون")
_add("Ksar El Kebir", _TTA, "ksar el kbir")
_add("Fnideq", _TTA, "fnidq")
_add("Martil", _TTA)
_add("Asilah", _TTA, "arzila")
_add("Agadir", _SM, "أكادير", "agadir ida outanane")
_add("Inezgane", _SM, "inzegane")
_add("Ait Melloul", _SM, "aït melloul")
_add("Taroudant", _SM, "تارودانت")
_add("Tiznit", _SM, "تزنيت")
_add("Ouarzazate", _DT, "ورزازات")
_add("Errachidia", _DT, "الرشيدية")
_add("Tinghir", _DT, "tinerhir")
_add("Zagora", _DT, "زاكورة")
_add("Midelt", _DT)
_add("Oujda", _ORI, "وجدة")
_add("Nador", _ORI, "الناظور")
_add("Berkane", _ORI, "بركان")
_add("Taourirt", _ORI)
_add("Jerada", _ORI)
_add("Driouch", _ORI)
_add("Beni Mellal", _BMK, "بني ملال")
_add("Khouribga", _BMK, "خريبكة")
_add("Khenifra", _BMK, "خنيفرة")
_add("Fquih Ben Salah", _BMK, "fkih ben salah")
_add("Azilal", _BMK)
_add("Guelmim", _GON, "كلميم")
_add("Tan-Tan", _GON, "tantan")
_add("Sidi Ifni", _GON)
_add("Laayoune", _LSH, "laâyoune", "العيون")
_add("Dakhla", _DOD, "الداخلة")

# Longest keys first so "sidi bennour" wins over "sidi".
_SORTED_KEYS = sorted(CITY_REGION, key=len, reverse=True)
_CITY_RE = re.compile(r"(?<![a-z])(" + "|".join(re.escape(k) for k in _SORTED_KEYS) + r")(?![a-z])")


def lookup_city(text: str) -> tuple[str, str]:
    """Find a Moroccan city in free text. Returns (city, region), or ('', '')."""
    if not text:
        return "", ""
    hit = _CITY_RE.search(_key(text))
    if not hit:
        return "", ""
    return CITY_REGION[hit.group(1)]


def format_location(city: str, region: str, fallback: str = "") -> str:
    if city and region:
        return f"{city}, {region}"
    if city:
        return city
    return fallback.strip()


def looks_moroccan(text: str) -> bool:
    """Cheap geo filter used to drop non-Morocco pages during discovery."""
    if not text:
        return False
    k = _key(text)
    if _CITY_RE.search(k):
        return True
    # \b cannot anchor before "+", so the phone prefixes need their own alternatives.
    return bool(re.search(r"\b(maroc|morocco|marocaine?|المغرب)\b|\+\s?212|\b00212", k))
