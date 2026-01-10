"""Test row 4 from input sheet."""

import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

from src.sheets_client import GoogleSheetsClient

def test_read_row_4():
    """Read row 4 from input sheet."""
    service_account = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    spreadsheet_url = os.getenv("SHEETS_SPREADSHEET_ID")

    sheets_client = GoogleSheetsClient(
        service_account_file=service_account,
        spreadsheet_url=spreadsheet_url
    )

    print("="*60)
    print("ROW 4 CHECK")
    print("="*60)

    worksheet = sheets_client.spreadsheet.worksheet("入力シート")

    # Get row 4
    row_4 = worksheet.row_values(4)

    print(f"\n[ROW 4] Total columns: {len(row_4)}")

    # Show important columns
    if len(row_4) > 1 and row_4[1]:
        print(f"\nB列 (起点eBay URL): {row_4[1]}")
    else:
        print(f"\nB列 (起点eBay URL): (empty)")

    # Show all non-empty cells
    print(f"\n[NON-EMPTY CELLS IN ROW 4]")
    for idx, val in enumerate(row_4, 1):
        if val and val.strip():
            print(f"  Column {idx}: {val[:80]}...")

    return row_4


if __name__ == "__main__":
    test_read_row_4()
