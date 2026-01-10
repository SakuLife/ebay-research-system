"""Test writing to 入力シート in correct format."""

import os
from datetime import datetime
from dotenv import load_dotenv
import gspread
from google.oauth2.service_account import Credentials

from src.sourcing import RakutenClient
from src.profit import calculate_profit


def test_write_to_input_sheet():
    """Write real Rakuten data to 入力シート."""
    load_dotenv()

    service_account = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    spreadsheet_url = os.getenv("SHEETS_SPREADSHEET_ID")
    rakuten_app_id = os.getenv("RAKUTEN_APPLICATION_ID")

    if not service_account or not spreadsheet_url:
        print("[WARN] Credentials not set")
        return

    print("\n" + "="*60)
    print("WRITE TO 入力シート TEST")
    print("="*60)

    # Connect to spreadsheet
    if "/d/" in spreadsheet_url:
        spreadsheet_id = spreadsheet_url.split("/d/")[1].split("/")[0]
    else:
        spreadsheet_id = spreadsheet_url

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(service_account, scopes=scopes)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(spreadsheet_id)

    input_sheet = spreadsheet.worksheet("入力シート")
    print(f"[OK] Connected to sheet")

    # Get current row count
    all_values = input_sheet.get_all_values()
    current_rows = len(all_values)
    print(f"[INFO] Current rows: {current_rows}")

    # Search Rakuten
    if not rakuten_app_id:
        print("[WARN] Rakuten not configured")
        return

    rakuten = RakutenClient(
        application_id=rakuten_app_id,
        affiliate_id=os.getenv("RAKUTEN_AFFILIATE_ID")
    )

    # Test with multiple searches
    search_keywords = [
        ("Gaming mouse", 30.0, 5.0),   # keyword, ebay_price, shipping
        ("USB cable type-c", 12.0, 3.0),
    ]

    for keyword, ebay_price, ebay_shipping in search_keywords:
        print(f"\n[SEARCH] {keyword}")

        # Search Rakuten
        offer = rakuten.search(keyword)
        if not offer:
            print(f"  [SKIP] No results")
            continue

        print(f"  [FOUND] Price: JPY {offer.source_price_jpy:,.0f}")

        # Calculate profit
        fee_rules = {
            "fx": {"default_rate": 150.0},
            "fees": {"default": {"percent": 0.12, "fixed": 0.30}},
            "shipping": {"default_jpy": 800},
        }

        profit = calculate_profit(
            ebay_price=ebay_price,
            ebay_shipping=ebay_shipping,
            source_price_jpy=offer.source_price_jpy,
            fee_rules=fee_rules,
        )

        print(f"  [PROFIT] JPY {profit.profit_jpy_no_rebate:,.0f} ({profit.profit_margin_no_rebate * 100:.1f}%)")

        # Prepare row data (34 columns matching 入力シート)
        row_data = [
            datetime.now().strftime("%Y-%m-%d"),  # 1. 追加日
            "",  # 2. 元eBay URL
            keyword,  # 3. 外サーチ文字
            "AUTO",  # 4. 外サーチ方法
            "UK",  # 5. 対象国
            "",  # 6. 発送
            "New",  # 7. Condition
            "Fixed price",  # 8. Format
            str(ebay_price),  # 9. Price min
            keyword,  # 10. キーワード
            "",  # 11. カテゴリ(表示用)
            "",  # 12. カテゴリNo
            "",  # 13. 元商品情報
            "AUTO",  # 14. 出品文作成エン
            offer.source_url,  # 15. ソーシング1 URL
            str(offer.source_price_jpy),  # 16. ソーシング1 価格
            "",  # 17. ソーシング2 URL
            "",  # 18. ソーシング2 価格
            "",  # 19. ソーシング3 URL
            "",  # 20. ソーシング3 価格
            "",  # 21. 対応eBay URL
            str(ebay_price),  # 22. eBay参考価格
            str(ebay_shipping),  # 23. eBay送料
            str(profit.estimated_weight_kg),  # 24. 推定重量
            profit.estimated_pkg_cm,  # 25. 梱包サイズ
            "",  # 26. 梱包重量 修正
            "",  # 27. 体積重量 修正
            str(profit.profit_jpy_no_rebate),  # 28. リベートなし利益
            str(profit.profit_margin_no_rebate * 100),  # 29. 利益率%
            str(profit.profit_jpy_with_rebate),  # 30. リベート後利益
            str(profit.profit_margin_with_rebate * 100),  # 31. 利益率%
            "候補",  # 32. ステータス
            "",  # 33. ログ内容
            f"AUTO - {offer.source_site}",  # 34. 備考
        ]

        print(f"  [WRITE] Appending to sheet...")
        input_sheet.append_row(row_data)
        print(f"  [OK] Row added")

    # Verify
    final_values = input_sheet.get_all_values()
    final_rows = len(final_values)
    added = final_rows - current_rows

    print(f"\n[RESULT]")
    print(f"  Initial rows: {current_rows}")
    print(f"  Final rows: {final_rows}")
    print(f"  Added: {added}")

    # Show last row
    if final_rows > 1:
        last_row = final_values[-1]
        print(f"\n[LAST ROW]")
        print(f"  追加日: {last_row[0]}")
        print(f"  キーワード: {last_row[9]}")
        print(f"  ソーシング1 URL: {last_row[14][:50]}...")
        print(f"  ソーシング1 価格: JPY {last_row[15]}")
        print(f"  利益予想: JPY {last_row[27]}")
        print(f"  ステータス: {last_row[31]}")

    print(f"\n[SUCCESS] Check your spreadsheet:")
    print(f"  {spreadsheet_url}")
