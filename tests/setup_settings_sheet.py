"""Setup settings sheet with examples and nice UI."""

import os
from pathlib import Path
from dotenv import load_dotenv
import sys

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.sheets_client import GoogleSheetsClient

try:
    from gspread_formatting import DataValidationRule, BooleanCondition
    HAS_FORMATTING = True
except ImportError:
    HAS_FORMATTING = False

load_dotenv()

# Initialize sheets client
service_account_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
if service_account_json and service_account_json.strip().startswith("{"):
    import tempfile
    temp_dir = tempfile.gettempdir()
    temp_sa_file = Path(temp_dir) / "service_account.json"
    temp_sa_file.write_text(service_account_json)
    service_account_path = str(temp_sa_file)
else:
    service_account_path = service_account_json or "ebaysystem-837d6cedefa5.json"

sheets_client = GoogleSheetsClient(
    service_account_file=service_account_path,
    spreadsheet_url=os.getenv("SHEETS_SPREADSHEET_ID")
)

# Get or create settings sheet
try:
    worksheet = sheets_client.spreadsheet.worksheet("設定＆キーワード")
    print("Found existing '設定＆キーワード' sheet")
except:
    print("Sheet not found - please create '設定＆キーワード' sheet first")
    sys.exit(1)

# Clear existing content
worksheet.clear()

# Setup headers with nice formatting
print("Setting up headers...")
headers = [
    ["設定項目", "値", "説明"],
]

# Settings section
settings_data = [
    ["", "", ""],  # Empty row
    ["【基本設定】", "", ""],
    ["検索市場", "UK", "eBay検索対象市場（UK/US/EU）"],
    ["検索期間", "90日", "販売実績の検索期間（30日/60日/90日）"],
    ["最低利益額", "フィルターなし", "候補として抽出する最低利益額（フィルターなし=全件出力）"],
    ["", "", ""],  # Empty row
    ["【キーワード設定】", "", ""],
    ["キーワード", "修飾語", "A列+B列で組み合わせて検索"],
]

# Keyword examples (2-column format: Main keyword + Modifier)
keyword_examples = [
    ["Yu-Gi-Oh", "Limited"],
    ["Gundam", "Vintage"],
    ["Pokemon", "Japanese"],
    ["Hello Kitty", "Rare"],
    ["Shiseido", ""],
    ["Japanese knife", ""],
    ["Senka", "perfect whip"],
]

# Combine all data
all_data = headers + settings_data + keyword_examples

# Write all data at once
worksheet.update(range_name="A1", values=all_data)

# Format headers (Row 1)
print("Formatting headers...")
worksheet.format("A1:C1", {
    "backgroundColor": {"red": 0.2, "green": 0.4, "blue": 0.8},
    "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
    "horizontalAlignment": "CENTER"
})

# Format section headers (【基本設定】, 【キーワード設定】)
print("Formatting section headers...")
worksheet.format("A3:C3", {
    "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9},
    "textFormat": {"bold": True},
})
worksheet.format("A8:C8", {
    "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9},
    "textFormat": {"bold": True},
})

# Format settings labels
worksheet.format("A4:A6", {
    "textFormat": {"bold": True},
})
worksheet.format("A9", {
    "textFormat": {"bold": True},
})

# Set column widths
print("Setting column widths...")
try:
    worksheet.set_column_width('A', 200)
    worksheet.set_column_width('B', 150)
    worksheet.set_column_width('C', 300)
except AttributeError:
    # Older gspread version - use format instead
    print("  (Using alternative method for column widths)")
    pass

# Add data validation (dropdowns)
print("Adding data validation...")

if HAS_FORMATTING:
    try:
        from gspread_formatting import set_data_validation_for_cell_range

        # Market dropdown (B4)
        rule_market = DataValidationRule(
            BooleanCondition('ONE_OF_LIST', ['UK', 'US', 'EU']),
            showCustomUi=True
        )
        set_data_validation_for_cell_range(worksheet, 'B4', rule_market)

        # Period dropdown (B5)
        rule_period = DataValidationRule(
            BooleanCondition('ONE_OF_LIST', ['30日', '60日', '90日']),
            showCustomUi=True
        )
        set_data_validation_for_cell_range(worksheet, 'B5', rule_period)

        # Minimum profit dropdown (B6)
        rule_profit = DataValidationRule(
            BooleanCondition('ONE_OF_LIST', ['フィルターなし', '1円', '500円', '1000円', '2000円', '3000円', '5000円']),
            showCustomUi=True
        )
        set_data_validation_for_cell_range(worksheet, 'B6', rule_profit)
        print("  Dropdowns set successfully!")
    except Exception as e:
        print(f"  (Could not set data validation: {e})")
else:
    print("  (gspread-formatting not installed - skipping data validation)")

# Add borders
print("Adding borders...")
worksheet.format("A1:C16", {
    "borders": {
        "top": {"style": "SOLID", "width": 1, "color": {"red": 0.8, "green": 0.8, "blue": 0.8}},
        "bottom": {"style": "SOLID", "width": 1, "color": {"red": 0.8, "green": 0.8, "blue": 0.8}},
        "left": {"style": "SOLID", "width": 1, "color": {"red": 0.8, "green": 0.8, "blue": 0.8}},
        "right": {"style": "SOLID", "width": 1, "color": {"red": 0.8, "green": 0.8, "blue": 0.8}}
    }
})

# Freeze header row
print("Freezing header row...")
worksheet.freeze(rows=1)

print("\n[SUCCESS] Settings sheet setup complete!")
print("\nSetup details:")
print("- Headers formatted with blue background")
print("- Section headers with gray background")
if HAS_FORMATTING:
    print("- Dropdowns added for market, period, and profit margin")
print("- 7 example keywords added")
print("- Column widths optimized")
print("- Borders and freeze applied")
