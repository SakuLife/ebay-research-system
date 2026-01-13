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
class AliExpressItem:
    """AliExpress商品の情報."""
    title: str
    price: float
    currency: str
    link: str
    item_id: str
    thumbnail: str = ""
    rating: float = 0.0
    orders: int = 0  # 注文数


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

    # フリマ・中古系サイト（New条件時に除外）
    # これ以外のサイトは全て許可（AliExpress, Alibaba等も含む）
    FLEA_MARKET_DOMAINS = [
        "mercari.com",      # メルカリ
        "jp.mercari.com",
        "yahoo.co.jp",      # ヤフオク
        "auctions.yahoo.co.jp",
        "paypayfleamarket", # PayPayフリマ
        "fril.jp",          # ラクマ
        "rakuma.rakuten",
        "magi.camp",        # magi
        "2ndstreet",        # セカスト
        "bookoff",          # ブックオフ
        "hardoff",          # ハードオフ
        "suruga-ya.jp",     # 駿河屋（中古メイン）
        "mandarake",        # まんだらけ
        "lashinbang",       # らしんばん
    ]

    # 除外すべきサイト（検索結果に出てきても無視）
    EXCLUDED_DOMAINS = [
        "ebay.com",         # eBay自体
        "ebay.co.uk",
        "ebay.de",
        "google.com",       # Googleリダイレクト
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

    # eBay Item Location codes
    EBAY_LOCATION_CODES = {
        "japan": "104",
        "us": "1",
        "uk": "3",
        "germany": "77",
        "china": "45",
        "worldwide": None,  # フィルタなし
    }

    def search_sold_items(
        self,
        keyword: str,
        market: str = "UK",
        min_price: float = 0,
        max_results: int = 20,
        item_location: str = "japan",
        condition: str = "any"
    ) -> List[SoldItem]:
        """
        eBayで売れた商品（完了したリスティング）を検索する.

        Args:
            keyword: 検索キーワード
            market: マーケット (UK, US, EU)
            min_price: 最低価格（現地通貨）
            max_results: 最大取得件数
            item_location: 商品所在地フィルタ ("japan", "us", "uk", "worldwide")
                           デフォルトは"japan"（日本から出品された商品のみ）
            condition: 商品状態 ("new", "used", "any")

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

        # Add item location filter (日本から出品された商品に限定)
        location_code = self.EBAY_LOCATION_CODES.get(item_location.lower())
        if location_code:
            params["_salic"] = location_code
            print(f"  [SerpApi] Filtering by location: {item_location} (code={location_code})")

        # Add condition filter (eBay uses different format)
        # Note: LH_ItemCondition uses "1000" for New, "3000" for Used on eBay
        # But SerpApi may not support this for sold items - skip for now
        # Condition filtering will be done on domestic source search instead

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

    def search_aliexpress(
        self,
        keyword: str,
        max_results: int = 10
    ) -> List[AliExpressItem]:
        """
        AliExpressで商品を検索する.
        中国からの仕入れ先として使用.

        Args:
            keyword: 検索キーワード（英語推奨）
            max_results: 最大取得件数

        Returns:
            AliExpressItemのリスト
        """
        if not self.is_enabled:
            print("  [WARN] SerpApi is not available")
            return []

        params = {
            "engine": "aliexpress",
            "q": keyword,
            "api_key": self.api_key
        }

        try:
            print(f"  [SerpApi] Searching AliExpress: '{keyword}'")
            search = GoogleSearch(params)
            results = search.get_dict()

            if "error" in results:
                print(f"  [ERROR] SerpApi AliExpress error: {results['error']}")
                return []

            organic = results.get("organic_results", [])
            print(f"  [SerpApi] Found {len(organic)} AliExpress items")

            items = []
            for item in organic[:max_results]:
                try:
                    title = item.get("title", "")
                    link = item.get("link", "")
                    item_id = item.get("product_id", "") or item.get("item_id", "")
                    thumbnail = item.get("thumbnail", "")

                    # Parse price (USD)
                    price = 0.0
                    price_info = item.get("price", {})
                    if isinstance(price_info, dict):
                        price = price_info.get("extracted", 0) or price_info.get("value", 0) or 0
                    elif isinstance(price_info, (int, float)):
                        price = float(price_info)
                    elif isinstance(price_info, str):
                        match = re.search(r'[\d.]+', price_info.replace(',', ''))
                        if match:
                            price = float(match.group())

                    # 価格0はスキップ
                    if price <= 0:
                        continue

                    # Rating and orders
                    rating = item.get("rating", 0) or 0
                    orders = 0
                    orders_str = item.get("orders", "") or item.get("sold", "")
                    if orders_str:
                        match = re.search(r'[\d,]+', str(orders_str).replace(',', ''))
                        if match:
                            orders = int(match.group().replace(',', ''))

                    items.append(AliExpressItem(
                        title=title,
                        price=price,
                        currency="USD",
                        link=link,
                        item_id=item_id,
                        thumbnail=thumbnail,
                        rating=rating,
                        orders=orders,
                    ))

                except Exception as e:
                    print(f"  [WARN] Failed to parse AliExpress item: {e}")
                    continue

            return items

        except Exception as e:
            print(f"  [ERROR] SerpApi AliExpress request failed: {e}")
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

    def _is_excluded_site(self, url: str, condition: str = "any") -> bool:
        """
        URLが除外すべきサイトかどうか判定する.

        Args:
            url: チェックするURL
            condition: "new"=フリマ除外, "used"/"any"=除外なし

        Returns:
            True=除外すべき, False=許可
        """
        url_lower = url.lower()

        # 常に除外するサイト（eBay自体、Googleリダイレクト等）
        if any(domain in url_lower for domain in self.EXCLUDED_DOMAINS):
            return True

        # New条件の場合、フリマ・中古系を除外
        if condition.lower() == "new":
            if any(domain in url_lower for domain in self.FLEA_MARKET_DOMAINS):
                return True

        return False

    def _extract_source_name(self, url: str) -> str:
        """URLからソース名を抽出する."""
        url_lower = url.lower()

        # 日本のサイト
        if "amazon.co.jp" in url_lower or "amazon.jp" in url_lower:
            return "Amazon"
        if "rakuten.co.jp" in url_lower:
            return "楽天"
        if "yahoo" in url_lower:
            return "Yahoo"
        if "mercari" in url_lower:
            return "メルカリ"
        if "yodobashi" in url_lower:
            return "ヨドバシ"
        if "biccamera" in url_lower:
            return "ビックカメラ"
        if "suruga-ya" in url_lower:
            return "駿河屋"

        # 海外サイト
        if "aliexpress" in url_lower:
            return "AliExpress"
        if "alibaba" in url_lower:
            return "Alibaba"
        if "amazon.com" in url_lower:
            return "Amazon US"
        if "walmart" in url_lower:
            return "Walmart"
        if "target.com" in url_lower:
            return "Target"

        # ドメインから抽出
        try:
            from urllib.parse import urlparse
            domain = urlparse(url).netloc
            # www.を除去
            if domain.startswith("www."):
                domain = domain[4:]
            # 最初のドット前を取得
            return domain.split(".")[0].capitalize()
        except:
            return "その他"

    def search_by_image(
        self,
        image_url: str,
        condition: str = "any",
        max_results: int = 10
    ) -> List[ShoppingItem]:
        """
        Google Lensで画像検索する.
        eBay商品画像から仕入先を探す（フリマ除外はconditionで制御）.

        Args:
            image_url: eBay商品の画像URL
            condition: "new"=フリマ除外, "used"/"any"=全サイト対象
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
            excluded_count = 0

            for item in visual_matches[:max_results * 3]:  # 余裕を持って取得
                try:
                    title = item.get("title", "")
                    # リンクは複数フィールドを確認
                    link = item.get("link", "") or item.get("product_link", "") or item.get("source_link", "")

                    # リンクが空の場合はスキップ
                    if not link:
                        continue

                    # 除外サイトチェック（フリマ除外 or 常に除外）
                    if self._is_excluded_site(link, condition):
                        excluded_count += 1
                        continue

                    # ソース名を抽出
                    source = self._extract_source_name(link)

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

            if excluded_count > 0:
                print(f"  [SerpApi] Excluded {excluded_count} sites (condition={condition})")
            print(f"  [SerpApi] Found {len(items)} valid matches")
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
        ブラックリスト方式：フリマ系以外は全て許可.

        Args:
            keyword: 検索キーワード
            condition: 商品状態 "new"=フリマ除外, "used"/"any"=全サイト
            max_results: 最大取得件数

        Returns:
            ShoppingItemのリスト
        """
        if not self.is_enabled:
            print("  [WARN] SerpApi is not available")
            return []

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
            excluded_count = 0

            for item in organic_results:
                try:
                    link = item.get("link", "")

                    # 除外サイトチェック（フリマ除外 or 常に除外）
                    if self._is_excluded_site(link, condition):
                        excluded_count += 1
                        continue

                    title = item.get("title", "")
                    snippet = item.get("snippet", "")

                    # ソース名を抽出
                    source = self._extract_source_name(link)

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

            if excluded_count > 0:
                print(f"  [SerpApi] Excluded {excluded_count} sites (condition={condition})")
            print(f"  [SerpApi] Found {len(items)} valid matches")
            return items

        except Exception as e:
            print(f"  [ERROR] SerpApi Google Web request failed: {e}")
            return []
