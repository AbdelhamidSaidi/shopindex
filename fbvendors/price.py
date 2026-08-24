"""Extract a price signal from Moroccan Facebook commerce posts.

Vendors here quote in dirhams a dozen different ways -- "250dh", "1 200 DHS",
"٢٥٠ درهم", "MAD 300", "de 250 a 400 dh". This module pulls those out, throws
away the things that look like prices but are not (phone numbers, years,
delivery fees), and reduces what is left to a few comparable numbers.
"""

from __future__ import annotations

import re
import statistics
import unicodedata
from dataclasses import dataclass, field

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

# Plausibility window in MAD. Anything outside is noise, not a consumer price.
MIN_PRICE = 5.0
MAX_PRICE = 2_000_000.0

_CUR = r"(?:dhs?|dh\.?|mad|درهم|د\.?م\.?|dirhams?)"
_NUM = r"\d{1,3}(?:[ \u00a0.,]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?"

# Currency after the number ("250 dh") or before it ("MAD 250").
_PRICE_RE = re.compile(
    rf"(?P<pre>{_CUR})?\s*(?<!\d)(?P<num>{_NUM})(?!\d)\s*(?P<post>{_CUR})?",
    re.IGNORECASE,
)

_RANGE_RE = re.compile(
    rf"(?:de|entre|from|من)?\s*(?<!\d)(?P<a>{_NUM})\s*(?:a|à|-|–|to|jusqu'?a|jusqu'?à|الى|إلى)\s*"
    rf"(?P<b>{_NUM})(?!\d)\s*(?P<cur>{_CUR})",
    re.IGNORECASE,
)

# A price right after these is a shipping fee, not the product price.
_DELIVERY_CTX = re.compile(
    r"(livraison|frais de port|shipping|توصيل|الشحن)\D{0,18}$", re.IGNORECASE
)

_ON_REQUEST = re.compile(
    r"(prix\s*(sur|par)\s*(demande|message|mp|inbox)|prix\s*en\s*(mp|dm|inbox)|"
    r"contactez[- ]nous\s*pour\s*(le\s*)?prix|price\s*on\s*request|"
    r"الثمن\s*(في|عبر)?\s*(الخاص|الرسائل)|تواصل\s*معنا\s*للثمن)",
    re.IGNORECASE,
)

_DELIVERS = re.compile(
    r"(livraison|livrer|nous livrons|shipping|delivery|توصيل|التوصيل|الشحن|"
    r"paiement\s*a\s*la\s*livraison|cash\s*on\s*delivery|الدفع\s*عند\s*الاستلام)",
    re.IGNORECASE,
)

_SHOP = re.compile(
    r"(commander|commandez|acheter|achetez|bon de commande|panier|boutique en ligne|"
    r"notre site|order now|shop now|add to cart|اطلب|الطلب|للطلب|اشتري)",
    re.IGNORECASE,
)

_WHOLESALE = re.compile(
    r"(\bgros\b|grossiste|wholesale|demi[- ]gros|prix\s*de\s*gros|جملة)",
    re.IGNORECASE,
)


@dataclass
class PriceStats:
    values: list[float] = field(default_factory=list)
    currency: str = "MAD"
    on_request: bool = False
    delivers: bool = False
    has_shop: bool = False
    wholesale: bool = False
    delivery_fees: list[float] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.values)

    def summary(self, robust: bool = True) -> dict[str, float | int | str]:
        if not self.values:
            return {}
        vals = sorted(self.values)
        lo, hi = vals[0], vals[-1]
        if robust and len(vals) >= 8:
            # One outlier post (a car among the phone cases) should not define the range.
            lo = _percentile(vals, 0.10)
            hi = _percentile(vals, 0.90)
        return {
            "min": round(lo, 2),
            "max": round(hi, 2),
            "median": round(statistics.median(vals), 2),
            "count": len(vals),
            "currency": self.currency,
        }

    def signal(self, robust: bool = True) -> str:
        s = self.summary(robust=robust)
        if not s:
            return "on-request" if self.on_request else ""
        lo, hi, med, n = s["min"], s["max"], s["median"], s["count"]
        rng = f"{lo:g}" if lo == hi else f"{lo:g}-{hi:g}"
        out = f"MAD {rng} (med {med:g}; n={n})"
        if self.wholesale:
            out += " [wholesale]"
        return out


def _percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _parse_number(raw: str) -> float | None:
    """'1 200' -> 1200, '1.200' -> 1200, '250,50' -> 250.5, '1,200.50' -> 1200.5."""
    s = raw.replace(" ", " ").strip()
    if not s:
        return None
    s = re.sub(r"(?<=\d)\s+(?=\d{3}\b)", "", s)  # space thousands separator
    if "." in s and "," in s:  # both present: the last one is the decimal mark
        dec = "." if s.rfind(".") > s.rfind(",") else ","
        thou = "," if dec == "." else "."
        s = s.replace(thou, "").replace(dec, ".")
    else:
        for sep in (".", ","):
            if sep in s:
                tail = s.rsplit(sep, 1)[1]
                if len(tail) == 3 and s.count(sep) >= 1 and len(s.split(sep)[0]) <= 3:
                    s = s.replace(sep, "")   # 1.200 -> thousands
                else:
                    s = s.replace(sep, ".")  # 250,50 -> decimal
    try:
        return float(s)
    except ValueError:
        return None


def _plausible(v: float | None) -> bool:
    return v is not None and MIN_PRICE <= v <= MAX_PRICE


def _normalize_text(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").translate(_ARABIC_DIGITS)
    return t.replace("‏", "").replace("‎", "")


def extract_prices(text: str) -> PriceStats:
    """Scan one blob of post text and return everything price-related in it."""
    stats = PriceStats()
    t = _normalize_text(text)
    if not t:
        return stats

    stats.on_request = bool(_ON_REQUEST.search(t))
    stats.delivers = bool(_DELIVERS.search(t))
    stats.has_shop = bool(_SHOP.search(t))
    stats.wholesale = bool(_WHOLESALE.search(t))

    consumed: list[tuple[int, int]] = []

    # Ranges first ("de 250 a 400 dh") so the endpoints are not double counted.
    for m in _RANGE_RE.finditer(t):
        a, b = _parse_number(m.group("a")), _parse_number(m.group("b"))
        if _plausible(a) and _plausible(b) and a <= b:
            stats.values.extend([a, b])
            consumed.append(m.span())

    for m in _PRICE_RE.finditer(t):
        if not (m.group("pre") or m.group("post")):
            continue  # a bare number is not a price
        if any(s <= m.start() < e for s, e in consumed):
            continue
        num_raw = m.group("num")
        if len(re.sub(r"\D", "", num_raw)) > 8:
            continue  # phone-number-shaped
        val = _parse_number(num_raw)
        if not _plausible(val):
            continue
        if _DELIVERY_CTX.search(t[max(0, m.start() - 40): m.start()]):
            stats.delivery_fees.append(val)
            continue
        stats.values.append(val)

    return stats


def aggregate(texts: list[str]) -> PriceStats:
    """Fold the per-post results for a whole Page into one signal."""
    total = PriceStats()
    for chunk in texts:
        s = extract_prices(chunk)
        total.values.extend(s.values)
        total.delivery_fees.extend(s.delivery_fees)
        total.on_request |= s.on_request
        total.delivers |= s.delivers
        total.has_shop |= s.has_shop
        total.wholesale |= s.wholesale
    return total
