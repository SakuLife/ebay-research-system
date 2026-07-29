"""GitHub Actions runner for eBay research pipeline."""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from .ebay_client import EbayClient
from .sourcing import SourcingClient
from .profit import calculate_profit
from .sheets_client import GoogleSheetsClient, extend_table_formatting
from .spreadsheet_mapping import INPUT_SHEET_COLUMNS
from .search_base_client import SearchBaseClient


def get_last_row(sheet_client) -> int:
    """Get the last row number with data in the spreadsheet."""
    worksheet = sheet_client.spreadsheet.worksheet("入力シート")
    # Get all values in column A (date column)
    col_a_values = worksheet.col_values(1)
    # Return the last non-empty row number (1-indexed)
    return len(col_a_values)


def update_status(sheet_client, row_number: int, status: str, log: str = ""):
    """Update status column only.

    列は入力シートのヘッダーに合わせる（W=ステータス, Y=メモ）。
    2026-07-28まで V(利益率%（還付あり）)・X(出品フラグ) に書いており、
    途中でエラー終了すると利益率欄と出品フラグを汚染していた。
    """
    worksheet = sheet_client.spreadsheet.worksheet("入力シート")

    # Update W column (ステータス) - column 23
    status_cell = f"W{row_number}"
    worksheet.update(range_name=status_cell, values=[[status]])

    # Update Y column (メモ) if provided - column 25
    if log:
        memo_cell = f"Y{row_number}"
        current_memo = worksheet.acell(memo_cell).value or ""
        new_memo = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {log}"
        if current_memo:
            new_memo = current_memo + "\n" + new_memo
        worksheet.update(range_name=memo_cell, values=[[new_memo]])

    print(f"  [STATUS] Row {row_number}: {status}")


def ensure_row_exists(sheet_client, row_number: int) -> None:
    """書き込み先の行がシートの行数を超えていたら行を追加する.

    スプレッドシートには実グリッドの行数上限があり、それを超える行へ書くと
    「exceeds grid limits」で失敗する。ステータス更新の時点で例外になるため、
    結果が1件も書かれないまま静かに終わる（2026-07-29に実際に踏んだ）。

    ⚠️ 行を足しただけでは縞模様や条件付き書式が付かず、見た目が崩れる。
    必ず extend_table_formatting() で書式範囲も広げること。
    """
    worksheet = sheet_client.spreadsheet.worksheet("入力シート")
    if row_number > worksheet.row_count:
        # 余裕を持たせて追加し、連続実行のたびに拡張しなくて済むようにする
        before = worksheet.row_count
        need = row_number - before + 50
        worksheet.add_rows(need)
        print(f"  [INFO] シートの行数が不足していたため {need} 行追加しました "
              f"（{before} → {before + need}）")
        try:
            extend_table_formatting(sheet_client)
        except Exception as e:
            # 書式拡張に失敗しても書き込み自体は続行する（見た目の問題に留めるため）
            print(f"  [WARN] 表の書式拡張に失敗しました（見た目のみの影響）: {e}")


def is_row_occupied(sheet_client, row_number: int) -> bool:
    """指定行に既存データがあるか判定する（処理開始前に呼ぶこと）.

    処理中にステータス列を書いた後で判定すると自分が書いた値を
    「既存データ」と誤検知するため、判定は必ずupdate_statusより前に行う。
    """
    worksheet = sheet_client.spreadsheet.worksheet("入力シート")
    try:
        existing_data = worksheet.row_values(row_number)
        return bool(existing_data) and any(cell.strip() for cell in existing_data)
    except Exception as e:
        # 行がまだ存在しない場合など。空とみなして進める
        print(f"  [INFO] Row {row_number} occupancy check skipped: {e}")
        return False


def write_to_spreadsheet(sheet_client, row_number: int, data: dict, was_occupied: bool = False):
    """Write research results to spreadsheet."""
    worksheet = sheet_client.spreadsheet.worksheet("入力シート")

    if was_occupied:
        print(f"  [WARNING] Row {row_number} already contains data!")
        print(f"  [WARNING] Existing data will be overwritten.")

    # Prepare row data (A〜Y列：25列固定)
    row_data = [""] * 25

    # Map data to columns
    row_data[0] = datetime.now().strftime("%Y-%m-%d")  # A: 日付
    row_data[1] = data.get("keyword", "")  # B: キーワード
    row_data[2] = data.get("category_name", "")  # C: カテゴリ
    # D: カテゴリ番号（そのまま書き込み）
    cat_id = data.get("category_id", "")
    row_data[3] = str(cat_id) if cat_id else ""
    row_data[4] = data.get("condition", "")  # E: 新品中古

    # ソーシング結果（国内最安①②③: F-N列）
    sourcing_results = data.get("sourcing_results", [])
    for idx, result in enumerate(sourcing_results[:3]):
        name_col = 5 + (idx * 3)   # F=5, I=8, L=11
        url_col = 6 + (idx * 3)    # G=6, J=9, M=12
        price_col = 7 + (idx * 3)  # H=7, K=10, N=13
        row_data[name_col] = result.get("name", "")[:50] if result.get("name") else ""
        row_data[url_col] = result.get("url", "")
        row_data[price_col] = str(result.get("price", "")) if result.get("price") else ""

    # eBay情報 (O-R列)
    row_data[14] = data.get("ebay_url", "")  # O: eBayリンク
    row_data[15] = str(data.get("sold_count", ""))  # P: 販売数
    row_data[16] = str(data.get("ebay_price", ""))  # Q: 販売価格（米ドル）
    row_data[17] = str(data.get("ebay_shipping", ""))  # R: 販売送料（米ドル）

    # 利益計算結果 (S-V列)
    row_data[18] = str(data.get("profit_no_rebate", ""))  # S: 還付抜き利益額（円）
    row_data[19] = str(data.get("profit_margin_no_rebate", ""))  # T: 利益率%（還付抜き）
    row_data[20] = str(data.get("profit_with_rebate", ""))  # U: 還付あり利益額（円）
    row_data[21] = str(data.get("profit_margin_with_rebate", ""))  # V: 利益率%（還付あり）

    # ステータスとメモ (W, X, Y列)
    if data.get("error"):
        row_data[22] = "エラー"  # W: ステータス
        row_data[24] = f"ERROR: {data.get('error')}"  # Y: メモ
    else:
        row_data[22] = "要確認"  # W: ステータス
        memo = f"自動処理 {datetime.now().strftime('%H:%M:%S')}"
        if data.get("note"):
            # 最安値検索での価格差し替え等の補足情報
            memo += f" | {data['note']}"
        row_data[24] = memo  # Y: メモ
    # X: 出品フラグは空（ユーザーが手動で入力）

    # Write to specific row (A〜Y列：25列のみ。はみ出し防止)
    cell_range = f"A{row_number}:Y{row_number}"
    worksheet.update(range_name=cell_range, values=[row_data[:25]])

    print(f"  [WRITE] Written to row {row_number}")

    return row_data


def main():
    parser = argparse.ArgumentParser(description="eBay Research Pipeline")
    parser.add_argument("--ebay-url", required=True, help="eBay item URL")
    parser.add_argument("--row", type=int, required=True, help="Spreadsheet row number")
    args = parser.parse_args()

    print(f"="*60)
    print(f"eBay RESEARCH PIPELINE")
    print(f"="*60)
    print(f"eBay URL: {args.ebay_url}")
    print(f"Row: {args.row}")
    print(f"="*60)

    # Load environment (in GitHub Actions, env vars are already set)
    load_dotenv()

    # Initialize clients
    ebay_client = EbayClient()
    sourcing_client = SourcingClient()

    # For Google Sheets, need to handle both file path and JSON content
    service_account_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if service_account_json:
        # Check if it's a file path or JSON content
        if service_account_json.strip().startswith("{"):
            # It's JSON content (GitHub Actions)
            import tempfile
            temp_dir = tempfile.gettempdir()
            temp_sa_file = Path(temp_dir) / "service_account.json"
            temp_sa_file.write_text(service_account_json)
            service_account_path = str(temp_sa_file)
        else:
            # It's a file path (Local development)
            service_account_path = service_account_json
    else:
        service_account_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON_PATH",
                                        "ebaysystem-837d6cedefa5.json")

    sheets_client = GoogleSheetsClient(
        service_account_file=service_account_path,
        spreadsheet_url=os.getenv("SHEETS_SPREADSHEET_ID")
    )

    # 検索ベースシートクライアント初期化
    search_base_client = SearchBaseClient(sheets_client)

    # Check last row
    last_row = get_last_row(sheets_client)
    print(f"Current last row: {last_row}")
    print(f"Target row: {args.row}")
    if args.row <= last_row and last_row > 1:
        print(f"[WARNING] Row {args.row} may already contain data (last row is {last_row})")
        print(f"[INFO] Consider using row {last_row + 1} for new data")

    # 書き込み先の行を確保してから、既存データの有無を判定する
    # （判定は自分がステータスを書き込む前に行うこと）
    ensure_row_exists(sheets_client, args.row)
    row_was_occupied = is_row_occupied(sheets_client, args.row)

    result_data = {
        "ebay_url": args.ebay_url,
        "sourcing_results": [],
        "error": None
    }

    try:
        # Update status to "処理中..." at the start
        update_status(sheets_client, args.row, "処理中...", "GitHub Actions started")
        # Step 1: Get eBay item info
        print(f"\n[1/5] Fetching eBay item info...")

        is_mock_data = False
        try:
            ebay_item = ebay_client.get_item_by_url(args.ebay_url)

            if not ebay_item:
                raise Exception(f"Could not fetch eBay item from URL")

            # Extract item details
            ebay_title = ebay_item.get("title", "Unknown")

            # Get price
            price_obj = ebay_item.get("price", {})
            if isinstance(price_obj, dict):
                price_value = price_obj.get("value", "0")
            else:
                price_value = "0"
            ebay_price = float(price_value)

            # Get shipping cost
            shipping_options = ebay_item.get("shippingOptions", [])
            ebay_shipping = 0.0
            if shipping_options:
                shipping_cost = shipping_options[0].get("shippingCost", {})
                if isinstance(shipping_cost, dict):
                    ebay_shipping = float(shipping_cost.get("value", 0))

            # Get category information
            category_name = ebay_item.get("categoryPath", "")
            category_id = ebay_item.get("categoryId", "")
            # If categoryPath doesn't exist, try categories array
            if not category_name:
                categories = ebay_item.get("categories", [])
                if categories:
                    category_name = categories[0].get("categoryName", "")
                    if not category_id:
                        category_id = categories[0].get("categoryId", "")

            # Get weight information
            weight_kg = None
            product = ebay_item.get("product", {})
            if product:
                # Check for weight in different formats
                weight_info = product.get("weight", {})
                if weight_info:
                    weight_value = weight_info.get("value")
                    weight_unit = weight_info.get("unit", "").lower()

                    if weight_value:
                        # Convert to kg
                        if weight_unit in ["kg", "kilogram", "kilograms"]:
                            weight_kg = float(weight_value)
                        elif weight_unit in ["g", "gram", "grams"]:
                            weight_kg = float(weight_value) / 1000
                        elif weight_unit in ["lb", "lbs", "pound", "pounds"]:
                            weight_kg = float(weight_value) * 0.453592
                        elif weight_unit in ["oz", "ounce", "ounces"]:
                            weight_kg = float(weight_value) * 0.0283495

            # 最安値検索（Step 1.5）用の情報を抽出
            ebay_image_url = ebay_item.get("image", {}).get("imageUrl", "")
            ebay_condition = ebay_item.get("condition", "")
            ebay_legacy_id = str(ebay_item.get("legacyItemId", "") or "")

            print(f"  Title: {ebay_title}")
            print(f"  Price: ${ebay_price}")
            print(f"  Shipping: ${ebay_shipping}")
            if category_name:
                print(f"  Category: {category_name} (ID: {category_id})")
            if weight_kg:
                print(f"  Weight: {weight_kg:.3f} kg")
            else:
                print(f"  Weight: Not available (will use default)")

        except Exception as e:
            # eBay API失敗時はUSE_MOCKSをチェック
            use_mocks_value = os.getenv("USE_MOCKS", "0")
            use_mocks = use_mocks_value in ["1", "2", "true", "True"]
            if use_mocks:
                print(f"  [WARN] eBay API failed: {e}")
                print(f"  [INFO] USE_MOCKS={use_mocks_value}: Using mock data")
                ebay_title = "Nintendo Switch"
                ebay_price = 299.99
                ebay_shipping = 15.00
                category_name = "Video Games > Consoles"
                category_id = "139971"
                weight_kg = 0.4  # Nintendo Switchの実重量約400g
                ebay_image_url = ""
                ebay_condition = "New"
                ebay_legacy_id = ""
                is_mock_data = True  # モック時はStep 1.5（最安値検索）をスキップ
                print(f"  Title: {ebay_title}")
                print(f"  Price: ${ebay_price}")
                print(f"  Shipping: ${ebay_shipping}")
                print(f"  Category: {category_name} (ID: {category_id})")
                print(f"  Weight: {weight_kg:.3f} kg (mock)")
            else:
                error_msg = f"eBay API error: {e}"
                print(f"  [ERROR] {error_msg}")
                update_status(sheets_client, args.row, "エラー", error_msg)
                return

        # Step 1.5: 同一商品の最安アクティブリスティングを検索
        # 貼られたURLの価格をそのまま使わず、同一商品でより安い出品が
        # あれば eBay側の販売価格・送料・URL を最安出品に差し替える
        # （Auto Research側と同じ find_cheapest_active_listing を流用）
        ebay_url = args.ebay_url

        # Geminiクライアントは Step 1.5（画像比較）と Step 2（翻訳）で共用
        from .gemini_client import GeminiClient
        gemini_client = GeminiClient()
        if not gemini_client.is_enabled:
            # キー未設定時はNone扱い（Step1.5はタイトル類似度のみ、Step2は英語のまま）
            gemini_client = None

        if not is_mock_data:
            print(f"\n[1.5/5] eBay最安アクティブリスティングを検索中...")
            try:
                cheapest = ebay_client.find_cheapest_active_listing(
                    ebay_title=ebay_title,
                    sold_price_usd=ebay_price,
                    market="US",  # 手動パイプラインはEBAY_USで商品取得しているため
                    item_location="japan",
                    condition=ebay_condition,
                    gemini_client=gemini_client,
                    ebay_image_url=ebay_image_url,
                    exclude_item_id=ebay_legacy_id,
                )

                original_total = ebay_price + ebay_shipping
                if cheapest and cheapest["total_price_usd"] < original_total - 0.01:
                    print(f"  [最安値検索] より安い同一商品の出品を発見!")
                    print(f"    旧: ${ebay_price:.2f} + 送料${ebay_shipping:.2f}"
                          f" → 新: ${cheapest['price']:.2f} + 送料${cheapest['shipping']:.2f}"
                          f" (類似度{cheapest['similarity']:.0%})")
                    result_data["note"] = (
                        f"最安値検索: ${ebay_price:.2f}→${cheapest['price']:.2f}に差替"
                        f"(類似度{cheapest['similarity']:.0%})"
                    )
                    ebay_price = cheapest["price"]
                    ebay_shipping = cheapest["shipping"]
                    ebay_url = cheapest["url"]
                    result_data["ebay_url"] = ebay_url
                elif cheapest:
                    print(f"  [最安値検索] 貼り付けURLが最安"
                          f"（候補の最安総額: ${cheapest['total_price_usd']:.2f}）")
                else:
                    print(f"  [最安値検索] 同一商品の他出品なし → 貼り付けURLの価格を使用")
            except Exception as e:
                print(f"  [WARN] 最安値検索失敗: {e} → 貼り付けURLの価格を使用")

        # Step 2: Generate search query (translate to Japanese)
        # 英語タイトルのまま楽天/Amazonを叩くと日本語の商品名に当たらず0件になるため、
        # Geminiで日本語キーワードに変換してから国内検索に渡す（2026-07-28）
        print(f"\n[2/5] Generating search query...")
        search_query = ebay_title
        if gemini_client:
            japanese_query = gemini_client.translate_product_name(ebay_title)
            if japanese_query:
                search_query = japanese_query
                print(f"  [Gemini] 翻訳: {ebay_title[:50]} → {search_query}")
            else:
                print(f"  [WARN] 翻訳失敗 → 英語タイトルで検索")
        else:
            print(f"  [WARN] Gemini無効 → 英語タイトルで検索（国内0件になりやすい）")
        print(f"  Query: {search_query}")

        result_data["keyword"] = ebay_title
        result_data["search_query"] = search_query
        result_data["ebay_price"] = ebay_price
        result_data["ebay_shipping"] = ebay_shipping
        result_data["category_name"] = category_name
        result_data["category_id"] = category_id

        # Step 3: Search domestic sources (multiple offers)
        print(f"\n[3/5] Searching domestic sources...")
        from .models import ListingCandidate
        listing = ListingCandidate(
            candidate_id=f"ROW-{args.row}",
            search_query=search_query,
            ebay_item_url=ebay_url,  # 最安値検索で差し替え済みの場合あり
            ebay_price=ebay_price,
            ebay_shipping=ebay_shipping,
            sold_signal=0
        )

        # 有料APIを叩く前の足切り。
        # 販売価格に対して手数料と国際送料を引いた時点で仕入予算が残らない商品は、
        # どんなに安い仕入先を見つけても赤字なので、探すこと自体が無駄になる。
        # （実測では6件中4件が赤字〜利益わずかで、そこにクレジットを払っていた）
        from .config_loader import load_all_configs as _load_cfg
        from .profit import max_affordable_source_jpy
        _fee_rules = _load_cfg().fee_rules
        budget_jpy = max_affordable_source_jpy(
            ebay_price=ebay_price, ebay_shipping=ebay_shipping, fee_rules=_fee_rules
        )
        print(f"  [INFO] 仕入予算の上限: JPY {budget_jpy:,.0f}"
              f"（販売${ebay_price:.2f}＋送料${ebay_shipping:.2f}から手数料・国際送料を差引）")
        if budget_jpy <= 0:
            print(f"  [SKIP] 販売価格が低すぎて仕入予算が残りません → 仕入先検索をスキップ（0クレジット）")
            result_data["error"] = "販売価格が低く利益が出ない（仕入先検索せず）"
            offers = []
        else:
            # Get multiple offers (up to 3)
            offers = sourcing_client.search_multiple_offers(listing, max_results=3)

        if offers:
            print(f"  Found {len(offers)} offers:")
            for idx, offer in enumerate(offers, 1):
                print(f"  #{idx}: {offer.source_site} - JPY {offer.source_price_jpy:,.0f}")
                result_data["sourcing_results"].append({
                    # write_to_spreadsheet は "name" を F/I/L列（商品名）に書く。
                    # "site" だけ入れていたため商品名列が常に空だった（2026-07-28修正）
                    "name": offer.source_site,
                    "site": offer.source_site,
                    "url": offer.source_url,
                    "price": offer.source_price_jpy,
                    "shipping": offer.source_shipping_jpy
                })

            # Step 4: Calculate profit (using the cheapest offer)
            print(f"\n[4/5] Calculating profit (Python)...")
            from .config_loader import load_all_configs
            configs = load_all_configs()

            # Use the first offer (cheapest) for profit calculation
            cheapest_offer = offers[0]

            profit = calculate_profit(
                ebay_price=ebay_price,
                ebay_shipping=ebay_shipping,
                source_price_jpy=cheapest_offer.source_price_jpy,
                fee_rules=configs.fee_rules
            )

            print(f"  Profit: JPY {profit.profit_jpy_no_rebate:,.0f}")
            print(f"  Margin: {profit.profit_margin_no_rebate * 100:.1f}%")
            print(f"  Profitable: {profit.is_profitable}")

            # Step 4.5: 検索ベースシートで利益計算
            print(f"\n[4.5/5] 検索ベースシートで利益計算...")

            # 重量をグラムに変換（検索ベースシートはグラム単位）
            weight_g = int(weight_kg * 1000) if weight_kg else None

            # 検索ベースシートに入力データを書き込む（書式保持）
            search_base_success = search_base_client.write_input_data(
                source_price_jpy=cheapest_offer.source_price_jpy,
                ebay_price_usd=ebay_price,
                ebay_shipping_usd=ebay_shipping,
                ebay_url=ebay_url,  # 最安値検索で差し替え済みの場合あり
                weight_g=weight_g
            )

            if search_base_success:
                # 計算結果を読み取る
                calc_result = search_base_client.read_calculation_results(max_wait_seconds=5)

                if calc_result and calc_result["profit_no_rebate"] != 0:
                    # 検索ベースシートの計算結果を使用
                    print(f"  [INFO] 検索ベースシートの計算結果を使用")
                    result_data["profit_no_rebate"] = calc_result["profit_no_rebate"]
                    result_data["profit_margin_no_rebate"] = calc_result["margin_no_rebate"]
                    result_data["profit_with_rebate"] = calc_result["profit_with_rebate"]
                    result_data["profit_margin_with_rebate"] = calc_result["margin_with_rebate"]
                    result_data["carrier"] = calc_result["carrier"]
                    result_data["shipping_method"] = calc_result["shipping_method"]
                else:
                    # 読み取り失敗時はPython側の計算結果を使用
                    print(f"  [INFO] Python計算結果を使用")
                    result_data["profit_no_rebate"] = profit.profit_jpy_no_rebate
                    result_data["profit_margin_no_rebate"] = profit.profit_margin_no_rebate * 100
                    result_data["profit_with_rebate"] = profit.profit_jpy_with_rebate
                    result_data["profit_margin_with_rebate"] = profit.profit_margin_with_rebate * 100
            else:
                # 書き込み失敗時はPython側の計算結果を使用
                print(f"  [INFO] Python計算結果を使用")
                result_data["profit_no_rebate"] = profit.profit_jpy_no_rebate
                result_data["profit_margin_no_rebate"] = profit.profit_margin_no_rebate * 100
                result_data["profit_with_rebate"] = profit.profit_jpy_with_rebate
                result_data["profit_margin_with_rebate"] = profit.profit_margin_with_rebate * 100
        else:
            print(f"  No sourcing results found")
            result_data["error"] = "No sourcing results"

        # Step 5: Write to spreadsheet
        print(f"\n[5/5] 入力シートに書き込み...")
        write_to_spreadsheet(sheets_client, args.row, result_data, was_occupied=row_was_occupied)

        print(f"\n{'='*60}")
        print(f"COMPLETED SUCCESSFULLY")
        # 有料APIの消費量を毎回出す。見えないコストは削れないため
        from .sourcing import credit_counter
        print(f"  {credit_counter.report()}")
        print(f"{'='*60}")

        return 0

    except Exception as e:
        print(f"\n{'='*60}")
        print(f"ERROR: {str(e)}")
        print(f"{'='*60}")

        import traceback
        traceback.print_exc()

        # Write error to spreadsheet
        try:
            result_data["error"] = str(e)
            result_data["profit_no_rebate"] = 0
            result_data["profit_margin_no_rebate"] = 0
            write_to_spreadsheet(sheets_client, args.row, result_data, was_occupied=row_was_occupied)
        except Exception as write_error:
            print(f"Failed to write error to spreadsheet: {write_error}")
            # Try to at least update status
            try:
                update_status(sheets_client, args.row, "エラー", str(e))
            except:
                pass

        return 1


if __name__ == "__main__":
    sys.exit(main())
