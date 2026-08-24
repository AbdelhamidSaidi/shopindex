"""Lead quality score, 0.0 - 5.0.

The score answers one question: how useful is this row to someone who wants to
contact and qualify this vendor? Reachability dominates, then commercial
evidence, then how alive the page looks.
"""

from __future__ import annotations

from .models import Vendor

WEIGHTS = {
    "phone": 1.20,       # the single most valuable field for B2B outreach in Morocco
    "whatsapp": 0.35,    # most Moroccan vendors actually sell over WhatsApp
    "website": 0.70,
    "email": 0.35,
    "location": 0.55,    # a resolved city, not just free-text
    "category": 0.40,
    "price_signal": 0.70,
    "activity": 0.45,    # posted recently
    "audience": 0.30,    # follower count as a proxy for being a real business
}
MAX_SCORE = 5.0


def _audience_points(v: Vendor) -> float:
    n = max(v.followers, v.likes)
    if n >= 20_000:
        return 1.0
    if n >= 5_000:
        return 0.8
    if n >= 1_000:
        return 0.6
    if n >= 200:
        return 0.35
    return 0.0


def _activity_points(v: Vendor) -> float:
    d = v.last_post_age_days
    if d is None:
        return 0.0
    if d <= 14:
        return 1.0
    if d <= 45:
        return 0.8
    if d <= 120:
        return 0.5
    if d <= 365:
        return 0.2
    return 0.0


def compute_score(v: Vendor) -> float:
    pts = 0.0
    if v.phone:
        pts += WEIGHTS["phone"]
        if len(v.all_phones) > 1:
            pts += 0.10  # multiple lines: a staffed business
    if v.whatsapp:
        pts += WEIGHTS["whatsapp"]
    if v.website:
        pts += WEIGHTS["website"]
    if v.email:
        pts += WEIGHTS["email"]
    if v.city:
        pts += WEIGHTS["location"]
    elif v.address or v.location:
        pts += WEIGHTS["location"] * 0.5
    if v.category:
        pts += WEIGHTS["category"]

    if v.price_count >= 5:
        pts += WEIGHTS["price_signal"]
    elif v.price_count >= 1:
        pts += WEIGHTS["price_signal"] * 0.6
    elif v.price_signal == "on-request":
        pts += WEIGHTS["price_signal"] * 0.25

    pts += WEIGHTS["activity"] * _activity_points(v)
    pts += WEIGHTS["audience"] * _audience_points(v)

    if v.verified:
        pts += 0.20
    if v.rating and v.reviews >= 5:
        pts += 0.15

    return round(min(pts, MAX_SCORE), 2)


def apply_score(v: Vendor) -> Vendor:
    v.score = compute_score(v)
    return v
