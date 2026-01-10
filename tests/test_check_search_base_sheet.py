"""Check 検索ベース sheet structure."""

import os
from dotenv import load_dotenv
import gspread
from google.oauth2.service_account import Credentials


def test_check_search_base_structure():
    """Check structure of 検索ベース sheet."""
    load_dotenv()

    service_account = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    spreadsheet_url = os.getenv("SHEETS_SPREADSHEET_ID")

    if not service_account or not spreadsheet_url:
        print("[WARN] Credentials not set")
        return

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

    # Check 検索ベース sheet
    search_base = spreadsheet.worksheet("検索ベース")

    print("\n" + "="*60)
    print("検索ベース SHEET ANALYSIS")
    print("="*60)

    # Get all values
    all_values = search_base.get_all_values()
    print(f"\nTotal rows: {len(all_values)}")
    print(f"Total columns: {len(all_values[0]) if all_values else 0}")

    # Show first 20 rows
    print(f"\n[PREVIEW] First 20 rows:")
    for idx, row in enumerate(all_values[:20], 1):
        # Show only non-empty cells
        non_empty = [(i+1, val) for i, val in enumerate(row) if val.strip()]
        if non_empty:
            print(f"Row {idx}: columns {[x[0] for x in non_empty[:10]]}")  # Column numbers only

    # Check for input/output areas
    print(f"\n[B10:I10] (入力エリア?):")
    try:
        b10_i10 = search_base.get("B10:I10")
        if b10_i10:
            print(f"  {b10_i10}")
    except:
        print("  (読み取りエラー)")

    # Check for common patterns
    print(f"\n[PATTERN ANALYSIS]")

    # Check row 1 (header?)
    if all_values:
        row1 = all_values[0]
        filled = [(i+1, val) for i, val in enumerate(row1) if val]
        print(f"Row 1 (Header?): {len(filled)} filled cells")
        print(f"  Filled columns: {[x[0] for x in filled[:20]]}")

    # Check row 10
    if len(all_values) > 9:
        row10 = all_values[9]
        filled = [(i+1, val) for i, val in enumerate(row10) if val]
        print(f"Row 10: {len(filled)} filled cells")
        print(f"  Filled columns: {[x[0] for x in filled[:20]]}")

    # Check column B
    col_b_filled = [(i+1, row[1]) for i, row in enumerate(all_values[:20]) if len(row) > 1 and row[1].strip()]
    print(f"\nColumn B (first 20): {len(col_b_filled)} filled cells")
    print(f"  Filled rows: {[x[0] for x in col_b_filled]}")

    # Find filled cells
    print(f"\n[DATA DISTRIBUTION]")
    for row_idx, row in enumerate(all_values[:30], 1):
        filled_cols = [i+1 for i, val in enumerate(row) if val.strip()]
        if len(filled_cols) > 3:  # Rows with more than 3 filled cells
            print(f"  Row {row_idx}: {len(filled_cols)} cells filled, columns: {filled_cols[:10]}")


if __name__ == "__main__":
    test_check_search_base_structure()
