"""Check actual spreadsheet headers."""

import os
from dotenv import load_dotenv
import gspread
from google.oauth2.service_account import Credentials


def test_check_headers():
    """Check headers in the spreadsheet."""
    load_dotenv()

    service_account = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    spreadsheet_url = os.getenv("SHEETS_SPREADSHEET_ID")

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

    # Check both sheets
    for sheet_name in ["検索ベース", "入力シート"]:
        try:
            worksheet = spreadsheet.worksheet(sheet_name)
            print(f"\n{'='*60}")
            print(f"SHEET: {sheet_name}")
            print(f"{'='*60}")

            all_values = worksheet.get_all_values()
            if len(all_values) > 0:
                headers = all_values[0]
                print(f"\nTotal columns: {len(headers)}")
                print(f"\nHeaders:")
                for idx, header in enumerate(headers, 1):
                    if header:  # Only show non-empty headers
                        print(f"  {idx:2d}. {header}")

                # Show a sample data row if exists
                if len(all_values) > 1:
                    # Find first non-empty row
                    for row_idx in range(1, min(len(all_values), 10)):
                        row = all_values[row_idx]
                        if any(cell.strip() for cell in row):
                            print(f"\nSample data row (row {row_idx + 1}):")
                            for idx, (header, value) in enumerate(zip(headers, row), 1):
                                if value and value.strip():
                                    print(f"  {idx:2d}. {header}: {value}")
                            break
            else:
                print("  (Empty sheet)")

        except gspread.WorksheetNotFound:
            print(f"\nSheet '{sheet_name}' not found!")
