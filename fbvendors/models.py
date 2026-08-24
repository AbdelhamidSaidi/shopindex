"""Core record type and the output schema.

The columns are driven by what a Facebook Page actually exposes: identity,
the "Page transparency"/About block, the contact rail, and whatever the recent
posts reveal about pricing and activity.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import date
from typing import Any

CSV_COLUMNS = [
    # identity
    "name",
    "kind",
    "category",
    "location",
    # contact
    "website",
    "phone",
    "whatsapp",
    "email",
    "address",
    # commercial signal
    "price_signal",
    "price_min",
    "price_max",
    "price_median",
    "price_count",
    "currency",
    "has_shop",
    "delivers",
    # audience / activity
    "followers",
    "likes",
    "rating",
    "reviews",
    "verified",
    "last_post_date",
    "posts_scanned",
    # context
    "about",
    "source",
    "score",
    "facebook_url",
    "page_id",
    "scraped_at",
]

_INT_COLUMNS = {"price_count", "followers", "likes", "reviews", "posts_scanned"}
_BOOL_COLUMNS = {"has_shop", "delivers", "verified"}


@dataclass
class Vendor:
    """One vendor row. Only the page identity is guaranteed to be populated."""

    # identity
    name: str = ""
    kind: str = ""          # facebook page | online shop | b2b supplier | supplier | ...
    category: str = ""
    location: str = ""

    # contact
    website: str = ""
    phone: str = ""
    whatsapp: str = ""
    email: str = ""
    address: str = ""

    # commercial signal
    price_signal: str = ""
    price_min: float | None = None
    price_max: float | None = None
    price_median: float | None = None
    price_count: int = 0
    currency: str = ""
    has_shop: bool = False
    delivers: bool = False

    # audience / activity
    followers: int = 0
    likes: int = 0
    rating: float | None = None
    reviews: int = 0
    verified: bool = False
    last_post_date: str = ""
    posts_scanned: int = 0

    # context
    about: str = ""
    source: str = "facebook.com"
    score: float = 0.0
    facebook_url: str = ""
    page_id: str = ""
    scraped_at: str = ""

    # --- retained for scoring / debugging, not written to the CSV ---
    slug: str = ""
    raw_category: str = ""
    city: str = ""
    region: str = ""
    all_phones: list[str] = field(default_factory=list)
    last_post_age_days: int | None = None
    fetch_status: str = ""
    notes: list[str] = field(default_factory=list)

    def to_row(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        row: dict[str, Any] = {}
        for col in CSV_COLUMNS:
            val = d.get(col, "")
            if val is None:
                row[col] = ""
            elif col in _BOOL_COLUMNS:
                row[col] = "yes" if val else ""
            elif col in _INT_COLUMNS:
                row[col] = str(val) if val else ""
            elif col == "score":
                row[col] = f"{val:.1f}" if val else ""
            elif col in ("price_min", "price_max", "price_median"):
                row[col] = f"{val:g}" if val else ""
            elif col == "rating":
                row[col] = f"{val:.1f}" if val else ""
            elif isinstance(val, str):
                # keep cells single-line so the file survives naive TSV readers
                row[col] = " ".join(val.split())
            else:
                row[col] = str(val)
        return row

    def stamp(self) -> None:
        if not self.scraped_at:
            self.scraped_at = date.today().isoformat()

    def is_usable(self) -> bool:
        """Worth keeping if we have a name plus at least one way to reach them."""
        return bool(self.name) and bool(self.phone or self.website or self.email or self.whatsapp)

    def dedupe_keys(self) -> list[str]:
        """Identities that mean 'this is the same vendor' across sources."""
        keys = []
        if self.page_id:
            keys.append(f"pid:{self.page_id}")
        if self.facebook_url:
            keys.append(f"fb:{self.facebook_url.lower()}")
        if self.slug:
            keys.append(f"slug:{self.slug.lower()}")
        if self.phone:
            keys.append(f"tel:{self.phone}")
        if self.website:
            keys.append(f"web:{self.website.lower()}")
        return keys
