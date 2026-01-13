"""SerpApi client for eBay sold items search and Amazon.co.jp search."""

import os
import re
from typing import List, Optional
from dataclasses import dataclass

try:
    from serpapi import GoogleSearch
    SERPAPI_AVAILABLE = True
except ImportError:
    SERPAPI_AVAILABLE = False


def clean_query_for_shopping(query: str, max_length: int = 100) -> str:
    """
    eBayタイトルをGoogle Shopping検索用に整形する.

    - 販売者情報・配送情報など関係ない言葉を削除
    - Bundle/Lot/Collection系を削除
    - PSA 10等のグレーディングは残す（重要な識別子）
    - 文字数制限

    Args:
        query: 元のクエリ（eBayタイトル）
        max_length: 最大文字数

    Returns:
        整形後のクエリ
    """
    if not query:
        return ""

    # 販売者・配送関連を削除（商品と無関係）
    noise_patterns = [
        r'\bFREE\s*SHIPPING\b',
        r'\bFAST\s*SHIPPING\b',
        r'\bUS\s*SELLER\b',
        r'\bUK\s*SELLER\b',
        r'\bJAPAN\s*IMPORT\b',
        r'\bJAPANESE\s*VERSION\b',
        r'\bFROM\s*JAPAN\b',
        r'\bSHIPS?\s*FROM\b',
        r'\bWORLDWIDE\b',
        # バンドル・まとめ売り系
        r'\b\d+\s*CARDS?\s*LOT\b',      # "180 Cards Lot"
        r'\bLOT\s*OF\s*\d+\b',           # "Lot of 50"
        r'\bBUNDLE\b',
        r'\bCOLLECTION\b',
        r'\bBULK\b',
        r'\bSET\s*OF\s*\d+\b',           # "Set of 10"
        # 宣伝文句
        r'\bMUST\s*SEE\b',
        r'\bLOOK\b',
        r'\bWOW\b',
        r'\bHOT\b',
        r'\bL@@K\b',
        r'\bNR\b',                        # No Reserve
        r'\bNO\s*RESERVE\b',
    ]
    for pattern in noise_patterns:
        query = re.sub(pattern, '', query, flags=re.IGNORECASE)

    # 余計な記号を削除（括弧内の短い記号表記など）
    query = re.sub(r'\([^)]{1,3}\)', '', query)   # (NM) (JP) など短いもの
    query = re.sub(r'\[[^\]]{1,3}\]', '', query)  # [NM] など短いもの

    # 複数のスペースを1つに
    query = re.sub(r'\s+', ' ', query).strip()

    # 文字数制限（単語単位で切る）
    if len(query) > max_length:
        words = query.split()
        result = []
        current_len = 0
        for word in words:
            if current_len + len(word) + 1 <= max_length:
                result.append(word)
                current_len += len(word) + 1
            else:
                break
        query = ' '.join(result)

    return query.strip()


@dataclass
class SoldItem:
    """売れた商品の情報."""
    title: str
    price: float
    currency: str
    link: str
    item_id: str
    condition: str = ""
    shipping: str = ""
    thumbnail: str = ""  # 商品サムネイル画像URL
    category_id: str = ""
    category_name: str = ""


@dataclass
class AmazonItem:
    """Amazon商品の情報."""
    title: str
    price: float
    currency: str
    link: str
    asin: str
    rating: float = 0.0


@dataclass
class ShoppingItem:
    """Google Shopping商品の情報."""
    title: str
    price: float
    currency: str
    link: str
    source: str  # 販売サイト名（Amazon, StockX, 楽天など）
    thumbnail: str = ""


class SerpApiClient:
    """SerpApiを使ってeBayの売れた商品を検索するクライアント."""

    # 新品サイト（New時のみ対象）
    NEW_DOMAINS = [
        "rakuten.co.jp",
        "amazon.co.jp",
        "yodobashi.com",
        "biccamera.com",
        "joshin.co.jp",
    ]

    # 中古サイト（Used時に追加で対象）
    USED_DOMAINS = [
        "mercari.com",
        "yahoo.co.jp",      # ヤフオク
        "magi.camp",
        "snkrdunk.com",
        "suruga-ya.jp",
        "trader.co.jp",
        "fril.jp",          # ラクマ
        "2ndstreet.jp",
    ]

    def __init__(self, api_key: Optional[str] = None):
        """
        Args:
            api_key: SerpApi API key. If not provided, reads from SERP_API_KEY env var.
        """
        self.api_key = api_key or os.getenv("SERP_API_KEY")
        self.is_enabled = bool(self.api_key) and SERPAPI_AVAILABLE

        if not SERPAPI_AVAILABLE:
            print("  [WARN] SerpApi library not installed. Run: pip install google-search-results")
        elif not self.api_key:
            print("  [WARN] SERP_API_KEY not set. SerpApi disabled.")

    def search_sold_items(
        self,
        keyword: str,
        market: str = "UK",
        min_price: float = 0,
        max_results: int = 20
    ) -> List[SoldItem]:
        """
        eBayで売れた商品（完了したリスティング）を検索する.

        Args:
            keyword: 検索キーワード
            market: マーケット (UK, US, EU)
            min_price: 最低価格（現地通貨）
            max_results: 最大取得件数

        Returns:
            SoldItemのリスト
        """
        if not self.is_enabled:
            print("  [WARN] SerpApi is not available")
            return []

        # Market to domain mapping
        domain_map = {
            "UK": "ebay.co.uk",
            "US": "ebay.com",
            "EU": "ebay.de",
        }
        ebay_domain = domain_map.get(market, "ebay.co.uk")

        params = {
            "engine": "ebay",
            "ebay_domain": ebay_domain,
            "_nkw": keyword,
            "LH_Sold": "1",       # 売れた商品
            "LH_Complete": "1",   # 完了したリスティング
            "_ipg": str(min(max_results * 2, 60)),  # 余裕を持って取得
            "api_key": self.api_key
        }

        # Add price filter if specified
        if min_price > 0:
            params["_udlo"] = str(int(min_price))  # Minimum price

        try:
            print(f"  [SerpApi] Searching sold items: '{keyword}' on {ebay_domain}")
            search = GoogleSearch(params)
            results = search.get_dict()

            # Check for errors
            if "error" in results:
                print(f"  [ERROR] SerpApi error: {results['error']}")
                return []

            organic = results.get("organic_results", [])
            print(f"  [SerpApi] Found {len(organic)} sold items")

            sold_items = []
            for item in organic[:max_results]:
                try:
                    title = item.get("title", "")
                    link = item.get("link", "")

                    # Extract item ID from link
                    item_id = ""
                    if "/itm/" in link:
                        import re
                        match = re.search(r'/itm/(\d+)', link)
                        if match:
                            item_id = match.group(1)

                    # Parse price
                    price_info = item.get("price", {})
                    if isinstance(price_info, dict):
                        price = price_info.get("extracted", 0) or price_info.get("from", {}).get("extracted", 0)
                        raw_price = price_info.get("raw", "")
                    else:
                        price = 0
                        raw_price = str(price_info)

                    # Determine currency from raw price
                    currency = "GBP" if "£" in raw_price else "USD" if "$" in raw_price else "EUR"

                    # Skip if below min_price
                    if min_price > 0 and price < min_price:
                        continue

                    condition = item.get("condition", "")
                    shipping = item.get("shipping", "")
                    thumbnail = item.get("thumbnail", "")

                    # カテゴリ情報を取得（SerpApiの結果に含まれている場合）
                    category_id = item.get("category_id", "") or item.get("categoryId", "")
                    category_name = item.get("category_name", "") or item.get("categoryName", "")
                    # extensions内にカテゴリがある場合もある
                    extensions = item.get("extensions", [])
                    if not category_name and extensions:
                        # extensionsの最初の要素がカテゴリ名の場合がある
                        for ext in extensions:
                            if isinstance(ext, str) and not ext.startswith("Free"):
                                category_name = ext
                                break

                    sold_items.append(SoldItem(
                        title=title,
                        price=price,
                        currency=currency,
                        link=link,
                        item_id=item_id,
                        condition=condition,
                        shipping=shipping,
                        thumbnail=thumbnail,
                        category_id=category_id,
                        category_name=category_name,
                    ))

                except Exception as e:
                    print(f"  [WARN] Failed to parse sold item: {e}")
                    continue

            return sold_items

        except Exception as e:
            print(f"  [ERROR] SerpApi request failed: {e}")
            return []

    def search_amazon_jp(
        self,
        keyword: str,
        max_results: int = 5
    ) -> List[AmazonItem]:
        """
        Amazon.co.jpで商品を検索する.

        Args:
            keyword: 検索キーワード（日本語推奨）
            max_results: 最大取得件数

        Returns:
            AmazonItemのリスト
        """
        if not self.is_enabled:
            print("  [WARN] SerpApi is not available")
            return []

        params = {
            "engine": "amazon",
            "amazon_domain": "amazon.co.jp",
            "k": keyword,
            "api_key": self.api_key
        }

        try:
            print(f"  [SerpApi] Searching Amazon.co.jp: '{keyword}'")
            search = GoogleSearch(params)
            results = search.get_dict()

            if "error" in results:
                print(f"  [ERROR] SerpApi Amazon error: {results['error']}")
                return []

            organic = results.get("organic_results", [])
            print(f"  [SerpApi] Found {len(organic)} Amazon items")

            items = []
            for item in organic[:max_results]:
                try:
                    title = item.get("title", "")
                    link = item.get("link", "")
                    asin = item.get("asin", "")

                    # Parse price
                    price_info = item.get("price", {})
                    if isinstance(price_info, dict):
                        price = price_info.get("extracted", 0) or 0
                        raw_price = price_info.get("raw", "")
                    elif isinstance(price_info, str):
                        # Try to extract number from string like "¥1,234"
                        import re
                        match = re.search(r'[\d,]+', price_info.replace(',', ''))
                        price = float(match.group().replace(',', '')) if match else 0
                        raw_price = price_info
                    else:
                        price = 0
                        raw_price = ""

                    # If price not in main field, try price.raw
                    if price == 0 and "price" in item:
                        raw = item.get("price", {}).get("raw", "")
                        if raw:
                            import re
                            match = re.search(r'[\d,]+', raw.replace(',', ''))
                            if match:
                                price = float(match.group().replace(',', ''))

                    # Also check extracted_price
                    if price == 0:
                        price = item.get("extracted_price", 0) or 0

                    rating = item.get("rating", 0) or 0

                    items.append(AmazonItem(
                        title=title,
                        price=price,
                        currency="JPY",
                        link=link,
                        asin=asin,
                        rating=rating,
                    ))

                except Exception as e:
                    print(f"  [WARN] Failed to parse Amazon item: {e}")
                    continue

            return items

        except Exception as e:
            print(f"  [ERROR] SerpApi Amazon request failed: {e}")
            return []

    def _extract_url_from_google_redirect(self, google_url: str) -> Optional[str]:
        """
        Google.comのリダイレクトURLから実際の商品URLを抽出する.

        Args:
            google_url: google.comを含むURL

        Returns:
            抽出された実URL、または抽出できない場合はNone
        """
        if not google_url or "google.com" not in google_url:
            return None

        try:
            from urllib.parse import urlparse, parse_qs, unquote

            parsed = urlparse(google_url)
            query_params = parse_qs(parsed.query)

            # よくあるリダイレクトパラメータ
            for param in ["url", "q", "u", "adurl", "dest", "redirect"]:
                if param in query_params:
                    extracted = unquote(query_params[param][0])
                    # google.comでない実際のURLの場合は返す
                    if extracted and "google.com" not in extracted and extracted.startswith("http"):
                        return extracted

            return None
        except Exception:
            return None

    def search_google_shopping_jp(
        self,
        keyword: str,
        max_results: int = 10
    ) -> List[ShoppingItem]:
        """
        Google Shopping（日本）で商品を検索する.
        Amazon, 楽天, StockXなど複数サイトの結果を一括取得.

        Args:
            keyword: 検索キーワード
            max_results: 最大取得件数

        Returns:
            ShoppingItemのリスト
        """
        if not self.is_enabled:
            print("  [WARN] SerpApi is not available")
            return []

        params = {
            "engine": "google_shopping",
            "q": keyword,
            "location": "Japan",
            "hl": "ja",
            "gl": "jp",
            "api_key": self.api_key
        }

        try:
            print(f"  [SerpApi] Searching Google Shopping: '{keyword}'")
            search = GoogleSearch(params)
            results = search.get_dict()

            if "error" in results:
                print(f"  [ERROR] SerpApi Google Shopping error: {results['error']}")
                return []

            shopping_results = results.get("shopping_results", [])
            print(f"  [SerpApi] Found {len(shopping_results)} shopping items")

            items = []
            skipped_google_urls = 0

            for item in shopping_results[:max_results * 2]:  # 余裕を持って取得
                try:
                    title = item.get("title", "")
                    source = item.get("source", "")
                    thumbnail = item.get("thumbnail", "")

                    # リンク取得（優先順位: product_link > source_link > sellers[0].link）
                    link = item.get("product_link", "") or item.get("source_link", "")

                    # sellersがあればそこから直接リンクを取得
                    if not link or "google.com" in link:
                        sellers = item.get("sellers", [])
                        if sellers and isinstance(sellers, list) and len(sellers) > 0:
                            seller_link = sellers[0].get("link", "")
                            if seller_link and "google.com" not in seller_link:
                                link = seller_link

                    # google.comのリダイレクトURLから実URLを抽出を試みる
                    if link and "google.com" in link:
                        extracted_url = self._extract_url_from_google_redirect(link)
                        if extracted_url:
                            link = extracted_url

                    # 最終的にgoogle.comのURLしかない場合はスキップ
                    if not link or "google.com" in link:
                        skipped_google_urls += 1
                        continue

                    # Parse price
                    price = 0.0
                    extracted_price = item.get("extracted_price", 0)
                    if extracted_price:
                        price = float(extracted_price)
                    else:
                        price_str = item.get("price", "")
                        if price_str:
                            # 数値を抽出（カンマ除去）
                            match = re.search(r'[\d,]+', price_str.replace(',', ''))
                            if match:
                                price = float(match.group().replace(',', ''))

                    # 価格0円はスキップ
                    if price <= 0:
                        continue

                    # 通貨判定
                    price_str = item.get("price", "")
                    if "¥" in price_str or "円" in price_str:
                        currency = "JPY"
                    elif "$" in price_str:
                        currency = "USD"
                    else:
                        currency = "JPY"  # デフォルト

                    items.append(ShoppingItem(
                        title=title,
                        price=price,
                        currency=currency,
                        link=link,
                        source=source,
                        thumbnail=thumbnail,
                    ))

                    if len(items) >= max_results:
                        break

                except Exception as e:
                    print(f"  [WARN] Failed to parse shopping item: {e}")
                    continue

            if skipped_google_urls > 0:
                print(f"  [SerpApi] Skipped {skipped_google_urls} items with google.com URLs")
            print(f"  [SerpApi] Returning {len(items)} valid shopping items")

            return items

        except Exception as e:
            print(f"  [ERROR] SerpApi Google Shopping request failed: {e}")
            return []

    def search_by_image(
        self,
        image_url: str,
        max_results: int = 10
    ) -> List[ShoppingItem]:
        """
        Google Lensで画像検索する.
        eBay商品画像から日本の仕入先（メルカリ、ヤフオク、楽天など）を探す.

        Args:
            image_url: eBay商品の画像URL
            max_results: 最大取得件数

        Returns:
            ShoppingItemのリスト
        """
        if not self.is_enabled:
            print("  [WARN] SerpApi is not available")
            return []

        params = {
            "engine": "google_lens",
            "url": image_url,
            "hl": "ja",
            "country": "jp",
            "api_key": self.api_key
        }

        # 検索対象の日本のサイトドメイン
        target_domains = [
            "mercari.com",
            "yahoo.co.jp",
            "rakuten.co.jp",
            "amazon.co.jp",
            "magi.camp",
            "snkrdunk.com",
            "suruga-ya.jp",
            "trader.co.jp",
        ]

        try:
            print(f"  [SerpApi] Google Lens image search...")
            search = GoogleSearch(params)
            results = search.get_dict()

            if "error" in results:
                print(f"  [ERROR] SerpApi Google Lens error: {results['error']}")
                return []

            # visual_matches に視覚的に似ている商品が入っている
            visual_matches = results.get("visual_matches", [])
            print(f"  [SerpApi] Found {len(visual_matches)} visual matches")

            items = []
            for item in visual_matches[:max_results * 2]:  # 余裕を持って取得
                try:
                    title = item.get("title", "")
                    # リンクは複数フィールドを確認
                    link = item.get("link", "") or item.get("product_link", "") or item.get("source_link", "")
                    source = item.get("source", "")

                    # リンクが空の場合はスキップ
                    if not link:
                        continue

                    # 日本のサイトのみフィルタ
                    if not any(domain in link for domain in target_domains):
                        continue

                    # ソース名を短く
                    if "mercari" in link:
                        source = "メルカリ"
                    elif "yahoo" in link:
                        source = "ヤフオク"
                    elif "rakuten" in link:
                        source = "楽天"
                    elif "amazon" in link:
                        source = "Amazon"
                    elif "suruga" in link:
                        source = "駿河屋"

                    # 価格取得
                    price = 0.0
                    price_info = item.get("price", {})
                    if isinstance(price_info, dict):
                        price = price_info.get("extracted_value", 0) or 0
                    elif isinstance(price_info, (int, float)):
                        price = float(price_info)

                    thumbnail = item.get("thumbnail", "")

                    items.append(ShoppingItem(
                        title=title,
                        price=price,
                        currency="JPY",
                        link=link,
                        source=source,
                        thumbnail=thumbnail,
                    ))

                    if len(items) >= max_results:
                        break

                except Exception as e:
                    print(f"  [WARN] Failed to parse visual match: {e}")
                    continue

            print(f"  [SerpApi] Found {len(items)} Japanese site matches")
            return items

        except Exception as e:
            print(f"  [ERROR] SerpApi Google Lens request failed: {e}")
            return []

    def search_google_web_jp(
        self,
        keyword: str,
        condition: str = "any",
        max_results: int = 10
    ) -> List[ShoppingItem]:
        """
        Google Web検索（日本）で商品を検索する.
        Shopping検索で結果がない場合のフォールバック用.
        日本のECサイト（メルカリ、ヤフオク、楽天、Amazon等）のみ抽出.

        Args:
            keyword: 検索キーワード
            condition: 商品状態 "new", "used", "any"
            max_results: 最大取得件数

        Returns:
            ShoppingItemのリスト
        """
        if not self.is_enabled:
            print("  [WARN] SerpApi is not available")
            return []

        # conditionに基づいて対象ドメインを決定
        if condition == "new":
            target_domains = self.NEW_DOMAINS
        elif condition == "used":
            target_domains = self.NEW_DOMAINS + self.USED_DOMAINS
        else:
            target_domains = self.NEW_DOMAINS + self.USED_DOMAINS

        params = {
            "engine": "google",
            "q": keyword,
            "location": "Japan",
            "hl": "ja",
            "gl": "jp",
            "num": 50,  # 多めに取得してフィルタ
            "api_key": self.api_key
        }

        try:
            print(f"  [SerpApi] Google Web search: '{keyword[:50]}...'")
            search = GoogleSearch(params)
            results = search.get_dict()

            if "error" in results:
                print(f"  [ERROR] SerpApi Google Web error: {results['error']}")
                return []

            organic_results = results.get("organic_results", [])
            print(f"  [SerpApi] Found {len(organic_results)} web results")

            items = []
            for item in organic_results:
                try:
                    link = item.get("link", "")

                    # 日本のECサイトのみフィルタ
                    if not any(domain in link for domain in target_domains):
                        continue

                    title = item.get("title", "")
                    snippet = item.get("snippet", "")

                    # ソース名を判定
                    source = ""
                    if "mercari.com" in link:
                        source = "メルカリ"
                    elif "yahoo.co.jp" in link:
                        source = "ヤフオク"
                    elif "rakuten.co.jp" in link:
                        source = "楽天"
                    elif "amazon.co.jp" in link:
                        source = "Amazon"
                    elif "suruga-ya.jp" in link:
                        source = "駿河屋"
                    elif "yodobashi.com" in link:
                        source = "ヨドバシ"
                    elif "biccamera.com" in link:
                        source = "ビックカメラ"
                    elif "magi.camp" in link:
                        source = "magi"
                    elif "snkrdunk.com" in link:
                        source = "スニダン"
                    elif "fril.jp" in link:
                        source = "ラクマ"
                    elif "2ndstreet.jp" in link:
                        source = "セカスト"
                    else:
                        source = "その他"

                    # 価格を抽出（snippet内の円表記から）
                    price = 0.0
                    # "1,234円" or "¥1,234" パターン
                    price_match = re.search(r'[¥￥]?\s*([\d,]+)\s*円', snippet)
                    if price_match:
                        price = float(price_match.group(1).replace(',', ''))
                    else:
                        # "¥1,234" パターン（円なし）
                        price_match = re.search(r'[¥￥]\s*([\d,]+)', snippet)
                        if price_match:
                            price = float(price_match.group(1).replace(',', ''))

                    # 価格0円の場合はスキップ（利益計算できない）
                    if price <= 0:
                        continue

                    thumbnail = item.get("thumbnail", "")

                    items.append(ShoppingItem(
                        title=title,
                        price=price,
                        currency="JPY",
                        link=link,
                        source=source,
                        thumbnail=thumbnail,
                    ))

                    if len(items) >= max_results:
                        break

                except Exception as e:
                    print(f"  [WARN] Failed to parse web result: {e}")
                    continue

            print(f"  [SerpApi] Found {len(items)} Japanese EC matches")
            return items

        except Exception as e:
            print(f"  [ERROR] SerpApi Google Web request failed: {e}")
            return []
