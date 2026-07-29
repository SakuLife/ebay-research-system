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

# SerpApi（Google Shopping。直APIが全滅した時のフォールバック仕入先）
# ⚠️ 変数名は SERP_API_KEY で統一。SERPAPI_API_KEY と書くと読まれず無効になる
SERP_API_KEY=xxx

# Yahoo!ショッピング（任意。3つ目の仕入先）
YAHOO_APP_ID=xxx
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

## 同一商品判定と打率（2026-07-28 改修）

eBay最安値検索（`find_cheapest_active_listing`）の**打率＝同一商品の別出品を見つけられた割合**は、
`tests/bench_cheapest_hitrate.py` で実測する。母集団は `tests/fixtures/cheapest_bench_items.json` に
固定してあり、改修の前後で同じ対象を比較できる（実行のたびに対象が変わると、改修の効果と
母集団の運不運を切り分けられない）。

```bash
python tests/bench_cheapest_hitrate.py --build --size 30   # 母集団を作り直す
python tests/bench_cheapest_hitrate.py --run               # 打率を測る
```

### 判定ロジックの要点

| 要素 | 内容 |
|---|---|
| 類似度 | **文字bigramのDice係数**と内容語トークンDiceの大きい方（`title_similarity`）。旧`SequenceMatcher`は語順違い・出品者の前置きに弱く、**別世代品を正解より高く評価**していた（WH-1000XM5=70% > 正解のXM4=65%）＝取りこぼしの主因 |
| 型番一致 | 英数字混在4文字以上を型番とみなす（`_model_tokens`）。末尾2文字以内の差は同一扱い（`F-91W` と `F91W-1`）。一致すれば足切りを下げる |
| 検索クエリ | 現行クエリ・Gemini短縮クエリ・**型番クエリ**の三段。型番クエリは他の2つが0件でも候補を拾える |
| 所在地 | 日本所在地の候補が25件未満なら全所在地でも検索（従来は0件のときだけ広げており取りこぼしていた） |
| 付属品除外 | 説明書のみ・箱のみ・部品取り等を候補から外す（`is_accessory_listing`）。安価ゆえ最安側に並び、判定枠を食い潰す |
| **走査順** | **もっともらしさ上位30件に絞ってから安い順**。ここが要注意（下記） |

### ⚠️ 走査順を「安い順」だけにすると打率が落ちる

候補プールを広げる改修を入れたとき、走査順が単純な安い順のままだったため、
**打率が 63%→53% に低下した**。増えた候補のうち無関係な安物（互換アクセサリ・別バリアント）が
先頭に並び、Gemini画像比較の回数上限（15回）を食い潰して本命に到達できなくなったため。
「候補を増やす」改修は「絞り込みと走査順」とセットで入れないと逆効果になる。

### 実測値（30件固定母集団・Gemini ON）

| 版 | 打率 | 所要 |
|---|---|---|
| 改修前 | 19/30 = **63%** | 1,732秒 |
| 改修v1（プール拡大のみ・走査順は安い順） | 16/30 = **53%** | 7,978秒 |
| 改修v2（＋もっともらしさ上位に絞る） | 20/30 = **67%** | 798秒 |
| **改修v3（＋品番[56809]/#241の抽出・寸法トークン除外・枠20/40）** | **21/30 = 70%** | 977秒 |

打率 63%→**70%**、所要時間は 1,732秒→977秒（**約44%短縮**）。
「より安い出品を見つけた」割合も 7/30→**9/30** に増えた。

残る取りこぼし9件の内訳:
- **カスタム品・改造品 3件**（Moissanite装飾G-SHOCK、ケース改造品2件）… 同一商品が世界に1点しかなく**原理的に拾えない**
- **サードパーティ製の汎用アクセサリ 2件**（各社カメラ対応の互換レンズ）… タイトルが対応機種の羅列で商品固有の識別子がない
- **ノーブランド品 1件**（中華製包丁）… 識別子が鋼材名のみ
- **プラモデル 3件**（Tamiya CB750 / MS-06 Zaku / SEED Meteor Unit）… 品番は拾えるようになったが、
  出品数自体が少なく同一品が市場に無い可能性が高い

＝**残りの多くは「そもそも同一商品が出品されていない」ケース**であり、
ロジック改善で伸ばせる余地は限定的。これ以上狙うなら Gemini画像比較の回数上限を上げる
（＝APIコスト増）方向になる。

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
| 楽天 API | **要移行**（下記） | 旧エンドポイント廃止。`RAKUTEN_ACCESS_KEY` の取得で復活する |
| Amazon PA-API | **403（恒久）** | `Your account does not currently meet the eligibility requirements` ＝アソシエイトの売上実績条件を満たすまで使用不可 |
| Yahoo!ショッピング | 未設定 | `YAHOO_APP_ID` を入れれば3つ目の仕入先として使える |
| SerpApi (Google Shopping) | 稼働（フォールバック） | 直APIが全滅した時のみ使う。**有料・無料枠100回/月** |

### ⚠️ 楽天APIの503は「メンテ待ち」ではない（2026-07-29 判明）

旧エンドポイントは **`503 service_unavailable / "under maintenance"`** を返すが、
これは一時的な障害ではなく**2026年のインフラ刷新で旧基盤が廃止された結果**。
待っても永久に復旧しない。移行しないと直らない。

| | 旧（廃止） | 新 |
|---|---|---|
| エンドポイント | `app.rakuten.co.jp/services/api/IchibaItem/Search/20170706` | `openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260701` |
| パス | `/services/api/` | **`/ichibams/api/`**（ドメインだけでなくパスも変わる） |
| 認証 | `applicationId` のみ | `applicationId` ＋ **`accessKey`** |

**切り分け方**（アプリID起因か基盤廃止かの判定）:
- 旧エンドポイントに**無効な**applicationIdを投げると `400 wrong_parameter`、
  有効なIDだと `503 under maintenance` → **IDは生きていて基盤側が死んでいる**証拠
- 新エンドポイントに applicationId だけ投げると
  `400 accessKey must be present` → **エンドポイントは生きており、足りないのは accessKey だけ**

⚠️ 全API（Ichiba/Books/Genre/Product）が一律で「メンテナンス中」と返すため、
**一時障害に見えて何日も待ってしまう**。この文言を見たら移行を疑うこと。

**アプリの新規作成が必要**（旧プラットフォームのアプリは新管理画面に出てこない）。
作成フォームの `Allowed websites` は「ここに登録したサイトからのリクエストのみ許可」という
新しい制限。⚠️ **フォームに薄字で出ている例（`rakuten.co.jp` / `webservice.rakuten.co.jp` /
`*.rakuten.com`）をそのまま入れてはいけない**。楽天所有ドメインを名乗ると
`403 Access from this IP address is not allowed` でIP許可リストに弾かれる。

**Referer は必須**（2026-07-29 実測で確定）。`RAKUTEN_REFERER` に
**Allowed websites に登録したドメイン**を設定しないと `403 REQUEST_CONTEXT_BODY_HTTP_REFERRER_MISSING` になる。

⚠️ **測定の落とし穴**: 未登録のアプリID／ダミーのaccessKeyで試すと、Refererを送らなくても
「Invalid Access Key」まで到達するため**「Refererは不要」と誤解する**。
正規のキーで叩いて初めてRefererが要求される。**検査は accessKey より前段にある。**
＝ 本物のキーを手にするまで、この仕様は正しく測れない（社訓「メタデータは嘘をつく」の実例）。

| 送る Referer | 登録済みキーでの結果 |
|---|---|
| ヘッダー無し | **403 HTTP_REFERRER_MISSING** |
| `https://github.com/`（Allowed websitesに登録済み） | **200 OK** |
| `webservice.rakuten.co.jp` | 403 IP許可リスト違反 |
| `localhost` | 503 Authentication service error |

**レスポンス構造は旧仕様のまま**（`Items[].Item` の入れ子）。フィールド名も
`itemName` / `itemPrice` / `itemUrl` / `shopName` / `availability` / `postageFlag` /
`mediumImageUrls` が従来どおり使える（2026-07-29 実データで確認）。
`affiliateId` も有効で、`itemUrl` がアフィリエイトリンク（`hb.afl.rakuten.co.jp`）になる。

対応: `RAKUTEN_ACCESS_KEY` を楽天ウェブサービスのアプリ管理画面から取得して `.env` に設定する。
未設定の間は `RakutenClient.is_enabled` が False になり、警告を出して自動的にSerpApiへ回る。
**新版レスポンスの形状（`Items[].Item` の入れ子が残っているか）は accessKey 取得後に実データで要確認**
（`_unwrap()` で両形式に対応済みだが未検証）。

### 有料APIを叩く前の足切り（コスト最適化・2026-07-29）

**`max_affordable_source_jpy()`（`profit.py`）で「仕入値の上限」を先に出し、
予算が残らない商品は仕入先検索そのものを行わない。**
販売価格からeBay手数料と国際送料を引いた時点で予算がゼロなら、
どんなに安い仕入先を見つけても採用されないため、探索は全額が無駄になる。

⚠️ **この足切りの式は `calculate_profit()` と同じ係数を使うこと**（同じ式を逆に解いたもの）。
独自の概算式（手取り率75%等）で判断したところ、**国際送料を無視したため利益率38%と誤判定**し、
実際は¥288しか出ない商品でSerpApiを省いて、より安い店（利益¥1,169）を取り逃した。
概算式を別に持つと、片方だけ古くなって判断が狂う。

適用箇所:
- `github_actions_runner.py` … 予算ゼロなら仕入先検索をスキップ
- `auto_research_runner.py` … 予算ゼロ（最低利益額込み）なら Web(EN)/Lens を丸ごとスキップ。
  スキップ件数は最後のサマリに `仕入予算不足で検索せず` として出る

### SerpApiを呼ぶ判断（`SOURCING_SERPAPI_MODE`）

| 値 | 挙動 | 用途 |
|---|---|---|
| `smart`（既定） | 直APIの仕入値が上限の50%以下＝利益に余裕がある商品だけ省く | 判断を変えずに節約 |
| `always` | 毎回呼ぶ | 最安値の精度を最優先 |
| `fallback` | 直APIが0件のときだけ | 消費最小。楽天より安い店を取り逃す |

消費量は実行の最後に `SerpApi消費: Nクレジット（約X円）` として必ず出力する
（`sourcing.credit_counter`）。見えないコストは削れない。

### 仕入先の探索方針（2026-07-29 更新）

**SerpApi(Google Shopping)は既定で毎回呼ぶ**（`SOURCING_SERPAPI_MODE=always`）。

⚠️ 当初「直APIが0件のときだけ呼ぶ」フォールバック方式にしていたが、これは要件違反だった。
楽天に在庫があった時点で探索を打ち切るため、**楽天より安い他店を取り逃す**。
実測例: グローバルミルボン → 楽天¥9,180 だが Amazon公式¥4,866 が存在。
フォールバック方式だと¥9,180を採用し、利益を大幅に過小評価する。
客の要件は「多数ある国内販売サイトから最安値を見つける」なので、全経路の和から最安を採る。
クレジットを絞りたい場合のみ `SOURCING_SERPAPI_MODE=fallback` で旧挙動に戻せる。

**中古除外**: Google Shoppingはメルカリ等のC2C中古が最安に並ぶので、
楽天と同じ `_is_used_item()` で除外してから採用する。

**海外ショップ除外** (`_is_domestic_source`): `gl=jp` で検索しても海外通販が混ざる。
実際に `vvs-automatismes.fr`（仏）や `osbrankoradicevicobilic.edu.rs`（セルビア）が
「国内最安」として採用されかけた。客は AliExpress・SHEIN等の中国系も明確に除外指定している。
判定は「日本語を含む／既知の国内ショップ名／.jp」なら国内、
それ以外で**生ドメイン表記（空白なし・ドットあり）なら海外**とみなして弾く。
＝ 国内店は店名が日本語や既知ブランドで返り、海外の雑多なサイトは生ドメインで返る差を使う。

**2026-07-28 の実走結果（行668）**: eBay $400.99 → 最安$135.99に差替（類似度89%・Gemini画像MATCH）、
国内最安 楽天市場 ¥10,091 → 検索ベースシート計算で **還付抜き利益 ¥6,373（29.0%）/ 還付あり ¥7,107（32.0%）**、
配送 CPaSS_Economy。1行のみ書き込み・隣接行は無傷。

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
