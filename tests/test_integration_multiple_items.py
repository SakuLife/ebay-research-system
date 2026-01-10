"""Integration test for multiple item search and CSV output."""

import os
from pathlib import Path

from dotenv import load_dotenv

from src.sourcing import RakutenClient
from src.models import ListingCandidate, SourceOffer
from src.sheets_client import LocalSheetsClient
from src.profit import calculate_profit
from src.models import CandidateRow


def test_multiple_items_pipeline():
    """Test searching multiple items and outputting to CSV."""
    load_dotenv()

    app_id = os.getenv("RAKUTEN_APPLICATION_ID")
    if not app_id:
        print("[WARN] RAKUTEN_APPLICATION_ID not set, skipping test")
        return

    client = RakutenClient(application_id=app_id, affiliate_id=os.getenv("RAKUTEN_AFFILIATE_ID"))
    sheets = LocalSheetsClient(base_dir="data/test_output2")

    # Test multiple search keywords
    search_keywords = [
        ("Pokemon card", 150.0),  # eBay price
        ("Keyboard mechanical", 80.0),
        ("USB cable", 15.0),
    ]

    candidates = []
    fee_rules = {
        "fx": {"default_rate": 150.0},
        "fees": {"default": {"percent": 0.12, "fixed": 0.30}},
        "shipping": {"default_jpy": 800},
    }

    print("\n" + "="*60)
    print("MULTI-ITEM SEARCH TEST")
    print("="*60)

    for idx, (keyword, ebay_price) in enumerate(search_keywords, 1):
        print(f"\n[{idx}] Searching: {keyword}")
        print("-" * 60)

        # Search Rakuten
        offer = client.search(keyword)

        if not offer:
            print(f"   [SKIP] No results from Rakuten for '{keyword}'")
            continue

        print(f"   [SOURCE] Rakuten")
        print(f"      Price: JPY {offer.source_price_jpy:,.0f}")
        print(f"      URL: {offer.source_url[:60]}...")

        # Create listing candidate
        listing = ListingCandidate(
            candidate_id=f"TEST-{idx:03d}",
            search_query=keyword,
            ebay_item_url=f"https://ebay.com/itm/test{idx}",
            ebay_price=ebay_price,
            ebay_shipping=10.0,
            sold_signal=50 + idx * 10,
        )

        # Calculate profit
        profit = calculate_profit(
            ebay_price=listing.ebay_price,
            ebay_shipping=listing.ebay_shipping,
            source_price_jpy=offer.source_price_jpy,
            fee_rules=fee_rules,
        )

        revenue = (listing.ebay_price + listing.ebay_shipping) * profit.fx_rate
        cost = offer.source_price_jpy + 800

        print(f"   [PROFIT]")
        print(f"      Revenue: JPY {revenue:,.0f}")
        print(f"      Cost: JPY {cost:,.0f}")
        print(f"      Profit: JPY {profit.profit_jpy_no_rebate:,.0f}")
        print(f"      Margin: {profit.profit_margin_no_rebate * 100:.1f}%")
        print(f"      Status: {'[PROFITABLE]' if profit.is_profitable else '[NOT PROFITABLE]'}")

        # Create candidate row
        candidate = CandidateRow(
            candidate_id=listing.candidate_id,
            created_at="2024-12-27T13:30:00Z",
            market="UK",
            status="NEW",
            keyword=keyword,
            ebay_search_query=keyword,
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

        candidates.append(candidate)

    # Write all candidates to CSV
    if candidates:
        sheets.append_candidates(candidates)

        csv_path = Path("data/test_output2/検索ベース.csv")
        print(f"\n" + "="*60)
        print(f"[CSV] Output: {csv_path}")
        print(f"      Total candidates: {len(candidates)}")

        # Verify and show results
        import csv
        with csv_path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        print(f"      CSV rows (excluding header): {len(rows)}")

        profitable_count = sum(1 for r in rows if r.get('is_profitable') == 'True')
        print(f"      Profitable items: {profitable_count}/{len(rows)}")

        print(f"\n      Summary:")
        for row in rows:
            profit_val = float(row.get('profit_jpy_no_rebate', 0))
            status = "[PROFIT]" if profit_val > 0 else "[LOSS]"
            print(f"         {status} {row.get('keyword'):30s} -> JPY {profit_val:>8,.0f}")

        print(f"\n[OK] Multi-item pipeline test completed!")
    else:
        print(f"\n[WARN] No candidates found")
