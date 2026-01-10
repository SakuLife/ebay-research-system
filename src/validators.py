"""Listing validation rules."""

from __future__ import annotations

from typing import Dict

from .models import ListingCandidate


def is_blocked_listing(listing: ListingCandidate, categories: Dict) -> bool:
    blocked_keywords = [k.lower() for k in categories.get("blocked_keywords", [])]
    title = listing.search_query.lower()
    return any(word in title for word in blocked_keywords)
