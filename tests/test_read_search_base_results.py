"""検索ベースシートの計算結果を確認するテスト."""

import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

from src.sheets_client import GoogleSheetsClient


def test_read_search_base_results():
    """検索ベースシートの計算結果を読み取る."""
    service_account = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    spreadsheet_url = os.getenv("SHEETS_SPREADSHEET_ID")

    sheets_client = GoogleSheetsClient(
        service_account_file=service_account,
        spreadsheet_url=spreadsheet_url
    )

    print("="*60)
    print("SEARCH BASE SHEET - CALCULATION RESULTS")
    print("="*60)

    worksheet = sheets_client.spreadsheet.worksheet("検索ベース")

    # B10:M10 の入力データを確認
    print("\n[INPUT DATA] B10:M10:")
    input_data = worksheet.get("B10:M10")
    if input_data:
        for idx, row in enumerate(input_data, 10):
            for col_idx, val in enumerate(row, 2):  # B列から
                if val:
                    col_name = chr(64 + col_idx)  # A=65, B=66, etc.
                    print(f"  {col_name}{idx}: {val}")

    # O10:R30 の計算結果を確認（広めに取得）
    print("\n[CALCULATION RESULTS] O10:R30:")
    results = worksheet.get("O10:R30")
    if results:
        for idx, row in enumerate(results, 10):
            if row and any(row):  # 空行でない
                # 数値が入っている行だけ表示
                has_number = False
                for val in row:
                    if val and isinstance(val, (int, float, str)):
                        try:
                            # 数値または数値っぽい文字列をチェック
                            if isinstance(val, (int, float)) or (isinstance(val, str) and any(c.isdigit() for c in val)):
                                has_number = True
                                break
                        except:
                            pass

                if has_number:
                    print(f"  Row {idx}:")
                    labels = ["O", "P", "Q", "R"]
                    for col_idx, val in enumerate(row):
                        if val:
                            print(f"    {labels[col_idx]}{idx}: {val}")

    # E10とJ10の数式を確認
    print("\n[FORMULAS] E10, J10:")
    e10 = worksheet.acell("E10", value_render_option="FORMULA").value
    j10 = worksheet.acell("J10", value_render_option="FORMULA").value
    print(f"  E10: {e10}")
    print(f"  J10: {j10}")


if __name__ == "__main__":
    test_read_search_base_results()
