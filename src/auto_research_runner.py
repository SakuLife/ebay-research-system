"""Auto Research Runner - Pattern② full automation."""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

from dotenv import load_dotenv

from .ebay_client import EbayClient
from .sourcing import SourcingClient
from .profit import calculate_profit
from .sheets_client import GoogleSheetsClient
from .spreadsheet_mapping import INPUT_SHEET_COLUMNS
from .search_base_client import SearchBaseClient
from .config_loader import load_all_configs


def get_next_empty_row(sheet_client) -> int:
    """Get the next empty row number in the input sheet."""
    worksheet = sheet_client.spreadsheet.worksheet("入力シート")
    # Get all values in column A (date column)
    col_a_values = worksheet.col_values(1)
    # Return the next row after the last non-empty row (1-indexed)
    return len(col_a_values) + 1


def write_result_to_spreadsheet(sheet_client, data: dict):
    """Write research results to the next empty row in spreadsheet."""
    worksheet = sheet_client.spreadsheet.worksheet("入力シート")
    row_number = get_next_empty_row(sheet_client)

    # Prepare row data matching INPUT_SHEET_COLUMNS
    row_data = [""] * len(INPUT_SHEET_COLUMNS)

    # Map data to columns
    row_data[0] = datetime.now().strftime("%Y-%m-%d")  # 日付
    row_data[1] = data.get("ebay_url", "")  # 起点商品リンク
    row_data[2] = data.get("keyword", "")  # キーワード
    row_data[3] = data.get("category_name", "")  # カテゴリ
    row_data[4] = data.get("category_id", "")  # カテゴリ番号

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
    return row_number


def main():
    parser = argparse.ArgumentParser(description="eBay Auto Research Pipeline (Pattern②)")
    args = parser.parse_args()

    print(f"="*60)
    print(f"eBay AUTO RESEARCH PIPELINE (Pattern②)")
    print(f"="*60)

    # Load environment
    load_dotenv()

    # Initialize clients
    ebay_client = EbayClient()
    sourcing_client = SourcingClient()
    configs = load_all_configs()

    # For Google Sheets, handle both file path and JSON content
    service_account_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if service_account_json:
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

    # Initialize search base client for profit calculation
    search_base_client = SearchBaseClient(sheets_client)

    # Step 1: Read settings and keywords from '設定＆キーワード' sheet
    print(f"\n[1/6] Reading settings from '設定＆キーワード' sheet...")
    settings = sheets_client.read_settings()

    market = settings.get("market", "UK")
    min_profit_str = settings.get("min_profit", "フィルターなし")

    # Parse min_profit (handle "フィルターなし" = no filter)
    if min_profit_str == "フィルターなし" or not min_profit_str:
        min_profit_jpy = None  # No filter
        print(f"  [INFO] Market: {market}")
        print(f"  [INFO] Minimum profit: フィルターなし（全件出力）")
    else:
        min_profit_jpy = int(min_profit_str.replace("円", "").replace(",", ""))
        print(f"  [INFO] Market: {market}")
        print(f"  [INFO] Minimum profit: JPY {min_profit_jpy}")

    print(f"\n[2/6] Reading keywords from '設定＆キーワード' sheet...")
    keywords = sheets_client.read_keywords_from_settings()

    if not keywords:
        print(f"  [ERROR] No keywords found in '設定＆キーワード' sheet!")
        sys.exit(1)

    print(f"  [INFO] Keywords: {', '.join(keywords)}")

    # Step 2-5: Process each keyword
    total_processed = 0
    total_profitable = 0

    for keyword in keywords:
        print(f"\n{'='*60}")
        print(f"Processing keyword: {keyword}")
        print(f"{'='*60}")

        # Step 3: Search eBay listings
        # Try Marketplace Insights API first (sold items with sold count)
        # Fall back to Browse API if Insights API is not available
        print(f"\n[3/5] Searching eBay listings...")

        # Try Marketplace Insights API first (requires Limited Release access)
        sold_items = ebay_client.search_sold_items(keyword, market=market, min_sold=2)

        if sold_items:
            print(f"  [INFO] Using Marketplace Insights API (sold items)")
            active_items = sold_items
        else:
            # Fall back to Browse API (active listings)
            print(f"  [INFO] Falling back to Browse API (active listings)")
            active_items = ebay_client.search_active_listings(keyword, market=market)

        if not active_items:
            print(f"  [WARN] No eBay listings found for '{keyword}'")
            continue

        # Limit to top 5 items per keyword
        active_items = active_items[:5]

        for item in active_items:
            ebay_url = item.ebay_item_url
            ebay_price = item.ebay_price
            ebay_shipping = item.ebay_shipping

            print(f"\n  Processing: {ebay_url}")
            print(f"  [INFO] eBay price: ${ebay_price} + ${ebay_shipping} shipping")

            # Step 4: Search domestic sources (Rakuten + Amazon)
            print(f"\n[4/5] Searching domestic sources...")

            # Search Rakuten (returns list)
            rakuten_results = sourcing_client.rakuten_client.search_multiple(keyword, max_results=5)
            # Search Amazon (returns list)
            amazon_results = sourcing_client.amazon_client.search_multiple(keyword, max_results=5)

            # Combine and sort by total price
            all_sources = rakuten_results + amazon_results
            all_sources.sort(key=lambda x: x.source_price_jpy + x.source_shipping_jpy)

            if not all_sources:
                print(f"  [WARN] No domestic sources found")
                continue

            # Take best source
            best_source = all_sources[0]
            total_source_price = best_source.source_price_jpy + best_source.source_shipping_jpy

            print(f"  [INFO] Best source: {best_source.source_site} - JPY {total_source_price}")
            print(f"  [INFO] URL: {best_source.source_url}")

            # Step 5: Calculate profit
            print(f"\n[5/5] Calculating profit...")

            try:
                # Use search base client for accurate calculation
                search_base_client.write_input_data(
                    source_price_jpy=total_source_price,
                    ebay_price_usd=ebay_price,
                    ebay_shipping_usd=ebay_shipping,
                    ebay_url=ebay_url
                )

                calc_result = search_base_client.read_calculation_results(max_wait_seconds=5)

                if calc_result and calc_result.get("profit_no_rebate") is not None:
                    profit_no_rebate = calc_result["profit_no_rebate"]
                    profit_margin_no_rebate = calc_result.get("margin_no_rebate", 0)
                    profit_with_rebate = calc_result.get("profit_with_rebate", 0)
                    profit_margin_with_rebate = calc_result.get("margin_with_rebate", 0)
                else:
                    # Fallback to Python calculation
                    profit_result = calculate_profit(
                        ebay_price=ebay_price,
                        ebay_shipping=ebay_shipping,
                        source_price_jpy=total_source_price,
                        fee_rules=configs.fee_rules
                    )
                    profit_no_rebate = profit_result.profit_jpy_no_rebate
                    profit_margin_no_rebate = profit_result.profit_margin_no_rebate
                    profit_with_rebate = profit_result.profit_jpy_with_rebate
                    profit_margin_with_rebate = profit_result.profit_margin_with_rebate

            except Exception as e:
                print(f"  [ERROR] Profit calculation failed: {e}")
                profit_no_rebate = 0
                profit_margin_no_rebate = 0
                profit_with_rebate = 0
                profit_margin_with_rebate = 0

            print(f"  [INFO] Profit (no rebate): JPY {profit_no_rebate:.0f} ({profit_margin_no_rebate:.1f}%)")

            # Check if profit meets minimum threshold (skip if no filter set)
            if min_profit_jpy is not None and profit_no_rebate < min_profit_jpy:
                print(f"  [SKIP] Profit JPY {profit_no_rebate:.0f} is below minimum JPY {min_profit_jpy}")
                continue

            # Write to spreadsheet
            result_data = {
                "keyword": keyword,
                "ebay_url": ebay_url,
                "ebay_price": ebay_price,
                "ebay_shipping": ebay_shipping,
                "sourcing_results": [
                    {
                        "url": best_source.source_url,
                        "price": total_source_price
                    }
                ],
                "profit_no_rebate": profit_no_rebate,
                "profit_margin_no_rebate": profit_margin_no_rebate,
                "profit_with_rebate": profit_with_rebate,
                "profit_margin_with_rebate": profit_margin_with_rebate,
                "category_name": "",
                "category_id": ""
            }

            row_num = write_result_to_spreadsheet(sheets_client, result_data)
            total_processed += 1

            if profit_no_rebate > 0:
                total_profitable += 1

            print(f"  [SUCCESS] Result written to row {row_num}")

    # Summary
    print(f"\n{'='*60}")
    print(f"AUTO RESEARCH COMPLETED")
    print(f"{'='*60}")
    print(f"Total processed: {total_processed}")
    print(f"Profitable items: {total_profitable}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
