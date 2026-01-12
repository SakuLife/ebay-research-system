"""Setup settings sheet with examples and nice UI.

Layout:
  A-C列: 基本設定・重量設定
  E-G列: キーワード設定（カテゴリ付き）
"""

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

# ============================================================
# LEFT SIDE: Settings (A-C columns)
# ============================================================
print("Setting up left side (Settings)...")

left_data = [
    # Row 1: Header
    ["設定項目", "値", "説明"],
    # Row 2: Empty
    ["", "", ""],
    # Row 3: Section header
    ["【基本設定】", "", ""],
    # Row 4-8: Basic settings
    ["検索市場", "UK", "eBay検索対象（UK/US/EU）"],
    ["検索期間", "90日", "販売実績期間"],
    ["最低価格($)", "100", "eBay検索の最低価格（ドル）"],
    ["最低利益額", "フィルターなし", "出力する最低利益"],
    ["取得件数/KW", "5", "キーワードあたりの取得件数(1-10)"],
    # Row 9: Empty
    ["", "", ""],
    # Row 10: Section header
    ["【重量設定】", "", ""],
    # Row 11-13: Weight settings (NOTE: read_settings reads B10-B12, shifted by 1)
    ["デフォルト重量", "自動推定", "カテゴリ別自動 or 固定値(g)"],
    ["梱包追加重量", "500", "梱包材の重量(g)"],
    ["サイズ倍率", "1.0", "大型商品は1.5など"],
]

# ============================================================
# RIGHT SIDE: Keywords (E-G columns)
# ============================================================
print("Setting up right side (Keywords)...")

right_header = [
    # Row 1: Header
    ["キーワード", "修飾語"],
    # Row 2: Empty
    ["", ""],
    # Row 3: Section header
    ["【キーワード一覧】", ""],
]

# Keyword examples
keyword_examples = [
    ["Yu-Gi-Oh", "Limited"],
    ["Gundam", "Vintage"],
    ["Pokemon", "Japanese"],
    ["Hello Kitty", "Rare"],
    ["Shiseido", ""],
    ["Japanese knife", ""],
    ["Senka", "perfect whip"],
]

# Write left side (A1:C11)
worksheet.update(range_name="A1", values=left_data)

# Write right side (E1:G3 headers, E4:G10 keywords)
worksheet.update(range_name="E1", values=right_header)
worksheet.update(range_name="E4", values=keyword_examples)

# ============================================================
# Formatting
# ============================================================
print("Applying formatting...")

# Left header (A1:C1) - Blue
worksheet.format("A1:C1", {
    "backgroundColor": {"red": 0.2, "green": 0.4, "blue": 0.8},
    "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
    "horizontalAlignment": "CENTER"
})

# Right header (E1:F1) - Green
worksheet.format("E1:F1", {
    "backgroundColor": {"red": 0.2, "green": 0.6, "blue": 0.3},
    "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
    "horizontalAlignment": "CENTER"
})

# Section headers - Gray
for row in ["A3:C3", "A10:C10", "E3:F3"]:
    worksheet.format(row, {
        "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9},
        "textFormat": {"bold": True},
    })

# Settings labels - Bold
worksheet.format("A4:A8", {"textFormat": {"bold": True}})
worksheet.format("A11:A13", {"textFormat": {"bold": True}})

# Set column widths
print("Setting column widths...")
try:
    worksheet.set_column_width('A', 150)
    worksheet.set_column_width('B', 120)
    worksheet.set_column_width('C', 200)
    worksheet.set_column_width('D', 30)  # Gap column
    worksheet.set_column_width('E', 150)
    worksheet.set_column_width('F', 120)
    worksheet.set_column_width('G', 120)
except AttributeError:
    print("  (Using alternative method for column widths)")

# ============================================================
# Data Validation (Dropdowns)
# ============================================================
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

        # Minimum price dropdown (B6) - NEW
        rule_min_price = DataValidationRule(
            BooleanCondition('ONE_OF_LIST', ['0', '30', '50', '100', '150', '200']),
            showCustomUi=True
        )
        set_data_validation_for_cell_range(worksheet, 'B6', rule_min_price)

        # Minimum profit dropdown (B7)
        rule_profit = DataValidationRule(
            BooleanCondition('ONE_OF_LIST', ['フィルターなし', '500円', '1000円', '2000円', '3000円', '5000円']),
            showCustomUi=True
        )
        set_data_validation_for_cell_range(worksheet, 'B7', rule_profit)

        # Items per keyword dropdown (B8)
        rule_items = DataValidationRule(
            BooleanCondition('ONE_OF_LIST', ['1', '2', '3', '5', '10']),
            showCustomUi=True
        )
        set_data_validation_for_cell_range(worksheet, 'B8', rule_items)

        # Default weight dropdown (B11)
        rule_weight = DataValidationRule(
            BooleanCondition('ONE_OF_LIST', ['自動推定', '500', '1000', '1500', '2000', '3000']),
            showCustomUi=True
        )
        set_data_validation_for_cell_range(worksheet, 'B11', rule_weight)

        # Packaging weight dropdown (B12)
        rule_packaging = DataValidationRule(
            BooleanCondition('ONE_OF_LIST', ['300', '500', '800', '1000', '1500', '2000']),
            showCustomUi=True
        )
        set_data_validation_for_cell_range(worksheet, 'B12', rule_packaging)

        # Size multiplier dropdown (B13)
        rule_size = DataValidationRule(
            BooleanCondition('ONE_OF_LIST', ['0.8', '1.0', '1.25', '1.5', '2.0']),
            showCustomUi=True
        )
        set_data_validation_for_cell_range(worksheet, 'B13', rule_size)

        print("  Dropdowns set successfully!")
    except Exception as e:
        print(f"  (Could not set data validation: {e})")
else:
    print("  (gspread-formatting not installed - skipping data validation)")

# Add borders
print("Adding borders...")
# Left side borders
worksheet.format("A1:C13", {
    "borders": {
        "top": {"style": "SOLID", "width": 1, "color": {"red": 0.8, "green": 0.8, "blue": 0.8}},
        "bottom": {"style": "SOLID", "width": 1, "color": {"red": 0.8, "green": 0.8, "blue": 0.8}},
        "left": {"style": "SOLID", "width": 1, "color": {"red": 0.8, "green": 0.8, "blue": 0.8}},
        "right": {"style": "SOLID", "width": 1, "color": {"red": 0.8, "green": 0.8, "blue": 0.8}}
    }
})
# Right side borders
worksheet.format("E1:F20", {
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

print("\n" + "=" * 50)
print("[SUCCESS] Settings sheet setup complete!")
print("=" * 50)
print("\nLayout:")
print("  A-C列: 基本設定・重量設定 (B4-B8, B11-B13)")
print("  E-F列: キーワード x 修飾語 (全組み合わせ生成)")
print("\nSettings (B4-B8):")
print("  - 検索市場: UK/US/EU")
print("  - 検索期間: 30日/60日/90日")
print("  - 最低価格($): 0/30/50/100/150/200")
print("  - 最低利益額: フィルターなし〜5000円")
print("  - 取得件数/KW: 1/2/3/5/10")
print("\nWeight settings (B11-B13):")
print("  - デフォルト重量: 自動推定 or 固定値")
print("  - 梱包追加重量: 300g〜2000g")
print("  - サイズ倍率: 0.8〜2.0")
print("\nKeywords (E-F列):")
print("  E列: メインキーワード (Pokemon, Gundam, etc.)")
print("  F列: 修飾語 (Japanese, Limited, Rare, etc.)")
print("  → 全組み合わせを自動生成")
