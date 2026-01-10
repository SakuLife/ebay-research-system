"""Read row 5 from input sheet."""

import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

from src.sheets_client import GoogleSheetsClient


def test_read_row_5():
    """Read row 5 from input sheet."""
    service_account = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    spreadsheet_url = os.getenv("SHEETS_SPREADSHEET_ID")

    sheets_client = GoogleSheetsClient(
        service_account_file=service_account,
        spreadsheet_url=spreadsheet_url
    )

    print("="*60)
    print("ROW 5 CHECK")
    print("="*60)

    worksheet = sheets_client.spreadsheet.worksheet("入力シート")

    # Get row 5
    row_5 = worksheet.row_values(5)

    print(f"\n[ROW 5] Total columns: {len(row_5)}")

    # Show B column (eBay URL)
    if len(row_5) > 1 and row_5[1]:
        print(f"\nB5 (eBay URL): {row_5[1]}")
    else:
        print(f"\nB5 (eBay URL): (empty)")

    return row_5


if __name__ == "__main__":
    row = test_read_row_5()
    if len(row) > 1 and row[1]:
        print(f"\n[EXECUTE] python -m src.github_actions_runner --ebay-url \"{row[1]}\" --row 5")
