"""Auto Research Runner - Pattern② full automation."""

import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from dotenv import load_dotenv

from .ebay_client import EbayClient
from .sourcing import SourcingClient
from .profit import calculate_profit
from .sheets_client import GoogleSheetsClient
from .spreadsheet_mapping import INPUT_SHEET_COLUMNS
from .search_base_client import SearchBaseClient
from .config_loader import load_all_configs
from .weight_estimator import estimate_weight_from_price
from .models import SourceOffer, ListingCandidate
from .serpapi_client import SerpApiClient, ShoppingItem
from .gemini_client import GeminiClient


def extract_search_keywords(ebay_title: str) -> str:
    """
    eBayタイトルから日本の仕入先検索用のキーワードを抽出する。
    型番、ブランド名、シリーズ名などを優先的に抽出。
    """
    # 一般的なノイズワードを除去
    noise_words = {
        'new', 'used', 'rare', 'vintage', 'limited', 'edition', 'free', 'shipping',
        'japan', 'japanese', 'authentic', 'genuine', 'original', 'official',
        'sealed', 'mint', 'box', 'with', 'and', 'the', 'for', 'from',
    }

    # 型番パターン（英数字+ハイフン、例: RX-78-2, MG-100）
    model_numbers = re.findall(r'\b[A-Z]{1,4}[-]?\d{1,5}[-]?[A-Z0-9]*\b', ebay_title, re.IGNORECASE)

    # スケール表記（1/100, 1:144など）
    scales = re.findall(r'\b1[:/]\d{2,4}\b', ebay_title)

    # ブランド/シリーズ名（大文字始まりの単語）
    words = ebay_title.split()
    keywords = []

    for word in words:
        # クリーンアップ
        clean_word = re.sub(r'[^\w\s-]', '', word).strip()
        if not clean_word:
            continue
        lower_word = clean_word.lower()

        # ノイズワードをスキップ
        if lower_word in noise_words:
            continue

        # 2文字以上で、数字のみでないもの
        if len(clean_word) >= 2 and not clean_word.isdigit():
            keywords.append(clean_word)

    # 型番を優先的に追加
    result_parts = []
    if model_numbers:
        result_parts.extend(model_numbers[:2])
    if scales:
        result_parts.extend(scales[:1])

    # 残りのキーワードを追加（最大5単語）
    for kw in keywords[:5]:
        if kw not in result_parts:
            result_parts.append(kw)

    return ' '.join(result_parts[:5])


def calculate_title_similarity(ebay_title: str, source_title: str) -> float:
    """
    eBayタイトルと仕入先タイトルの類似度を計算する。
    共通キーワードの割合を返す（0.0〜1.0）。
    """
    if not ebay_title or not source_title:
        return 0.0

    # 正規化: 小文字化、記号除去
    def normalize(text: str) -> set:
        # 英数字とひらがな・カタカナ・漢字を抽出
        words = re.findall(r'[a-zA-Z0-9]+|[\u3040-\u309F]+|[\u30A0-\u30FF]+|[\u4E00-\u9FFF]+', text.lower())
        # 2文字以上の単語のみ
        return set(w for w in words if len(w) >= 2)

    ebay_words = normalize(ebay_title)
    source_words = normalize(source_title)

    if not ebay_words or not source_words:
        return 0.0

    # 共通キーワード数
    common = ebay_words & source_words

    # 類似度 = 共通キーワード数 / min(両方のキーワード数)
    similarity = len(common) / min(len(ebay_words), len(source_words))

    return similarity


def find_best_matching_source(
    ebay_title: str,
    sources: List[SourceOffer],
    min_similarity: float = 0.2
) -> Optional[SourceOffer]:
    """
    eBayタイトルに最もマッチする仕入先を見つける。
    類似度が閾値未満の場合はNoneを返す。
    """
    if not sources:
        return None

    best_source = None
    best_similarity = 0.0

    for source in sources:
        similarity = calculate_title_similarity(ebay_title, source.title)
        if similarity > best_similarity:
            best_similarity = similarity
            best_source = source

    if best_similarity >= min_similarity:
        return best_source

    return None


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

    # Map data to columns (19 columns: A-S)
    row_data[0] = datetime.now().strftime("%Y-%m-%d")  # 日付
    row_data[1] = data.get("keyword", "")  # キーワード
    row_data[2] = data.get("category_name", "")  # カテゴリ
    row_data[3] = data.get("category_id", "")  # カテゴリ番号

    # ソーシング結果（国内最安①②③）
    sourcing_results = data.get("sourcing_results", [])
    for idx, result in enumerate(sourcing_results[:3]):
        url_col = 4 + (idx * 2)  # 4, 6, 8
        price_col = 5 + (idx * 2)  # 5, 7, 9
        row_data[url_col] = result.get("url", "")
        row_data[price_col] = str(result.get("price", ""))

    # eBay情報
    row_data[10] = data.get("ebay_url", "")  # eBayリンク
    row_data[11] = str(data.get("ebay_price", ""))  # 販売価格（米ドル）
    row_data[12] = str(data.get("ebay_shipping", ""))  # 販売送料（米ドル）

    # 利益計算結果
    row_data[13] = str(data.get("profit_no_rebate", ""))  # 還付抜き利益額（円）
    row_data[14] = str(data.get("profit_margin_no_rebate", ""))  # 利益率%（還付抜き）
    row_data[15] = str(data.get("profit_with_rebate", ""))  # 還付あり利益額（円）
    row_data[16] = str(data.get("profit_margin_with_rebate", ""))  # 利益率%（還付あり）

    # ステータスとメモ
    if data.get("error"):
        row_data[17] = "エラー"  # ステータス
        row_data[18] = f"ERROR: {data.get('error')}"  # メモ
    else:
        row_data[17] = "要確認"  # ステータス
        row_data[18] = f"自動処理 {datetime.now().strftime('%H:%M:%S')}"  # メモ

    # Write to specific row (A〜S列：19列)
    cell_range = f"A{row_number}:S{row_number}"
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
    min_price_str = settings.get("min_price", "100")
    min_profit_str = settings.get("min_profit", "フィルターなし")
    items_per_keyword_str = settings.get("items_per_keyword", "5")
    min_sold_str = settings.get("min_sold", "0")

    # Weight settings
    default_weight = settings.get("default_weight", "自動推定")
    packaging_weight_str = settings.get("packaging_weight", "500")
    size_multiplier_str = settings.get("size_multiplier", "1.0")

    # Parse values
    min_price_usd = float(min_price_str) if min_price_str.replace(".", "").isdigit() else 100.0
    items_per_keyword = int(items_per_keyword_str) if items_per_keyword_str.isdigit() else 5
    items_per_keyword = max(1, min(10, items_per_keyword))  # Clamp to 1-10
    min_sold = int(min_sold_str) if min_sold_str.isdigit() else 0
    packaging_weight_g = int(packaging_weight_str) if packaging_weight_str.isdigit() else 500
    size_multiplier = float(size_multiplier_str) if size_multiplier_str else 1.0

    print(f"  [INFO] Market: {market}")
    print(f"  [INFO] Min price: ${min_price_usd}")
    print(f"  [INFO] Items per keyword: {items_per_keyword}")
    print(f"  [INFO] Min sold: {min_sold}" if min_sold > 0 else "  [INFO] Min sold: (disabled)")

    # Parse min_profit (handle "フィルターなし" = no filter)
    if min_profit_str == "フィルターなし" or not min_profit_str:
        min_profit_jpy = None  # No filter
        print(f"  [INFO] Min profit: フィルターなし（全件出力）")
    else:
        min_profit_jpy = int(min_profit_str.replace("円", "").replace(",", ""))
        print(f"  [INFO] Min profit: JPY {min_profit_jpy}")

    print(f"  [INFO] Weight: {default_weight}, Packaging: {packaging_weight_g}g, Size: x{size_multiplier}")

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

        # Step 3: Search eBay sold items
        # Priority: SerpApi (sold items) > Browse API (active listings)
        print(f"\n[3/5] Searching eBay sold items (${min_price_usd}+)...")

        # Currency conversion for local price filter
        currency_rates = {"UK": 0.79, "US": 1.0, "EU": 0.92}  # USD to local
        local_rate = currency_rates.get(market, 0.79)
        min_price_local = min_price_usd * local_rate

        # Try SerpApi first (sold items - past completed listings)
        serpapi_client = SerpApiClient()
        sold_items = []

        if serpapi_client.is_enabled:
            serpapi_results = serpapi_client.search_sold_items(
                keyword, market=market, min_price=min_price_local, max_results=items_per_keyword * 2
            )

            # Convert SerpApi results to ListingCandidate format
            for sold_item in serpapi_results:
                # Convert price to USD
                usd_rates = {"GBP": 1.27, "EUR": 1.09, "USD": 1.0}
                usd_rate = usd_rates.get(sold_item.currency, 1.0)
                price_usd = sold_item.price * usd_rate

                sold_items.append(ListingCandidate(
                    candidate_id=sold_item.item_id,
                    search_query=keyword,
                    ebay_item_url=sold_item.link,
                    ebay_price=price_usd,
                    ebay_shipping=0.0,  # SerpApi doesn't provide shipping cost separately
                    sold_signal=1,  # It's a sold item
                    ebay_title=sold_item.title,
                    currency=sold_item.currency,
                ))

        if sold_items:
            print(f"  [INFO] Using SerpApi (past sold items)")
            active_items = sold_items
        else:
            # Fall back to Browse API (active listings)
            print(f"  [INFO] Falling back to Browse API (active listings)")
            active_items = ebay_client.search_active_listings(
                keyword, market=market, min_price_usd=min_price_usd, min_sold=min_sold
            )

        if not active_items:
            print(f"  [WARN] No eBay listings found for '{keyword}'")
            continue

        # Limit to configured items per keyword
        active_items = active_items[:items_per_keyword]

        for item in active_items:
            ebay_url = item.ebay_item_url
            ebay_price = item.ebay_price
            ebay_shipping = item.ebay_shipping
            ebay_title = getattr(item, 'ebay_title', '') or ''
            item_currency = getattr(item, 'currency', 'USD')
            category_id = getattr(item, 'category_id', '') or ''
            category_name = getattr(item, 'category_name', '') or ''

            print(f"\n  Processing: {ebay_url}")
            print(f"  [INFO] eBay title: {ebay_title[:60]}..." if len(ebay_title) > 60 else f"  [INFO] eBay title: {ebay_title}")
            print(f"  [INFO] eBay price: ${ebay_price:.2f} ({item_currency}) + ${ebay_shipping} shipping")
            if category_name:
                print(f"  [INFO] Category: {category_name} ({category_id})")

            # Step 4: Search domestic sources (SerpAPI Google Shopping)
            # 1. まず英語のままで検索
            # 2. 見つからなければ日本語に翻訳して再検索

            print(f"\n[4/5] Searching domestic sources...")

            all_sources = []
            best_source = None

            # === 1. 英語のままで検索 ===
            if serpapi_client.is_enabled and ebay_title:
                print(f"  [SerpAPI] Searching (English): {ebay_title[:50]}...")
                shopping_results = serpapi_client.search_google_shopping_jp(ebay_title, max_results=10)

                # ShoppingItemをSourceOfferに変換
                for shop_item in shopping_results:
                    if shop_item.price > 0:
                        price_jpy = shop_item.price
                        if shop_item.currency == "USD":
                            price_jpy = shop_item.price * 150

                        all_sources.append(SourceOffer(
                            source_site=shop_item.source,
                            source_url=shop_item.link,
                            source_price_jpy=price_jpy,
                            source_shipping_jpy=0,
                            title=shop_item.title,
                        ))

                print(f"  [SerpAPI] Found {len(all_sources)} items (English search)")

                # 類似商品があるかチェック
                if all_sources:
                    best_source = find_best_matching_source(ebay_title, all_sources, min_similarity=0.2)

            # === 2. 英語で見つからなければ日本語で再検索 ===
            if not best_source and serpapi_client.is_enabled:
                print(f"  [INFO] No match with English, trying Japanese...")

                # Geminiで翻訳
                gemini_client = GeminiClient()
                japanese_query = None
                if gemini_client.is_enabled and ebay_title:
                    japanese_query = gemini_client.translate_product_name(ebay_title)
                    if japanese_query:
                        print(f"  [Gemini] Translated: {japanese_query}")

                # 翻訳失敗時は型番抽出
                if not japanese_query:
                    japanese_query = extract_search_keywords(ebay_title) if ebay_title else keyword
                    print(f"  [INFO] Using extracted keywords: {japanese_query}")

                # 日本語で再検索
                print(f"  [SerpAPI] Searching (Japanese): {japanese_query}")
                shopping_results = serpapi_client.search_google_shopping_jp(japanese_query, max_results=10)

                all_sources = []  # リセット
                for shop_item in shopping_results:
                    if shop_item.price > 0:
                        price_jpy = shop_item.price
                        if shop_item.currency == "USD":
                            price_jpy = shop_item.price * 150

                        all_sources.append(SourceOffer(
                            source_site=shop_item.source,
                            source_url=shop_item.link,
                            source_price_jpy=price_jpy,
                            source_shipping_jpy=0,
                            title=shop_item.title,
                        ))

                print(f"  [SerpAPI] Found {len(all_sources)} items (Japanese search)")

                # 日本語検索結果から類似商品を探す
                if all_sources:
                    best_source = find_best_matching_source(ebay_title, all_sources, min_similarity=0.2)

            # 結果判定
            error_reason = None

            if not best_source:
                if not all_sources:
                    print(f"  [WARN] No domestic sources found")
                    error_reason = "国内仕入先なし"
                else:
                    print(f"  [WARN] No matching product found (title similarity too low)")
                    print(f"         eBay: {ebay_title[:50]}...")
                    print(f"         Best candidate: {all_sources[0].title[:50]}...")
                    error_reason = "類似商品なし"

            # 仕入先が見つかった場合の処理
            total_source_price = 0
            similarity = 0.0
            if best_source:
                total_source_price = best_source.source_price_jpy + best_source.source_shipping_jpy
                similarity = calculate_title_similarity(ebay_title, best_source.title)

                print(f"  [INFO] Best source: {best_source.source_site} - JPY {total_source_price}")
                print(f"  [INFO] Source title: {best_source.title[:50]}..." if len(best_source.title) > 50 else f"  [INFO] Source title: {best_source.title}")
                print(f"  [INFO] Title similarity: {similarity:.0%}")
                print(f"  [INFO] URL: {best_source.source_url}")

            # Step 5: Calculate profit (with weight estimation)
            profit_no_rebate = 0
            profit_margin_no_rebate = 0
            profit_with_rebate = 0
            profit_margin_with_rebate = 0

            # 仕入先がある場合のみ利益計算
            if best_source:
                print(f"\n[5/5] Calculating profit...")

                # Estimate weight based on keyword and price
                weight_est = estimate_weight_from_price(ebay_price, keyword.split()[0].lower())

                # Apply size multiplier from settings
                adjusted_depth = weight_est.depth_cm * size_multiplier
                adjusted_width = weight_est.width_cm * size_multiplier
                adjusted_height = weight_est.height_cm * size_multiplier

                # Apply packaging weight from settings (override default)
                adjusted_weight_g = weight_est.actual_weight_g - 500 + packaging_weight_g

                print(f"  [INFO] Weight estimate: {adjusted_weight_g}g (packaging: {packaging_weight_g}g)")
                print(f"  [INFO] Dimensions: {adjusted_depth:.1f}x{adjusted_width:.1f}x{adjusted_height:.1f}cm (x{size_multiplier})")

                try:
                    # Use search base client for accurate calculation
                    search_base_client.write_input_data(
                        source_price_jpy=total_source_price,
                        ebay_price_usd=ebay_price,
                        ebay_shipping_usd=ebay_shipping,
                        ebay_url=ebay_url,
                        weight_g=adjusted_weight_g,
                        depth_cm=adjusted_depth,
                        width_cm=adjusted_width,
                        height_cm=adjusted_height,
                        category_id=category_id
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
                    error_reason = f"計算エラー: {str(e)[:30]}"

                print(f"  [INFO] Profit (no rebate): JPY {profit_no_rebate:.0f} ({profit_margin_no_rebate:.1f}%)")

                # Check if profit meets minimum threshold
                if min_profit_jpy is not None and profit_no_rebate < min_profit_jpy:
                    print(f"  [INFO] Profit JPY {profit_no_rebate:.0f} is below minimum JPY {min_profit_jpy}")
                    error_reason = f"利益不足 (JPY {profit_no_rebate:.0f})"
            else:
                print(f"\n[5/5] Skipping profit calculation (no source found)")

            # Write to spreadsheet（常に出力、エラー時もerror付きで）
            sourcing_results = []
            if best_source:
                sourcing_results.append({
                    "url": best_source.source_url,
                    "price": total_source_price
                })

            result_data = {
                "keyword": keyword,
                "ebay_url": ebay_url,
                "ebay_price": ebay_price,
                "ebay_shipping": ebay_shipping,
                "sourcing_results": sourcing_results,
                "profit_no_rebate": profit_no_rebate,
                "profit_margin_no_rebate": profit_margin_no_rebate,
                "profit_with_rebate": profit_with_rebate,
                "profit_margin_with_rebate": profit_margin_with_rebate,
                "category_name": category_name,
                "category_id": category_id,
                "error": error_reason  # エラー理由（None or 文字列）
            }

            row_num = write_result_to_spreadsheet(sheets_client, result_data)
            total_processed += 1

            if profit_no_rebate > 0 and not error_reason:
                total_profitable += 1

            if error_reason:
                print(f"  [WRITTEN] Row {row_num} (Error: {error_reason})")
            else:
                print(f"  [SUCCESS] Result written to row {row_num}")

            # Rate limit protection: wait 2 seconds between items
            # Google Sheets API limit is 60 writes/minute
            import time
            time.sleep(2)

    # Summary
    print(f"\n{'='*60}")
    print(f"AUTO RESEARCH COMPLETED")
    print(f"{'='*60}")
    print(f"Total processed: {total_processed}")
    print(f"Profitable items: {total_profitable}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
