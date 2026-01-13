"""SerpApi client for eBay sold items search and Amazon.co.jp search."""

import os
from typing import List, Optional
from dataclasses import dataclass

try:
    from serpapi import GoogleSearch
    SERPAPI_AVAILABLE = True
except ImportError:
    SERPAPI_AVAILABLE = False


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

                    sold_items.append(SoldItem(
                        title=title,
                        price=price,
                        currency=currency,
                        link=link,
                        item_id=item_id,
                        condition=condition,
                        shipping=shipping,
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
            for item in shopping_results[:max_results]:
                try:
                    title = item.get("title", "")
                    link = item.get("link", "")
                    source = item.get("source", "")
                    thumbnail = item.get("thumbnail", "")

                    # Parse price
                    price = 0.0
                    extracted_price = item.get("extracted_price", 0)
                    if extracted_price:
                        price = float(extracted_price)
                    else:
                        price_str = item.get("price", "")
                        if price_str:
                            import re
                            # 数値を抽出（カンマ除去）
                            match = re.search(r'[\d,]+', price_str.replace(',', ''))
                            if match:
                                price = float(match.group().replace(',', ''))

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

                except Exception as e:
                    print(f"  [WARN] Failed to parse shopping item: {e}")
                    continue

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
                    link = item.get("link", "")
                    source = item.get("source", "")

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
