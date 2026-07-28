# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

eBay reseller research automation system that finds profitable products by sourcing from Japanese domestic suppliers (Rakuten/Amazon) and calculating profit margins for eBay sales. The system runs on GitHub Actions and integrates with Google Sheets for a spreadsheet-based workflow.

## Development Commands

### Environment Setup

```bash
# Create and activate virtual environment
python -m venv .venv
.venv\Scripts\activate  # Windows
source .venv/bin/activate  # Mac/Linux

# Install dependencies
pip install -r requirements.txt

# Create .env file from template
cp .env.example .env
# Edit .env with API credentials
```

### Running Tests

```bash
# Run all tests
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/test_profit.py -v

# Run tests with pattern matching
python -m pytest tests/ -k "rakuten" -v

# Run integration tests (requires API credentials)
python -m pytest tests/test_integration_rakuten.py -v
```

### Manual Pipeline Execution

```bash
# Local execution without GitHub Actions
python -m src.github_actions_runner \
  --ebay-url "https://www.ebay.com/itm/123456789" \
  --row 5

# Interactive local pipeline test (no GitHub Token required)
python tests/test_local_pipeline.py

# Read-only test: check search base sheet calculations
python tests/test_read_only_search_base.py
```

### Spreadsheet Operations

```bash
# Check spreadsheet structure
python tests/test_check_search_base_sheet.py

# Test writing to input sheet
python tests/test_write_input_sheet.py

# Export headers for mapping updates
python tests/test_export_headers.py
```

## System Architecture

### Dual-Sheet Calculation Flow

The system uses two Google Sheets that work together:

1. **入力シート (Input Sheet)**: Main data sheet with 34 columns
   - Row format defined in `src/spreadsheet_mapping.py`
   - Column B: eBay URL input
   - Columns O-T: Sourcing results (up to 3 sources)
   - Columns AB-AE: Profit calculations
   - Column AF: Status tracking

2. **検索ベース (Search Base Sheet)**: Calculation engine
   - Row 10: Input data (B10=source price, C10=sell price, D10=shipping, K9=URL)
   - Row 10 columns N-Q: Calculation results (carrier, method, profit, margin)
   - Row 13 columns P-Q: Alternative profit calculations (with rebate)
   - **CRITICAL**: E10 and J10 contain formulas - never overwrite
   - **CRITICAL**: Use `value_input_option='USER_ENTERED'` to preserve cell formatting

### Pipeline Execution Flow

```
User pastes eBay URL in 入力シート → GAS button click
  ↓
Google Apps Script triggers GitHub Actions (repository_dispatch)
  ↓
GitHub Actions Runner (src/github_actions_runner.py):
  [1/5] Fetch eBay item info (eBay Browse API)
  [1.5/5] Find cheapest active listing of the same product (2026-07-28追加)
          - Auto Research側の find_cheapest_active_listing() を流用
          - 同一商品でより安い出品（送料込み総額）が見つかれば
            eBay価格・送料・URLを最安出品に差し替えて以降の計算に使用
          - GEMINI_API_KEY があれば画像比較で同一商品判定、無ければタイトル類似度のみ
          - 差し替えた場合はメモ欄（Y列）に「最安値検索: $旧→$新に差替」と記録
  [2/5] Generate Japanese search query (Gemini translate_product_name・2026-07-28実装)
        - 英語タイトルのまま楽天/Amazonを叩くと日本語商品名に当たらず0件になるため
        - Gemini無効時は英語タイトルのまま（0件になりやすい旨を警告表示）
  [3/5] Search domestic sources (Rakuten + Amazon PA-API)
  [4/5] Calculate profit (Python fallback)
  [4.5/5] Write to 検索ベース sheet → Read calculation results
  [5/5] Write results to 入力シート
  ↓
GAS polls status every 5 seconds (max 3 minutes)
  ↓
Completion notification to user
```

### Key Components

**src/github_actions_runner.py**: Main pipeline orchestrator
- Entry point for GitHub Actions
- Handles status updates in column AF
- Falls back to Python calculations if sheet calculations fail
- ⚠️ この手動パイプライン用ワークフロー（research.yml / Pattern①）は 2026-01-11 に
  「Pattern②へ置き換え」で削除済み。現在は `python -m src.github_actions_runner` の
  ローカル実行のみ可能（GASボタン→repository_dispatch の経路は復活させないと動かない）

**src/search_base_client.py**: 検索ベース sheet interface
- `write_input_data()`: Writes to B10, C10, D10, F10, G-I10, K9 (preserving formats)
- `read_calculation_results()`: Reads N10:Q10 and P13:Q13
- Returns carrier, shipping method, profit amounts, and margins

**src/sourcing.py**: Domestic supplier search
- `SourcingClient`: Unified interface for Rakuten + Amazon
- `RakutenClient`: Rakuten Ichiba API (no request limits)
- `AmazonPaapiClient`: Amazon Product Advertising API (1 req/sec limit)
- Returns cheapest option: `min(offers, key=lambda o: o.source_price_jpy + o.source_shipping_jpy)`

**src/ebay_client.py**: eBay Browse API integration
- `get_item_by_url()`: Extracts item ID from URL (handles short URLs like ebay.us/m/xxx)
- `get_item_by_id()`: Fetches item details via Browse API
- Sandbox mode: `EBAY_USE_SANDBOX=true` in .env
- Production requires different credentials (no `-SBX-` in client ID)

**src/sheets_client.py**: Google Sheets API wrapper
- Uses service account authentication
- Handles both file path and JSON content for credentials (GitHub Actions compatibility)

**src/profit.py**: Profit calculation (Python fallback)
- Applies FX rate, eBay fees, shipping costs
- Returns `ProfitResult` with profit_jpy_no_rebate and profit_margin_no_rebate

**src/spreadsheet_mapping.py**: Column definitions
- `INPUT_SHEET_COLUMNS`: 34-column array matching 入力シート headers
- Maps Python data to exact spreadsheet column positions

## Important Patterns

### Writing to 検索ベース Sheet (Preserving Formats)

```python
# CORRECT: Preserves ¥ and $ formatting
worksheet.update(range_name="B10", values=[[5000]], value_input_option='USER_ENTERED')

# WRONG: Loses formatting
worksheet.update(range_name="B10", values=[["5000"]])
```

### Reading Calculation Results

```python
# Always check if result is valid
calc_result = search_base_client.read_calculation_results(max_wait_seconds=5)

if calc_result and calc_result["profit_no_rebate"] != 0:
    # Use spreadsheet calculations (more accurate)
    profit = calc_result["profit_no_rebate"]
else:
    # Fallback to Python calculations
    profit = python_calculated_profit
```

### Status Updates

入力シートの実ヘッダーは **W=ステータス / X=出品フラグ / Y=メモ**（A〜Yの25列）。
2026-07-28 まで `update_status()` は V（利益率%（還付あり））と X（出品フラグ）に書いており、
途中でエラー終了すると利益率欄と出品フラグを汚染していた（W/Yへ修正済み）。

```python
# Update status column (W) and memo column (Y)
update_status(sheets_client, row_number=5, status="処理中...", log="Started processing")
update_status(sheets_client, row_number=5, status="要確認", log="Completed successfully")
```

行の既存データ判定（`is_row_occupied`）は **`update_status` より前に呼ぶ**こと。
後で判定すると自分が書いたステータスを「既存データ」と誤検知し、毎回上書き警告が出る。

### eBay API Error Handling

```python
try:
    ebay_item = ebay_client.get_item_by_url(ebay_url)
except Exception as e:
    # Sandbox items may 404, use mock data for testing
    ebay_title = "Mock Product"
    ebay_price = 29.99
```

## Configuration Files

**config/fee_rules.yaml**: eBay fee calculations, FX rates, shipping costs
**config/categories.yaml**: eBay category mappings
**config/hotwords.yaml**: Keywords for search query optimization
**docs/mapping.txt**: Detailed 検索ベース sheet cell mapping (B10:M10 input, N10:Q13 output)

## Environment Variables

Required in `.env` for local development and GitHub Secrets for production:

```
# eBay API
EBAY_CLIENT_ID=xxx
EBAY_CLIENT_SECRET=xxx
EBAY_USE_SANDBOX=true  # false for production

# Rakuten API
RAKUTEN_APPLICATION_ID=xxx
RAKUTEN_AFFILIATE_ID=xxx  # Optional

# Amazon PA-API
AMAZON_ACCESS_KEY_ID=xxx
AMAZON_SECRET_ACCESS_KEY=xxx
AMAZON_PARTNER_TAG=xxx
AMAZON_MARKETPLACE=JP

# Google Sheets (file path locally, JSON content in GitHub Actions)
GOOGLE_SERVICE_ACCOUNT_JSON=path/to/service-account.json
SHEETS_SPREADSHEET_ID=https://docs.google.com/spreadsheets/d/xxx

# Gemini API（翻訳・画像比較で使用）
GEMINI_API_KEY=xxx
# バージョン直書きは提供終了で404になる（2.0-flash→2.5-flashと2度踏んだ）。-latestは現行世代に自動追従
GEMINI_MODEL=gemini-flash-latest
```

## Critical Implementation Details

### Do Not Overwrite Formula Cells

E10 and J10 in 検索ベース sheet contain formulas. The code explicitly skips these:

```python
# E10: 適用重量（g） - 数式なのでスキップ
# J10: 合計（g） - 数式なのでスキップ
```

### Handle Unicode Characters in Japanese Text

Windows console uses cp932 encoding. Avoid ¥ symbol in print statements:

```python
# WRONG: UnicodeEncodeError on Windows
print(f"Price: ¥{price}")

# CORRECT:
print(f"Price: JPY {price}")
```

### Spreadsheet Row Numbers

- Spreadsheet rows are 1-indexed (row 1 = header)
- Python processes data starting from row 2+
- `--row N` argument refers to actual spreadsheet row number

## Testing Strategy

**Unit tests**: `test_profit.py`, `test_models.py`, `test_validators.py`
**Integration tests**: `test_integration_rakuten.py` (requires API key)
**Local pipeline**: `test_local_pipeline.py` (interactive, no GitHub Token)
**Sheet operations**: `test_write_input_sheet.py`, `test_read_only_search_base.py`

## Known Limitations

- eBay production API requires separate credentials from Sandbox
- Short URLs (ebay.us/m/xxx) may hit redirect loops in Sandbox
- GitHub Actions has 10-minute timeout per job
- Google Sheets API has rate limits (100 requests per 100 seconds per user)

### 2026-07-28 実測で判明した稼働状況（本番APIで確認）

| 依存先 | 状態 | 備考 |
|---|---|---|
| GitHubリポジトリ | **アーカイブ済み＝読み取り専用** | push 403。GitHub Actions も動かないので**ローカル実行のみ** |
| eBay Browse API | 稼働 | 商品取得・最安値検索とも正常 |
| Gemini | 稼働 | ただし `.env` の `GEMINI_MODEL` が `gemini-2.5-flash` のままで404だった → `gemini-flash-latest` に修正 |
| Google Sheets | 稼働 | `.env` の service account パスが旧フォルダ名 `ebaySystem` を指していた → `0_ebaySystem` に修正 |
| 楽天 API | **503（メンテナンス中）** | 一時的。復旧すれば動く |
| Amazon PA-API | **403（恒久）** | `Your account does not currently meet the eligibility requirements` ＝アソシエイトの売上実績条件を満たすまで使用不可 |
| Yahoo!ショッピング | 未設定 | `YAHOO_APP_ID` を入れれば3つ目の仕入先として使える |

⚠️ **仕入先が楽天503・Amazon403・Yahoo未設定で全滅のため、現状はeBay側だけ取れて利益計算まで到達しない**
（`No sourcing results` でエラー行になる）。最安値検索そのものは正常動作している。

## Deployment

Code runs directly from GitHub repository - no build step required:

```bash
git add .
git commit -m "Update sourcing logic"
git push
# GitHub Actions automatically uses latest code
```

## ログ共有ルール

- ログは `logs/input.txt` に貼り付ける
- 「ここ見て」と言われたら内容を確認
- 確認後、ファイル名を `YYYY-MM-DD_HHMM.txt` 形式（JST）にリネーム
