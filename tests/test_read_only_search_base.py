"""検索ベースシートから計算結果を読み取るだけのテスト."""

import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

from src.sheets_client import GoogleSheetsClient
from src.search_base_client import SearchBaseClient


def test_read_only():
    """検索ベースシートから計算結果を読み取るだけ."""
    service_account = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    spreadsheet_url = os.getenv("SHEETS_SPREADSHEET_ID")

    sheets_client = GoogleSheetsClient(
        service_account_file=service_account,
        spreadsheet_url=spreadsheet_url
    )

    search_base_client = SearchBaseClient(sheets_client)

    print("="*60)
    print("SEARCH BASE SHEET - READ ONLY TEST")
    print("="*60)

    # 入力データ確認
    worksheet = sheets_client.spreadsheet.worksheet("検索ベース")

    print("\n[INPUT DATA]")
    b10 = worksheet.acell("B10").value
    c10 = worksheet.acell("C10").value
    d10 = worksheet.acell("D10").value
    k9 = worksheet.acell("K9").value

    # Clean values for display
    b10_clean = str(b10).replace('\xa5', 'JPY ') if b10 else ""
    c10_clean = str(c10).replace('\xa5', '$') if c10 else ""
    d10_clean = str(d10).replace('\xa5', '$') if d10 else ""

    print(f"  B10 (source price): {b10_clean}")
    print(f"  C10 (sell price): {c10_clean}")
    print(f"  D10 (shipping): {d10_clean}")
    print(f"  K9 (eBay URL): {k9}")

    # 計算結果を読み取る（待機なし）
    print("\n[CALCULATION RESULTS]")
    result = search_base_client.read_calculation_results(max_wait_seconds=0)

    if result:
        print(f"\n[SUCCESS] 計算結果取得成功:")
        print(f"  業者: {result['carrier']}")
        print(f"  発送方法: {result['shipping_method']}")
        print(f"  還付抜き利益: JPY {result['profit_no_rebate']:,.0f}")
        print(f"  還付抜き利益率: {result['margin_no_rebate']:.1f}%")
        print(f"  還付あり利益: JPY {result['profit_with_rebate']:,.0f}")
        print(f"  還付あり利益率: {result['margin_with_rebate']:.1f}%")
    else:
        print("\n[WARN] 計算結果が取得できませんでした")

        # 詳細確認
        print("\n[DEBUG] N10:Q13 の値:")
        debug_range = worksheet.get("N10:Q13")
        if debug_range:
            for idx, row in enumerate(debug_range, 10):
                if row and any(row):
                    print(f"  Row {idx}: {row}")


if __name__ == "__main__":
    test_read_only()
