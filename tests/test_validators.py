from src.models import ListingCandidate
from src.validators import is_blocked_listing


def test_blocked_keywords():
    """Test that blocked keywords are detected."""
    listing = ListingCandidate(
        candidate_id="1",
        search_query="Best Perfume Set",
        ebay_item_url="https://example.com",
        ebay_price=10.0,
        ebay_shipping=1.0,
        sold_signal=50,
    )
    categories = {"blocked_keywords": ["perfume"]}
    assert is_blocked_listing(listing, categories) is True


def test_not_blocked_keywords():
    """Test that non-blocked keywords pass validation."""
    listing = ListingCandidate(
        candidate_id="2",
        search_query="Gaming Mouse RGB",
        ebay_item_url="https://example.com",
        ebay_price=25.0,
        ebay_shipping=5.0,
        sold_signal=100,
    )
    categories = {"blocked_keywords": ["perfume", "alcohol"]}
    assert is_blocked_listing(listing, categories) is False


def test_empty_blocked_keywords():
    """Test with empty blocked keywords list."""
    listing = ListingCandidate(
        candidate_id="3",
        search_query="Any Product",
        ebay_item_url="https://example.com",
        ebay_price=20.0,
        ebay_shipping=3.0,
        sold_signal=75,
    )
    categories = {"blocked_keywords": []}
    assert is_blocked_listing(listing, categories) is False


def test_no_blocked_keywords_key():
    """Test when blocked_keywords key is missing."""
    listing = ListingCandidate(
        candidate_id="4",
        search_query="Random Item",
        ebay_item_url="https://example.com",
        ebay_price=15.0,
        ebay_shipping=2.0,
        sold_signal=60,
    )
    categories = {}
    assert is_blocked_listing(listing, categories) is False


def test_case_insensitive_blocking():
    """Test that blocking is case-insensitive."""
    listing = ListingCandidate(
        candidate_id="5",
        search_query="PERFUME Gift Set",
        ebay_item_url="https://example.com",
        ebay_price=30.0,
        ebay_shipping=5.0,
        sold_signal=80,
    )
    categories = {"blocked_keywords": ["perfume"]}
    assert is_blocked_listing(listing, categories) is True


def test_multiple_blocked_keywords():
    """Test with multiple blocked keywords."""
    listing = ListingCandidate(
        candidate_id="6",
        search_query="Wine Bottle Opener",
        ebay_item_url="https://example.com",
        ebay_price=12.0,
        ebay_shipping=2.0,
        sold_signal=40,
    )
    categories = {"blocked_keywords": ["perfume", "wine", "alcohol"]}
    assert is_blocked_listing(listing, categories) is True


def test_partial_word_match():
    """Test that partial word matches are detected."""
    listing = ListingCandidate(
        candidate_id="7",
        search_query="Luxury Perfumery Collection",
        ebay_item_url="https://example.com",
        ebay_price=50.0,
        ebay_shipping=10.0,
        sold_signal=120,
    )
    categories = {"blocked_keywords": ["perfume"]}
    assert is_blocked_listing(listing, categories) is True
