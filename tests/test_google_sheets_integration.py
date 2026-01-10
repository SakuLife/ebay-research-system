"""Integration test for Google Sheets."""

import os
from pathlib import Path

from dotenv import load_dotenv

from src.sourcing import RakutenClient
from src.models import ListingCandidate, CandidateRow
from src.sheets_client import GoogleSheetsClient
from src.profit import calculate_profit


def test_google_sheets_write():
    """Test writing to actual Google Sheets."""
    load_dotenv()

    service_account = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    spreadsheet_url = os.getenv("SHEETS_SPREADSHEET_ID")
    rakuten_app_id = os.getenv("RAKUTEN_APPLICATION_ID")

    if not service_account or not spreadsheet_url:
        print("[WARN] Google Sheets credentials not set, skipping test")
        return

    if not Path(service_account).exists():
        print(f"[WARN] Service account file not found: {service_account}")
        return

    print("\n" + "="*60)
    print("GOOGLE SHEETS INTEGRATION TEST")
    print("="*60)
    print(f"Service Account: {service_account}")
    print(f"Spreadsheet URL: {spreadsheet_url}")

    # Initialize Google Sheets client
    try:
        sheets = GoogleSheetsClient(
            service_account_file=service_account,
            spreadsheet_url=spreadsheet_url,
        )
        print(f"[OK] Connected to Google Sheets")
        print(f"     Spreadsheet ID: {sheets.spreadsheet_id}")
        print(f"     Title: {sheets.spreadsheet.title}")
    except Exception as e:
        print(f"[ERROR] Failed to connect to Google Sheets: {e}")
        return

    # Get data from Rakuten
    if rakuten_app_id:
        client = RakutenClient(
            application_id=rakuten_app_id,
            affiliate_id=os.getenv("RAKUTEN_AFFILIATE_ID")
        )
        offer = client.search("Gaming headset")

        if offer:
            print(f"\n[SOURCE] Rakuten search successful")
            print(f"   Price: JPY {offer.source_price_jpy:,.0f}")
            source_price = offer.source_price_jpy
            source_url = offer.source_url
            source_site = offer.source_site
        else:
            print(f"\n[WARN] No Rakuten results, using test data")
            source_price = 3500.0
            source_url = "https://item.rakuten.co.jp/test"
            source_site = "Rakuten"
    else:
        print(f"\n[WARN] Rakuten not configured, using test data")
        source_price = 3500.0
        source_url = "https://item.rakuten.co.jp/test"
        source_site = "Rakuten"

    # Create test candidate
    listing = ListingCandidate(
        candidate_id="GSHEET-TEST-001",
        search_query="Gaming headset test",
        ebay_item_url="https://ebay.com/itm/gsheet-test",
        ebay_price=75.0,
        ebay_shipping=10.0,
        sold_signal=80,
    )

    # Calculate profit
    fee_rules = {
        "fx": {"default_rate": 150.0},
        "fees": {"default": {"percent": 0.12, "fixed": 0.30}},
        "shipping": {"default_jpy": 800},
    }

    profit = calculate_profit(
        ebay_price=listing.ebay_price,
        ebay_shipping=listing.ebay_shipping,
        source_price_jpy=source_price,
        fee_rules=fee_rules,
    )

    print(f"\n[PROFIT]")
    print(f"   Profit: JPY {profit.profit_jpy_no_rebate:,.0f}")
    print(f"   Margin: {profit.profit_margin_no_rebate * 100:.1f}%")

    # Create candidate row
    candidate = CandidateRow(
        candidate_id=listing.candidate_id,
        created_at="2024-12-27T14:00:00Z",
        market="UK",
        status="NEW",
        keyword="Gaming headset",
        ebay_search_query=listing.search_query,
        ebay_item_url=listing.ebay_item_url,
        ebay_price=listing.ebay_price,
        ebay_shipping=listing.ebay_shipping,
        ebay_currency="USD",
        ebay_category_id="",
        ebay_sold_signal=listing.sold_signal,
        source_site=source_site,
        source_url=source_url,
        source_price_jpy=source_price,
        source_shipping_jpy=0.0,
        stock_hint="in_stock",
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

    # Write to Google Sheets
    print(f"\n[WRITE] Writing to Google Sheets...")
    try:
        sheets.append_candidates([candidate])
        print(f"[OK] Successfully wrote to sheet: {sheets.candidates_name}")

        # Verify by reading back
        worksheet = sheets._get_or_create_worksheet(sheets.candidates_name)
        all_values = worksheet.get_all_values()
        print(f"[OK] Sheet now has {len(all_values)} rows (including header)")

        if len(all_values) > 1:
            last_row = all_values[-1]
            print(f"\n[VERIFY] Last row in sheet:")
            print(f"   Candidate ID: {last_row[0] if len(last_row) > 0 else 'N/A'}")
            print(f"   Keyword: {last_row[4] if len(last_row) > 4 else 'N/A'}")
            print(f"   Source: {last_row[12] if len(last_row) > 12 else 'N/A'}")
            print(f"   Profit: JPY {last_row[20] if len(last_row) > 20 else 'N/A'}")

        print(f"\n[OK] Google Sheets integration test completed!")
        print(f"Check your spreadsheet: {spreadsheet_url}")

    except Exception as e:
        print(f"[ERROR] Failed to write to Google Sheets: {e}")
        raise
