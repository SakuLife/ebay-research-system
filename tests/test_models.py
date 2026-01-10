"""Tests for data models."""

from src.models import (
    CandidateRow,
    GptListing,
    ListedRow,
    ListingCandidate,
    ListingRequest,
    ListingResult,
    ProfitResult,
    SourceOffer,
)


def test_listing_candidate_creation():
    """Test creating a ListingCandidate instance."""
    candidate = ListingCandidate(
        candidate_id="test-123",
        search_query="gaming mouse",
        ebay_item_url="https://ebay.com/item/123",
        ebay_price=29.99,
        ebay_shipping=5.99,
        sold_signal=100,
    )
    assert candidate.candidate_id == "test-123"
    assert candidate.search_query == "gaming mouse"
    assert candidate.ebay_price == 29.99
    assert candidate.sold_signal == 100


def test_source_offer_creation():
    """Test creating a SourceOffer instance."""
    offer = SourceOffer(
        source_site="mercari",
        source_url="https://mercari.com/item/456",
        source_price_jpy=2000.0,
        source_shipping_jpy=500.0,
        stock_hint="in stock",
    )
    assert offer.source_site == "mercari"
    assert offer.source_price_jpy == 2000.0
    assert offer.stock_hint == "in stock"


def test_profit_result_creation():
    """Test creating a ProfitResult instance."""
    result = ProfitResult(
        fx_rate=150.0,
        estimated_weight_kg=0.5,
        estimated_pkg_cm="20x15x10",
        profit_jpy_no_rebate=1500.0,
        profit_margin_no_rebate=0.3,
        profit_jpy_with_rebate=1800.0,
        profit_margin_with_rebate=0.36,
        is_profitable=True,
    )
    assert result.fx_rate == 150.0
    assert result.is_profitable is True
    assert result.profit_margin_no_rebate == 0.3


def test_gpt_listing_creation():
    """Test creating a GptListing instance."""
    listing = GptListing(
        title_en="Gaming Mouse - RGB LED",
        description_en="High-quality gaming mouse with RGB LED lighting",
        size_weight_block="Weight: 100g, Size: 12x8x4cm",
    )
    assert "Gaming Mouse" in listing.title_en
    assert "RGB LED" in listing.description_en
    assert "Weight" in listing.size_weight_block


def test_listing_request_creation():
    """Test creating a ListingRequest instance."""
    request = ListingRequest(
        candidate_id="test-123",
        title_en="Gaming Mouse",
        description_en="Description here",
        size_weight_block="Weight: 100g",
        price="29.99",
        currency="USD",
    )
    assert request.candidate_id == "test-123"
    assert request.price == "29.99"
    assert request.currency == "USD"


def test_listing_result_creation():
    """Test creating a ListingResult instance."""
    result = ListingResult(
        listing_id="ebay-789",
        listed_url="https://ebay.com/itm/789",
        error_message="",
    )
    assert result.listing_id == "ebay-789"
    assert result.error_message == ""


def test_candidate_row_creation():
    """Test creating a CandidateRow instance."""
    row = CandidateRow(
        candidate_id="test-123",
        created_at="2024-01-01",
        market="ebay_us",
        status="pending",
        keyword="gaming",
        ebay_search_query="gaming mouse",
        ebay_item_url="https://ebay.com/item/123",
        ebay_price=29.99,
        ebay_shipping=5.99,
        ebay_currency="USD",
        ebay_category_id="175673",
        ebay_sold_signal=100,
        source_site="mercari",
        source_url="https://mercari.com/item/456",
        source_price_jpy=2000.0,
        source_shipping_jpy=500.0,
        stock_hint="in stock",
        fx_rate=150.0,
        estimated_weight_kg=0.5,
        estimated_pkg_cm="20x15x10",
        profit_jpy_no_rebate=1500.0,
        profit_margin_no_rebate=0.3,
        profit_jpy_with_rebate=1800.0,
        profit_margin_with_rebate=0.36,
        is_profitable=True,
        title_en="Gaming Mouse",
        description_en="Description",
        size_weight_block="Weight: 100g",
        gpt_model="gpt-4",
        gpt_prompt_version="v1",
        listing_id="",
        listed_url="",
        listed_at="",
        error_message="",
    )
    assert row.candidate_id == "test-123"
    assert row.market == "ebay_us"
    assert row.is_profitable is True


def test_listed_row_creation():
    """Test creating a ListedRow instance."""
    row = ListedRow(
        candidate_id="test-123",
        listed_at="2024-01-01 12:00:00",
        listing_id="ebay-789",
        listed_url="https://ebay.com/itm/789",
        error_message="",
    )
    assert row.candidate_id == "test-123"
    assert row.listing_id == "ebay-789"
    assert row.error_message == ""
