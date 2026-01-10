"""Integration test for Rakuten sourcing and CSV output."""

import os
from pathlib import Path

from dotenv import load_dotenv

from src.sourcing import RakutenClient
from src.models import ListingCandidate
from src.sheets_client import LocalSheetsClient
from src.profit import calculate_profit
from src.models import CandidateRow


def test_rakuten_search_real():
    """Test actual Rakuten API search."""
    load_dotenv()

    app_id = os.getenv("RAKUTEN_APPLICATION_ID")
    affiliate_id = os.getenv("RAKUTEN_AFFILIATE_ID")

    if not app_id:
        print("[WARN] RAKUTEN_APPLICATION_ID not set, skipping real API test")
        return

    client = RakutenClient(application_id=app_id, affiliate_id=affiliate_id)

    # Search for a common item
    result = client.search("Nintendo Switch")

    if result:
        print(f"\n[OK] Rakuten search successful!")
        print(f"   Site: {result.source_site}")
        print(f"   Price: JPY {result.source_price_jpy:,.0f}")
        print(f"   Shipping: JPY {result.source_shipping_jpy:,.0f}")
        print(f"   Stock: {result.stock_hint}")
        print(f"   URL: {result.source_url[:80]}...")
        assert result.source_site == "Rakuten"
        assert result.source_price_jpy > 0
    else:
        print("\n[WARN] No results found from Rakuten")


def test_full_pipeline_rakuten_to_csv():
    """Test full pipeline: Rakuten search -> profit calculation -> CSV output."""
    load_dotenv()

    app_id = os.getenv("RAKUTEN_APPLICATION_ID")
    if not app_id:
        print("[WARN] RAKUTEN_APPLICATION_ID not set, skipping pipeline test")
        return

    # Setup
    client = RakutenClient(application_id=app_id, affiliate_id=os.getenv("RAKUTEN_AFFILIATE_ID"))
    sheets = LocalSheetsClient(base_dir="data/test_output")

    # Create a mock eBay listing
    listing = ListingCandidate(
        candidate_id="TEST-001",
        search_query="Nintendo Switch",
        ebay_item_url="https://ebay.com/itm/test123",
        ebay_price=50.0,
        ebay_shipping=10.0,
        sold_signal=100,
    )

    # Search Rakuten
    offer = client.search(listing.search_query)

    if not offer:
        print("\n[WARN] No Rakuten results, using fallback data for test")
        # Use fallback data for testing
        from src.models import SourceOffer
        offer = SourceOffer(
            source_site="Rakuten",
            source_url="https://item.rakuten.co.jp/test",
            source_price_jpy=3500.0,
            source_shipping_jpy=0.0,
            stock_hint="in_stock",
        )

    print(f"\n[SOURCE] From {offer.source_site}:")
    print(f"   Price: JPY {offer.source_price_jpy:,.0f}")
    print(f"   Total: JPY {offer.source_price_jpy + offer.source_shipping_jpy:,.0f}")

    # Calculate profit
    fee_rules = {
        "fx": {"default_rate": 150.0},
        "fees": {"default": {"percent": 0.12, "fixed": 0.30}},
        "shipping": {"default_jpy": 800},
    }

    profit = calculate_profit(
        ebay_price=listing.ebay_price,
        ebay_shipping=listing.ebay_shipping,
        source_price_jpy=offer.source_price_jpy,
        fee_rules=fee_rules,
    )

    print(f"\n[PROFIT] Analysis:")
    print(f"   Revenue: JPY {(listing.ebay_price + listing.ebay_shipping) * profit.fx_rate:,.0f}")
    print(f"   Cost: JPY {offer.source_price_jpy + 800:,.0f}")
    print(f"   Profit: JPY {profit.profit_jpy_no_rebate:,.0f}")
    print(f"   Margin: {profit.profit_margin_no_rebate * 100:.1f}%")
    print(f"   Profitable: {'[YES]' if profit.is_profitable else '[NO]'}")

    # Create candidate row
    candidate = CandidateRow(
        candidate_id=listing.candidate_id,
        created_at="2024-12-27T13:00:00Z",
        market="UK",
        status="NEW",
        keyword="Nintendo Switch",
        ebay_search_query=listing.search_query,
        ebay_item_url=listing.ebay_item_url,
        ebay_price=listing.ebay_price,
        ebay_shipping=listing.ebay_shipping,
        ebay_currency="USD",
        ebay_category_id="",
        ebay_sold_signal=listing.sold_signal,
        source_site=offer.source_site,
        source_url=offer.source_url,
        source_price_jpy=offer.source_price_jpy,
        source_shipping_jpy=offer.source_shipping_jpy,
        stock_hint=offer.stock_hint,
        fx_rate=profit.fx_rate,
        estimated_weight_kg=profit.estimated_weight_kg,
        estimated_pkg_cm=profit.estimated_pkg_cm,
        profit_jpy_no_rebate=profit.profit_jpy_no_rebate,
        profit_margin_no_rebate=profit.profit_margin_no_rebate,
        profit_jpy_with_rebate=profit.profit_jpy_with_rebate,
        profit_margin_with_rebate=profit.profit_margin_with_rebate,
        is_profitable=profit.is_profitable,
        title_en="",
        description_en="",
        size_weight_block="",
        gpt_model="",
        gpt_prompt_version="v1",
        listing_id="",
        listed_url="",
        listed_at="",
        error_message="",
    )

    # Write to CSV
    sheets.append_candidates([candidate])

    # Verify CSV was created
    csv_path = Path("data/test_output/検索ベース.csv")
    assert csv_path.exists(), f"CSV not created at {csv_path}"

    # Read and display CSV content
    import csv
    with csv_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"\n[CSV] Output: {csv_path}")
    print(f"   Total rows: {len(rows)}")
    if rows:
        last_row = rows[-1]
        print(f"   Latest entry:")
        print(f"     - ID: {last_row.get('candidate_id')}")
        print(f"     - Keyword: {last_row.get('keyword')}")
        print(f"     - Source: {last_row.get('source_site')}")
        print(f"     - Source Price: JPY {last_row.get('source_price_jpy')}")
        print(f"     - Profit: JPY {last_row.get('profit_jpy_no_rebate')}")
        print(f"     - Profitable: {last_row.get('is_profitable')}")

    print(f"\n[OK] Full pipeline test completed!")
