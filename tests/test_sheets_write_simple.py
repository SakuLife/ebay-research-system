"""Simple test to write data to Google Sheets.

WARNING: このテストは本番スプレッドシート(検索ベース)に直接書き込みます。
手動でのみ実行してください。
"""

import os
from dotenv import load_dotenv
import gspread
import pytest
from google.oauth2.service_account import Credentials

from src.models import CandidateRow


@pytest.mark.skip(reason="本番スプレッドシートに書き込むため、手動実行のみ")
def test_simple_write():
    """Test simple write to Google Sheets."""
    load_dotenv()

    service_account = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    spreadsheet_url = os.getenv("SHEETS_SPREADSHEET_ID")

    # Extract spreadsheet ID
    if "/d/" in spreadsheet_url:
        spreadsheet_id = spreadsheet_url.split("/d/")[1].split("/")[0]
    else:
        spreadsheet_id = spreadsheet_url

    # Authenticate
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(service_account, scopes=scopes)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(spreadsheet_id)

    # Get sheet
    sheet_name = "検索ベース"
    worksheet = spreadsheet.worksheet(sheet_name)

    print(f"\n[INFO] Current rows: {len(worksheet.get_all_values())}")

    # Create test candidate
    candidate = CandidateRow(
        candidate_id="TEST-SIMPLE-001",
        created_at="2024-12-27T15:00:00Z",
        market="UK",
        status="NEW",
        keyword="Test Keyword",
        ebay_search_query="test query",
        ebay_item_url="https://ebay.com/test",
        ebay_price=100.0,
        ebay_shipping=10.0,
        ebay_currency="USD",
        ebay_category_id="12345",
        ebay_sold_signal=50,
        source_site="Rakuten",
        source_url="https://rakuten.co.jp/test",
        source_price_jpy=5000.0,
        source_shipping_jpy=500.0,
        stock_hint="in_stock",
        fx_rate=150.0,
        estimated_weight_kg=0.5,
        estimated_pkg_cm="20x15x10",
        profit_jpy_no_rebate=5000.0,
        profit_margin_no_rebate=0.25,
        profit_jpy_with_rebate=5500.0,
        profit_margin_with_rebate=0.27,
        is_profitable=True,
        title_en="Test Title",
        description_en="Test Description",
        size_weight_block="Size block",
        gpt_model="gpt-4",
        gpt_prompt_version="v1",
        listing_id="",
        listed_url="",
        listed_at="",
        error_message="",
    )

    # Convert to dict and check
    print(f"\n[DEBUG] Candidate as dict:")
    candidate_dict = candidate.__dict__
    print(f"  Keys: {list(candidate_dict.keys())[:5]}...")
    print(f"  Values: {list(candidate_dict.values())[:5]}...")

    # Prepare row data
    HEADERS = [
        "candidate_id", "created_at", "market", "status", "keyword",
        "ebay_search_query", "ebay_item_url", "ebay_price", "ebay_shipping",
        "ebay_currency", "ebay_category_id", "ebay_sold_signal",
        "source_site", "source_url", "source_price_jpy", "source_shipping_jpy",
        "stock_hint", "fx_rate", "estimated_weight_kg", "estimated_pkg_cm",
        "profit_jpy_no_rebate", "profit_margin_no_rebate",
        "profit_jpy_with_rebate", "profit_margin_with_rebate", "is_profitable",
        "title_en", "description_en", "size_weight_block",
        "gpt_model", "gpt_prompt_version",
        "listing_id", "listed_url", "listed_at", "error_message",
    ]

    values = []
    for field in HEADERS:
        val = getattr(candidate, field, "")
        values.append(val)
        if field in ["candidate_id", "keyword", "source_site", "profit_jpy_no_rebate"]:
            print(f"  {field}: {val}")

    print(f"\n[DEBUG] Row to append:")
    print(f"  Length: {len(values)}")
    print(f"  First 5: {values[:5]}")

    # Append row
    print(f"\n[WRITE] Appending row...")
    worksheet.append_row(values)

    # Verify
    all_values = worksheet.get_all_values()
    print(f"\n[OK] Total rows now: {len(all_values)}")

    if len(all_values) > 0:
        last_row = all_values[-1]
        print(f"\n[VERIFY] Last row:")
        print(f"  Length: {len(last_row)}")
        print(f"  candidate_id: {last_row[0] if len(last_row) > 0 else 'N/A'}")
        print(f"  keyword: {last_row[4] if len(last_row) > 4 else 'N/A'}")
        print(f"  source_site: {last_row[12] if len(last_row) > 12 else 'N/A'}")
        print(f"  profit: {last_row[20] if len(last_row) > 20 else 'N/A'}")

    print(f"\n[OK] Write test completed!")
