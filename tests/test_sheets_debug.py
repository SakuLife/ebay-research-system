"""Debug Google Sheets connection and data."""

import os
from dotenv import load_dotenv
import gspread
from google.oauth2.service_account import Credentials


def test_sheets_connection_debug():
    """Debug Google Sheets connection."""
    load_dotenv()

    service_account = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    spreadsheet_url = os.getenv("SHEETS_SPREADSHEET_ID")

    print("\n" + "="*60)
    print("GOOGLE SHEETS DEBUG")
    print("="*60)

    # Extract spreadsheet ID
    if "/d/" in spreadsheet_url:
        spreadsheet_id = spreadsheet_url.split("/d/")[1].split("/")[0]
    else:
        spreadsheet_id = spreadsheet_url

    print(f"Service Account: {service_account}")
    print(f"Spreadsheet ID: {spreadsheet_id}")

    # Authenticate
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(service_account, scopes=scopes)
    client = gspread.authorize(creds)

    # Open spreadsheet
    spreadsheet = client.open_by_key(spreadsheet_id)
    print(f"\n[OK] Opened spreadsheet: {spreadsheet.title}")

    # List all worksheets
    print(f"\n[WORKSHEETS]")
    for ws in spreadsheet.worksheets():
        print(f"  - {ws.title} ({ws.row_count} rows x {ws.col_count} cols)")

    # Check specific worksheets
    find_sheet_name = os.getenv("SHEETS_NAME_FIND", "検索ベース")
    input_sheet_name = os.getenv("SHEETS_NAME_INPUT", "入力シート")

    print(f"\n[CHECK] Looking for sheets:")
    print(f"  Find sheet: '{find_sheet_name}'")
    print(f"  Input sheet: '{input_sheet_name}'")

    # Try to access find sheet
    try:
        find_ws = spreadsheet.worksheet(find_sheet_name)
        print(f"\n[OK] Found sheet: '{find_sheet_name}'")
        print(f"  Rows: {find_ws.row_count}")
        print(f"  Cols: {find_ws.col_count}")

        # Get all values
        all_values = find_ws.get_all_values()
        print(f"  Data rows: {len(all_values)}")

        if len(all_values) > 0:
            print(f"\n[HEADER] First row:")
            print(f"  {all_values[0][:5]}...")  # First 5 columns

        if len(all_values) > 1:
            print(f"\n[DATA] Last row:")
            last_row = all_values[-1]
            print(f"  Row length: {len(last_row)}")
            print(f"  First 5 values: {last_row[:5]}")

    except gspread.WorksheetNotFound:
        print(f"\n[ERROR] Sheet '{find_sheet_name}' not found!")
        print(f"[INFO] Available sheets: {[ws.title for ws in spreadsheet.worksheets()]}")

    # Try to access input sheet
    try:
        input_ws = spreadsheet.worksheet(input_sheet_name)
        print(f"\n[OK] Found sheet: '{input_sheet_name}'")
        print(f"  Rows: {input_ws.row_count}")
        all_values = input_ws.get_all_values()
        print(f"  Data rows: {len(all_values)}")

        if len(all_values) > 0:
            print(f"\n[INPUT SHEET] Header:")
            print(f"  {all_values[0][:5]}...")

    except gspread.WorksheetNotFound:
        print(f"\n[ERROR] Sheet '{input_sheet_name}' not found!")
