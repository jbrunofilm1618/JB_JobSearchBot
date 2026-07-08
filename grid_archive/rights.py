"""Rights classifier: map each source's free-text rights / license URL onto a
small set of tiers the pipeline can enforce.

The film is COMMERCIAL use (brand content on X/LinkedIn), so:
  * usable as-is ..... public_domain, attribution (CC-BY — credit in post info)
  * hard-gated ....... restricted (any NC/ND/in-copyright/ARR) and share_alike
                       (SA imposes share-alike terms on the film itself)
  * verify ........... unknown (no statement, or "see provider") — kept and
                       judged, badged for manual checking before use

Clip length is irrelevant: there is no "under 2 seconds" rights exemption.
Classification is heuristic string matching over heterogeneous archive
metadata — the manifest always carries the raw rights text too, so any call
this module makes can be double-checked by a human.
"""

from __future__ import annotations

TIER_PUBLIC_DOMAIN = "public_domain"
TIER_ATTRIBUTION = "attribution"
TIER_SHARE_ALIKE = "share_alike"
TIER_RESTRICTED = "restricted"
TIER_UNKNOWN = "unknown"

#: tiers usable in the film without further clearance work
USABLE_TIERS = frozenset({TIER_PUBLIC_DOMAIN, TIER_ATTRIBUTION})
#: tiers the judge skips and fetch-masters refuses (without --force)
HARD_GATED_TIERS = frozenset({TIER_RESTRICTED, TIER_SHARE_ALIKE})

# Checked FIRST: unambiguous "free of copyright" phrasings that would otherwise
# false-positive on substrings like "in copyright".
_PD_STRONG = (
    "no known restrictions", "no known copyright", "not in copyright",
    "rightsstatements.org/vocab/noc", "publicdomain", "public domain", "cc0",
    "united states government work", "us government work", "unrestricted",
    "copyrighted free use",
)
# Anything here is unusable for commercial work.
_RESTRICTED = (
    "by-nc", "noncommercial", "non-commercial", "by-nd", "noderiv",
    "rightsstatements.org/vocab/inc", "in copyright", "all rights reserved",
    "fair use", "non-free", "permission required", "access_restricted",
    "orphan work", "copyright not evaluated", "vocab/cne", "vocab/und",
)
_SHARE_ALIKE = ("by-sa", "sharealike", "share-alike", "share alike", "gfdl")
_ATTRIBUTION = ("creativecommons.org/licenses/by/", "cc by", "cc-by", "attribution")

_UNKNOWN_LITERALS = {"", "unspecified", "see provider"}


def classify_rights(text: str) -> str:
    """Classify a rights statement / license URL into a tier."""
    t = (text or "").strip().lower()
    if t in _UNKNOWN_LITERALS:
        return TIER_UNKNOWN
    if any(m in t for m in _PD_STRONG):
        return TIER_PUBLIC_DOMAIN
    if any(m in t for m in _RESTRICTED):
        return TIER_RESTRICTED
    if any(m in t for m in _SHARE_ALIKE):
        return TIER_SHARE_ALIKE
    if any(m in t for m in _ATTRIBUTION):
        return TIER_ATTRIBUTION
    return TIER_UNKNOWN


def ensure_tier(item) -> str:
    """Return the item's rights tier, computing and caching it if absent.
    Used everywhere a gate is enforced, so rows created by older versions of
    the manifest get classified on first touch."""
    if not getattr(item, "rights_tier", ""):
        item.rights_tier = classify_rights(item.rights)
    return item.rights_tier
