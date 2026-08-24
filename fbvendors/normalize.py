"""Normalisers for the messy values Facebook Pages carry: phones, links, emails."""

from __future__ import annotations

import re
import urllib.parse as up

# Digits Facebook renders in Arabic-Indic form on some pages.
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

# National significant number: 9 digits. 5=fixed, 6/7=mobile, 8=special/toll.
_VALID_HEAD = "5678"

_PHONE_CANDIDATE = re.compile(
    r"(?:(?:\+|00)\s?212|\b0)[\s\-./]?(\d[\s\-./]?){8,9}\d"
)

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")

# Domains that are social presence, not a vendor's own website.
_SOCIAL_HOSTS = {
    "facebook.com", "fb.com", "fb.me", "m.facebook.com", "web.facebook.com",
    "instagram.com", "wa.me", "api.whatsapp.com", "whatsapp.com", "chat.whatsapp.com",
    "youtube.com", "youtu.be", "tiktok.com", "twitter.com", "x.com", "t.me",
    "linkedin.com", "pinterest.com", "snapchat.com", "linktr.ee", "messenger.com",
    "maps.google.com", "goo.gl", "google.com", "bit.ly",
}

_TRACKING_PARAMS = re.compile(r"^(fbclid|utm_[a-z_]+|gclid|mc_[a-z]+|ref|ref_src|_ga)$", re.I)


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s.translate(_ARABIC_DIGITS))


def normalize_phone(raw: str) -> str:
    """Return a Moroccan national number as 9 digits (e.g. '661754248'), or ''.

    Accepts +212 6 61 75 42 48, 00212661754248, 0661-754-248, 0661754248.
    """
    if not raw:
        return ""
    d = _digits(raw)
    if not d:
        return ""
    for prefix in ("00212", "212"):
        if d.startswith(prefix):
            d = d[len(prefix):]
            break
    d = d.lstrip("0")
    if len(d) != 9 or d[0] not in _VALID_HEAD:
        return ""
    return d


def find_phones(text: str) -> list[str]:
    """Pull every plausible Moroccan number out of free text, de-duplicated, in order."""
    if not text:
        return []
    text = text.translate(_ARABIC_DIGITS)
    out: list[str] = []
    for m in _PHONE_CANDIDATE.finditer(text):
        n = normalize_phone(m.group(0))
        if n and n not in out:
            out.append(n)
    return out


def find_emails(text: str) -> list[str]:
    out: list[str] = []
    for m in _EMAIL_RE.finditer(text or ""):
        e = m.group(0).lower()
        # Facebook embeds these in tracking payloads; they are never the vendor.
        if e.endswith((".png", ".jpg", ".gif")) or "facebook.com" in e or "sentry" in e:
            continue
        if e not in out:
            out.append(e)
    return out


def unwrap_fb_link(url: str) -> str:
    """Facebook rewrites outbound links as l.facebook.com/l.php?u=<encoded>."""
    if not url:
        return ""
    try:
        parsed = up.urlsplit(url)
    except ValueError:
        return ""
    if parsed.netloc.endswith("facebook.com") and parsed.path in ("/l.php", "/flx/warn/"):
        qs = up.parse_qs(parsed.query)
        for key in ("u", "url", "next"):
            if qs.get(key):
                return unwrap_fb_link(up.unquote(qs[key][0]))
    return url


def clean_url(url: str) -> str:
    """Canonicalise a URL and strip tracking noise. Returns '' if unusable."""
    url = unwrap_fb_link((url or "").strip())
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    if not re.match(r"^https?://", url, re.I):
        if not re.match(r"^[\w.\-]+\.[a-z]{2,}", url, re.I):
            return ""
        url = "https://" + url
    try:
        p = up.urlsplit(url)
    except ValueError:
        return ""
    host = p.netloc.lower().split("@")[-1]
    if not host or "." not in host:
        return ""
    host = host.removeprefix("www.")
    query = up.urlencode(
        [(k, v) for k, v in up.parse_qsl(p.query, keep_blank_values=False)
         if not _TRACKING_PARAMS.match(k)]
    )
    path = p.path.rstrip("/") if p.path != "/" else "/"
    return up.urlunsplit(("https", host, path or "/", query, ""))


def root_host(url: str) -> str:
    try:
        return up.urlsplit(url).netloc.lower().removeprefix("www.")
    except ValueError:
        return ""


def is_social(url: str) -> bool:
    host = root_host(url)
    return any(host == s or host.endswith("." + s) for s in _SOCIAL_HOSTS)


def whatsapp_from_url(url: str) -> str:
    """wa.me/212661754248 or api.whatsapp.com/send?phone=... -> 661754248."""
    url = unwrap_fb_link(url or "")
    host = root_host(url)
    if "whatsapp" not in host and host != "wa.me":
        return ""
    try:
        p = up.urlsplit(url)
    except ValueError:
        return ""
    cand = up.parse_qs(p.query).get("phone", [""])[0] or p.path
    return normalize_phone(cand)


def clean_name(raw: str) -> str:
    """Trim the ' | Facebook' / ' - Home' suffixes off page titles."""
    n = re.sub(r"\s+", " ", (raw or "")).strip()
    n = re.sub(r"\s*[|\-–]\s*(Facebook|Home|Accueil|About|À propos)\s*$", "", n, flags=re.I)
    n = re.sub(r"^\(\d+\)\s*", "", n)  # unread-count prefix
    return n.strip(" -|·")


def normalize_slug(url_or_slug: str) -> str:
    """Reduce any Page URL to its stable slug or numeric id."""
    s = (url_or_slug or "").strip()
    if "facebook.com" in s:
        try:
            p = up.urlsplit(s if re.match(r"^https?://", s) else "https://" + s)
        except ValueError:
            return ""
        qs = up.parse_qs(p.query)
        if qs.get("id"):
            return qs["id"][0]
        parts = [seg for seg in p.path.split("/") if seg]
        if not parts:
            return ""
        if parts[0] == "p" and len(parts) >= 2:
            # /p/Some-Business-Name-61565864440334 -> trailing numeric id
            tail = re.search(r"(\d{8,})$", parts[1])
            return tail.group(1) if tail else parts[1]
        if parts[0] in ("pages", "pg"):
            parts = parts[1:]
            # /pages/Some-Name/1234567 -> the numeric id is the stable identifier
            if len(parts) >= 2 and parts[1].isdigit() and len(parts[1]) >= 5:
                return parts[1]
        if parts and parts[0] == "profile.php":
            return qs.get("id", [""])[0]
        s = parts[0] if parts else ""
    s = s.split("?")[0].strip("/")
    return s if re.match(r"^[A-Za-z0-9.\-_]{2,}$", s) else ""


def page_url(slug: str) -> str:
    return f"https://www.facebook.com/{slug}" if slug else ""
