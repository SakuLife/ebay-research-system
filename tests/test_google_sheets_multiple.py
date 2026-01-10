"""Test writing multiple items to Google Sheets."""

import os
from pathlib import Path

from dotenv import load_dotenv

from src.sourcing import RakutenClient
from src.models import ListingCandidate, CandidateRow
from src.sheets_client import GoogleSheetsClient
from src.profit import calculate_profit


def test_google_sheets_multiple_items():
    """Test writing multiple items from Rakuten to Google Sheets."""
    load_dotenv()

    service_account = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    spreadsheet_url = os.getenv("SHEETS_SPREADSHEET_ID")
    rakuten_app_id = os.getenv("RAKUTEN_APPLICATION_ID")

    if not service_account or not spreadsheet_url or not rakuten_app_id:
        print("[WARN] Required credentials not set, skipping test")
        return

    if not Path(service_account).exists():
        print(f"[WARN] Service account file not found: {service_account}")
        return

    print("\n" + "="*60)
    print("GOOGLE SHEETS MULTI-ITEM TEST")
    print("="*60)

    # Initialize clients
    sheets = GoogleSheetsClient(
        service_account_file=service_account,
        spreadsheet_url=spreadsheet_url,
    )
    rakuten = RakutenClient(
        application_id=rakuten_app_id,
        affiliate_id=os.getenv("RAKUTEN_AFFILIATE_ID")
    )

    print(f"[OK] Connected to: {sheets.spreadsheet.title}")

    # Get initial row count
    worksheet = sheets._get_or_create_worksheet(sheets.candidates_name)
    initial_count = len(worksheet.get_all_values())
    print(f"[INFO] Initial rows: {initial_count}")

    # Search for multiple items
    search_items = [
        ("Wireless mouse", 25.0),
        ("USB hub", 20.0),
        ("Phone case", 15.0),
    ]

    candidates = []
    fee_rules = {
        "fx": {"default_rate": 150.0},
        "fees": {"default": {"percent": 0.12, "fixed": 0.30}},
        "shipping": {"default_jpy": 800},
    }

    print(f"\n[SEARCH] Searching Rakuten...")

    for idx, (keyword, ebay_price) in enumerate(search_items, 1):
        print(f"\n[{idx}] {keyword}")

        offer = rakuten.search(keyword)
        if not offer:
            print(f"   [SKIP] No results")
            continue

        print(f"   Price: JPY {offer.source_price_jpy:,.0f}")

        listing = ListingCandidate(
            candidate_id=f"MULTI-{idx:03d}",
            search_query=keyword,
            ebay_item_url=f"https://ebay.com/itm/multi{idx}",
            ebay_price=ebay_price,
            ebay_shipping=5.0,
            sold_signal=50 + idx * 10,
        )

        profit = calculate_profit(
            ebay_price=listing.ebay_price,
            ebay_shipping=listing.ebay_shipping,
            source_price_jpy=offer.source_price_jpy,
            fee_rules=fee_rules,
        )

        print(f"   Profit: JPY {profit.profit_jpy_no_rebate:,.0f} ({profit.profit_margin_no_rebate * 100:.1f}%)")

        candidate = CandidateRow(
            candidate_id=listing.candidate_id,
            created_at="2024-12-27T14:30:00Z",
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

    # Write to Google Sheets
    if candidates:
        print(f"\n[WRITE] Writing {len(candidates)} items to Google Sheets...")
        sheets.append_candidates(candidates)

        # Verify
        final_count = len(worksheet.get_all_values())
        added_count = final_count - initial_count

        print(f"[OK] Successfully added {added_count} rows")
        print(f"[OK] Total rows now: {final_count}")
        print(f"\n[SUCCESS] Check your spreadsheet:")
        print(f"   {spreadsheet_url}")
    else:
        print(f"\n[WARN] No candidates to write")
