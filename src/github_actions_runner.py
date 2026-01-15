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
from .sheets_client import GoogleSheetsClient
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
    """Update status column only."""
    worksheet = sheet_client.spreadsheet.worksheet("入力シート")

    # Update S column (status) - column 19
    status_cell = f"S{row_number}"
    worksheet.update(range_name=status_cell, values=[[status]])

    # Update T column (memo) if provided - column 20
    if log:
        memo_cell = f"T{row_number}"
        current_memo = worksheet.acell(memo_cell).value or ""
        new_memo = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {log}"
        if current_memo:
            new_memo = current_memo + "\n" + new_memo
        worksheet.update(range_name=memo_cell, values=[[new_memo]])

    print(f"  [STATUS] Row {row_number}: {status}")


def write_to_spreadsheet(sheet_client, row_number: int, data: dict):
    """Write research results to spreadsheet."""
    worksheet = sheet_client.spreadsheet.worksheet("入力シート")

    # Check if the specified row is empty
    try:
        existing_data = worksheet.row_values(row_number)
        if existing_data and any(cell.strip() for cell in existing_data):
            print(f"  [WARNING] Row {row_number} already contains data!")
            print(f"  [WARNING] Existing data will be overwritten.")
    except Exception:
        pass  # Row doesn't exist yet, which is fine

    # Prepare row data matching INPUT_SHEET_COLUMNS
    row_data = [""] * len(INPUT_SHEET_COLUMNS)

    # Map data to columns (新しい20列構成)
    row_data[0] = datetime.now().strftime("%Y-%m-%d")  # 日付
    row_data[1] = data.get("ebay_url", "")  # 起点商品リンク
    row_data[2] = data.get("keyword", "")  # キーワード
    row_data[3] = data.get("category_name", "")  # カテゴリ
    # カテゴリ番号（先頭ゼロを保持するため、'を付けてテキスト扱いに）
    cat_id = data.get("category_id", "")
    row_data[4] = f"'{cat_id}" if cat_id else ""  # カテゴリ番号

    # ソーシング結果（国内最安①②③）
    sourcing_results = data.get("sourcing_results", [])
    for idx, result in enumerate(sourcing_results[:3]):
        url_col = 5 + (idx * 2)  # 5, 7, 9
        price_col = 6 + (idx * 2)  # 6, 8, 10
        row_data[url_col] = result.get("url", "")
        row_data[price_col] = str(result.get("price", ""))

    # eBay情報
    row_data[11] = data.get("ebay_url", "")  # 最安販売先リンク
    row_data[12] = str(data.get("ebay_price", ""))  # 販売価格（米ドル）
    row_data[13] = str(data.get("ebay_shipping", ""))  # 販売送料（米ドル）

    # 利益計算結果
    row_data[14] = str(data.get("profit_no_rebate", ""))  # 還付抜き利益額（円）
    row_data[15] = str(data.get("profit_margin_no_rebate", ""))  # 利益率%（還付抜き）
    row_data[16] = str(data.get("profit_with_rebate", ""))  # 還付あり利益額（円）
    row_data[17] = str(data.get("profit_margin_with_rebate", ""))  # 利益率%（還付あり）

    # ステータスとメモ
    if data.get("error"):
        row_data[18] = "エラー"  # ステータス
        row_data[19] = f"ERROR: {data.get('error')}"  # メモ
    else:
        row_data[18] = "要確認"  # ステータス
        row_data[19] = f"自動処理 {datetime.now().strftime('%H:%M:%S')}"  # メモ

    # Write to specific row (A〜T列：20列)
    cell_range = f"A{row_number}:T{row_number}"
    worksheet.update(range_name=cell_range, values=[row_data])

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

        # Step 2: Generate search query (translate to Japanese)
        print(f"\n[2/5] Generating search query...")
        # TODO: Implement Gemini translation - for now use eBay title
        search_query = ebay_title
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
            ebay_item_url=args.ebay_url,
            ebay_price=ebay_price,
            ebay_shipping=ebay_shipping,
            sold_signal=0
        )

        # Get multiple offers (up to 3)
        offers = sourcing_client.search_multiple_offers(listing, max_results=3)

        if offers:
            print(f"  Found {len(offers)} offers:")
            for idx, offer in enumerate(offers, 1):
                print(f"  #{idx}: {offer.source_site} - JPY {offer.source_price_jpy:,.0f}")
                result_data["sourcing_results"].append({
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
                ebay_url=args.ebay_url,
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
        write_to_spreadsheet(sheets_client, args.row, result_data)

        print(f"\n{'='*60}")
        print(f"COMPLETED SUCCESSFULLY")
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
            write_to_spreadsheet(sheets_client, args.row, result_data)
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
