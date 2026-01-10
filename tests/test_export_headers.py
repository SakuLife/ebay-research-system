"""Export current spreadsheet headers as mapping."""

import os
from dotenv import load_dotenv
import gspread
from google.oauth2.service_account import Credentials


def test_export_headers():
    """Export headers from spreadsheet."""
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

    # Get 入力シート headers
    input_sheet = spreadsheet.worksheet("入力シート")
    all_values = input_sheet.get_all_values()

    if len(all_values) > 0:
        headers = all_values[0]

        print("\n" + "="*60)
        print("入力シート HEADERS")
        print("="*60)
        print(f"\nTotal columns: {len(headers)}\n")

        # Output for mapping file
        mapping_content = "# スプレッドシート「入力シート」のカラムマッピング\n"
        mapping_content += f"# 最終更新: 2025-12-27\n"
        mapping_content += f"# 総カラム数: {len(headers)}\n\n"

        for idx, header in enumerate(headers, 1):
            col_letter = chr(64 + idx) if idx <= 26 else f"A{chr(64 + idx - 26)}"
            print(f"{idx:2d}. [{col_letter}] {header}")
            mapping_content += f"{idx:2d}. [{col_letter:3s}] {header}\n"

        # Save to file
        output_file = "docs/スプレッドシート_カラムマッピング.txt"
        os.makedirs("docs", exist_ok=True)

        with open(output_file, "w", encoding="utf-8") as f:
            f.write(mapping_content)

        print(f"\n[SAVED] {output_file}")

        # Also create Python mapping
        python_mapping = "# Column mapping for 入力シート\n"
        python_mapping += "# Auto-generated from spreadsheet\n\n"
        python_mapping += "INPUT_SHEET_COLUMNS = [\n"
        for header in headers:
            python_mapping += f'    "{header}",\n'
        python_mapping += "]\n\n"
        python_mapping += "# Column indices (0-based)\n"
        python_mapping += "COL_INDEX = {\n"
        for idx, header in enumerate(headers):
            safe_key = header.replace("（", "_").replace("）", "").replace(" ", "_").replace("/", "_")
            python_mapping += f'    "{safe_key}": {idx},\n'
        python_mapping += "}\n"

        py_file = "src/spreadsheet_mapping.py"
        with open(py_file, "w", encoding="utf-8") as f:
            f.write(python_mapping)

        print(f"[SAVED] {py_file}")
