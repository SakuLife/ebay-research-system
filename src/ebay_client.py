"""eBay client interfaces and mock implementation."""

from __future__ import annotations

import os
import re
import uuid
import base64
import requests
from typing import List, Optional, Dict, Any
from urllib.parse import urlparse, parse_qs

from .models import ListingCandidate, ListingRequest, ListingResult


class EbayClient:
    """Real eBay API client using Browse API."""

    def __init__(self):
        self.client_id = os.getenv("EBAY_CLIENT_ID")
        self.client_secret = os.getenv("EBAY_CLIENT_SECRET")
        self.use_sandbox = os.getenv("EBAY_USE_SANDBOX", "true").lower() == "true"

        # Production App ID for Finding API (optional, falls back to client_id)
        # Finding API requires production credentials to get real sold data
        self.finding_app_id = os.getenv("EBAY_PRODUCTION_APP_ID", self.client_id)

        # Sandbox or Production endpoints for OAuth/Browse API
        if self.use_sandbox:
            self.oauth_url = "https://api.sandbox.ebay.com/identity/v1/oauth2/token"
            self.browse_url = "https://api.sandbox.ebay.com/buy/browse/v1"
            self.insights_url = "https://api.sandbox.ebay.com/buy/marketplace_insights/v1_beta"
        else:
            self.oauth_url = "https://api.ebay.com/identity/v1/oauth2/token"
            self.browse_url = "https://api.ebay.com/buy/browse/v1"
            self.insights_url = "https://api.ebay.com/buy/marketplace_insights/v1_beta"

        self._access_token: Optional[str] = None
        self._insights_token: Optional[str] = None  # Separate token for Insights API

    def _get_access_token(self) -> str:
        """Get OAuth access token using Client Credentials grant."""
        if self._access_token:
            return self._access_token

        # Base64 encode client_id:client_secret
        credentials = f"{self.client_id}:{self.client_secret}"
        b64_credentials = base64.b64encode(credentials.encode()).decode()

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {b64_credentials}"
        }

        data = {
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope"
        }

        response = requests.post(self.oauth_url, headers=headers, data=data)
        response.raise_for_status()

        self._access_token = response.json()["access_token"]
        return self._access_token

    def _get_insights_token(self) -> str:
        """Get OAuth access token with Marketplace Insights scope."""
        if self._insights_token:
            return self._insights_token

        credentials = f"{self.client_id}:{self.client_secret}"
        b64_credentials = base64.b64encode(credentials.encode()).decode()

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {b64_credentials}"
        }

        data = {
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope/buy.marketplace.insights"
        }

        response = requests.post(self.oauth_url, headers=headers, data=data)
        response.raise_for_status()

        self._insights_token = response.json()["access_token"]
        return self._insights_token

    def search_sold_items(self, keyword: str, market: str = "UK", min_sold: int = 1) -> List[ListingCandidate]:
        """
        Search sold items using Marketplace Insights API.

        Args:
            keyword: Search keyword
            market: Market (UK, US, EU)
            min_sold: Minimum sold quantity to filter

        Returns:
            List of ListingCandidate with sold items data
        """
        try:
            token = self._get_insights_token()
        except Exception as e:
            print(f"  [WARN] Marketplace Insights API not available: {e}")
            return []

        # Market to Marketplace ID mapping
        marketplace_map = {
            "UK": "EBAY_GB",
            "US": "EBAY_US",
            "EU": "EBAY_DE"
        }
        marketplace_id = marketplace_map.get(market, "EBAY_GB")

        headers = {
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": marketplace_id
        }

        params = {
            "q": keyword,
            "limit": 50,
        }

        url = f"{self.insights_url}/item_sales/search"

        try:
            response = requests.get(url, headers=headers, params=params, timeout=15)

            if response.status_code == 403:
                print(f"  [WARN] Marketplace Insights API access denied (Limited Release API)")
                return []

            if response.status_code != 200:
                print(f"  [ERROR] Insights API error {response.status_code}: {response.text[:200]}")
                return []

            data = response.json()
            items = data.get("itemSales", [])

            if not items:
                print(f"  [INFO] No sold items found for '{keyword}'")
                return []

            candidates = []
            for item in items:
                try:
                    total_sold = item.get("totalSoldQuantity", 0)

                    # Filter by minimum sold quantity
                    if total_sold < min_sold:
                        continue

                    last_sold_price = item.get("lastSoldPrice", {})
                    price = float(last_sold_price.get("value", 0))

                    item_href = item.get("itemHref", "")
                    title = item.get("title", "")

                    candidate = ListingCandidate(
                        candidate_id=str(uuid.uuid4()),
                        search_query=keyword,
                        ebay_item_url=item_href,
                        ebay_price=price,
                        ebay_shipping=0,
                        sold_signal=total_sold,
                    )
                    candidates.append(candidate)

                except (KeyError, ValueError, TypeError) as e:
                    continue

            print(f"  [INFO] Found {len(candidates)} sold items (sold >= {min_sold}) for '{keyword}'")
            return candidates

        except requests.exceptions.RequestException as e:
            print(f"  [ERROR] Insights API network error: {e}")
            return []

    def _extract_item_id(self, url: str) -> Optional[str]:
        """Extract eBay item ID from URL."""
        # Handle short URLs like ebay.us/m/xZnI6h
        if "ebay.us" in url or "ebay.to" in url:
            try:
                # Follow redirect to get full URL with max_redirects limit
                session = requests.Session()
                session.max_redirects = 10
                response = session.get(url, allow_redirects=True, timeout=10)
                url = response.url
            except requests.exceptions.TooManyRedirects:
                print(f"  [WARN] Too many redirects for URL: {url}")
                # Try to extract from the short URL itself
                # ebay.us/m/xZnI6h might have item ID in query params after redirect
                return None

        # Extract from /itm/123456789 or /itm/title/123456789
        match = re.search(r'/itm/(?:[^/]+/)?(\d+)', url)
        if match:
            return match.group(1)

        # Extract from ?item=123456789
        parsed = urlparse(url)
        query_params = parse_qs(parsed.query)
        if 'item' in query_params:
            return query_params['item'][0]

        return None

    def get_item_by_url(self, url: str) -> Optional[Dict[str, Any]]:
        """Get item details from eBay URL."""
        item_id = self._extract_item_id(url)
        if not item_id:
            print(f"  [WARN] Could not extract item ID from URL: {url}")
            return None

        return self.get_item_by_id(item_id)

    def get_item_by_id(self, item_id: str) -> Optional[Dict[str, Any]]:
        """Get item details by item ID using Browse API."""
        token = self._get_access_token()

        headers = {
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": "EBAY_US"  # or EBAY_GB for UK
        }

        # If item_id already contains v1|, use it as-is, otherwise format it
        if item_id.startswith("v1|"):
            url = f"{self.browse_url}/item/{item_id}"
        else:
            url = f"{self.browse_url}/item/v1|{item_id}|0"

        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            print(f"  [ERROR] eBay API error: {e}")
            if e.response.status_code == 404:
                print(f"  [INFO] Item {item_id} not found (may be sold/removed)")
            return None

    def get_item_category(self, item_id: str, market: str = "UK") -> tuple:
        """
        Get category info for an item by item ID.

        Args:
            item_id: eBay item ID (numeric string)
            market: Market (UK, US, EU)

        Returns:
            Tuple of (category_id, category_name) or ("", "") if not found
        """
        try:
            token = self._get_access_token()

            marketplace_map = {
                "UK": "EBAY_GB",
                "US": "EBAY_US",
                "EU": "EBAY_DE"
            }
            marketplace_id = marketplace_map.get(market, "EBAY_GB")

            headers = {
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": marketplace_id
            }

            # Format item ID for API
            if item_id.startswith("v1|"):
                url = f"{self.browse_url}/item/{item_id}"
            else:
                url = f"{self.browse_url}/item/v1|{item_id}|0"

            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code != 200:
                return ("", "")

            data = response.json()

            # Extract category info
            category_id = data.get("categoryId", "")
            category_path = data.get("categoryPath", "")

            # categoryPath is like "Toys & Hobbies|Model Railroads & Trains|N Scale"
            # Take the last part as category name
            if category_path:
                category_name = category_path.split("|")[-1]
            else:
                category_name = ""

            return (str(category_id), category_name)

        except Exception as e:
            print(f"    [WARN] Failed to get category: {e}")
            return ("", "")

    def search_active_listings(
        self,
        keyword: str,
        market: str = "UK",
        min_price_usd: float = 0,
        min_sold: int = 0
    ) -> List[ListingCandidate]:
        """
        Search active listings using eBay Browse API.

        Note: Finding API (findCompletedItems) has been deprecated.
        This method uses Browse API to search active listings instead.

        Args:
            keyword: Search keyword
            market: Market (UK, US, EU) - default UK
            min_price_usd: Minimum price in USD
            min_sold: Minimum sold quantity (requires extra API calls per item)

        Returns:
            List of ListingCandidate with active items
        """
        token = self._get_access_token()

        # Market to Marketplace ID mapping
        marketplace_map = {
            "UK": "EBAY_GB",
            "US": "EBAY_US",
            "EU": "EBAY_DE"
        }
        marketplace_id = marketplace_map.get(market, "EBAY_GB")

        headers = {
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": marketplace_id
        }

        # Search for active listings
        # Filters: Fixed Price only, New condition, optional min price
        # Sort: Best Match (default, most relevant first)
        filter_parts = ["buyingOptions:{FIXED_PRICE}", "conditionIds:{1000}"]

        # Currency conversion rates (approximate USD to local currency)
        # Using conservative rates to ensure we don't miss items at the boundary
        currency_info = {
            "UK": {"rate": 0.78, "currency": "GBP"},  # USD to GBP (conservative)
            "US": {"rate": 1.0, "currency": "USD"},   # USD to USD
            "EU": {"rate": 0.90, "currency": "EUR"},  # USD to EUR (conservative)
        }

        # Add minimum price filter if specified (convert to local currency)
        if min_price_usd > 0:
            info = currency_info.get(market, {"rate": 1.0, "currency": "USD"})
            local_price = int(min_price_usd * info["rate"])
            # IMPORTANT: priceCurrency is required for price filter to work correctly
            filter_parts.append(f"priceCurrency:{info['currency']}")
            filter_parts.append(f"price:[{local_price}..]")
            print(f"  [INFO] Price filter: ${min_price_usd}+ = {info['currency']} {local_price}+")

        params = {
            "q": keyword,
            # No "sort" param = Best Match (relevance, default)
            "limit": 50,
            "filter": ",".join(filter_parts)
        }

        url = f"{self.browse_url}/item_summary/search"

        try:
            response = requests.get(url, headers=headers, params=params, timeout=15)

            if response.status_code != 200:
                try:
                    error_data = response.json()
                    errors = error_data.get("errors", [])
                    for err in errors:
                        print(f"  [ERROR] Browse API: {err.get('message', 'Unknown error')}")
                except:
                    print(f"  [ERROR] Browse API HTTP {response.status_code}: {response.text[:200]}")
                return []

            data = response.json()
            items = data.get("itemSummaries", [])

            if not items:
                print(f"  [INFO] No active listings found for keyword: {keyword}")
                return []

            candidates = []
            for item in items[:50]:  # Check more items to find enough above min_price
                try:
                    item_id = item.get("itemId", "")
                    title = item.get("title", "")
                    view_url = item.get("itemWebUrl", "")

                    # Get price (in local currency)
                    price_info = item.get("price", {})
                    price_local = float(price_info.get("value", 0))
                    currency = price_info.get("currency", "USD")

                    # Convert to USD for comparison
                    usd_rates = {"GBP": 1.27, "EUR": 1.09, "USD": 1.0}  # Local to USD
                    usd_rate = usd_rates.get(currency, 1.0)
                    price = price_local * usd_rate  # Convert to USD

                    # Code-level price filter (compare in USD)
                    if min_price_usd > 0 and price < min_price_usd:
                        # Debug log for filtered items
                        # print(f"  [DEBUG] Filtered: {currency} {price_local:.2f} = ${price:.2f} < ${min_price_usd}")
                        continue  # Skip items below min price

                    # Get shipping cost (if available)
                    shipping_options = item.get("shippingOptions", [])
                    shipping_cost = 0.0
                    if shipping_options:
                        shipping_cost_info = shipping_options[0].get("shippingCost", {})
                        shipping_cost = float(shipping_cost_info.get("value", 0))

                    # Use item sold quantity as signal (if available)
                    # Browse API doesn't directly provide sold count, so we use 1 as default
                    sold_signal = 1

                    # Get category info
                    categories = item.get("categories", [])
                    category_id = ""
                    category_name = ""
                    if categories:
                        category_id = categories[0].get("categoryId", "")
                        category_name = categories[0].get("categoryName", "")

                    # Get image URL (for Google Lens search)
                    image_url = ""
                    image_info = item.get("image", {})
                    if image_info:
                        image_url = image_info.get("imageUrl", "")
                    # Fallback to thumbnailImages
                    if not image_url:
                        thumbnails = item.get("thumbnailImages", [])
                        if thumbnails:
                            image_url = thumbnails[0].get("imageUrl", "")

                    candidate = ListingCandidate(
                        candidate_id=str(uuid.uuid4()),
                        search_query=keyword,
                        ebay_item_url=view_url,
                        ebay_price=price,
                        ebay_shipping=shipping_cost,
                        sold_signal=sold_signal,
                        category_id=category_id,
                        category_name=category_name,
                        ebay_title=title,
                        currency=currency,
                        image_url=image_url,
                    )
                    candidates.append(candidate)

                    # Stop after finding 20 valid items
                    if len(candidates) >= 20:
                        break

                except (KeyError, ValueError, TypeError) as e:
                    print(f"  [WARN] Failed to parse item: {e}")
                    continue

            print(f"  [INFO] Found {len(candidates)} active listings for '{keyword}'")

            # Apply min_sold filter if specified (requires extra API calls)
            if min_sold > 0 and candidates:
                print(f"  [INFO] Filtering by min_sold >= {min_sold}...")
                filtered_candidates = []

                for candidate in candidates:
                    # Extract itemId from URL or use legacyItemId
                    item_url = candidate.ebay_item_url
                    # Try to get sold quantity from item details
                    try:
                        # Extract legacy item ID from URL
                        import re
                        match = re.search(r'/itm/(\d+)', item_url)
                        if match:
                            legacy_id = match.group(1)
                            item_id = f"v1|{legacy_id}|0"

                            # Get item details
                            item_url_api = f"https://api.ebay.com/buy/browse/v1/item/{item_id}"
                            item_resp = requests.get(item_url_api, headers=headers)

                            if item_resp.status_code == 200:
                                item_data = item_resp.json()
                                availabilities = item_data.get("estimatedAvailabilities", [])
                                if availabilities:
                                    sold_qty = availabilities[0].get("estimatedSoldQuantity", 0)
                                    candidate.sold_signal = sold_qty

                                    if sold_qty >= min_sold:
                                        filtered_candidates.append(candidate)
                                        print(f"    [OK] {candidate.ebay_title[:40]}... (Sold: {sold_qty})")
                                    else:
                                        print(f"    [SKIP] {candidate.ebay_title[:40]}... (Sold: {sold_qty} < {min_sold})")
                                else:
                                    # No availability info, skip
                                    pass
                            else:
                                # API error, include anyway
                                filtered_candidates.append(candidate)

                            # Rate limiting
                            import time
                            time.sleep(0.1)

                    except Exception as e:
                        print(f"    [WARN] Failed to get sold qty: {e}")
                        filtered_candidates.append(candidate)

                print(f"  [INFO] After sold filter: {len(filtered_candidates)} items")
                return filtered_candidates

            return candidates

        except requests.exceptions.RequestException as e:
            print(f"  [ERROR] Browse API network error: {e}")
            return []

    # Keep old method name for backward compatibility
    def search_completed(self, keyword: str, market: str = "UK") -> List[ListingCandidate]:
        """
        Deprecated: Finding API (findCompletedItems) has been decommissioned.
        This now calls search_active_listings() using Browse API instead.
        """
        print(f"  [INFO] Using Browse API (Finding API deprecated)")
        return self.search_active_listings(keyword, market)

    def find_cheapest_active_listing(
        self,
        ebay_title: str,
        sold_price_usd: float,
        market: str = "UK",
        item_location: str = "japan",
        condition: str = "New",
        gemini_client: Any = None,
        ebay_image_url: str = "",
    ) -> Optional[Dict[str, Any]]:
        """
        売済み品のタイトルをベースに、同一商品の現在最安アクティブリスティングを検索.

        SerpAPIで見つけた売済み品と同一の商品が、より安い価格で
        アクティブ出品されていないかをBrowse APIで確認する。
        Gemini画像比較が利用可能な場合、タイトル類似度が低い候補も
        画像で同一商品判定を行い、より広範囲から最安値を探す。

        Args:
            ebay_title: eBayの商品タイトル（SerpAPI売済み品）
            sold_price_usd: 売済み品の価格（USD換算）
            market: マーケット (UK, US, EU)
            item_location: 出品者の所在地フィルター
            condition: 商品状態 ("New", "Used", None)
            gemini_client: Geminiクライアント（画像比較用、Noneなら従来のタイトル判定のみ）
            ebay_image_url: eBay商品の画像URL（Gemini画像比較用）

        Returns:
            Dict with cheapest active listing details, or None if not found
        """
        from difflib import SequenceMatcher

        token = self._get_access_token()

        marketplace_map = {
            "UK": "EBAY_GB",
            "US": "EBAY_US",
            "EU": "EBAY_DE"
        }
        marketplace_id = marketplace_map.get(market, "EBAY_GB")

        headers = {
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": marketplace_id
        }

        # フィルター構築
        filter_parts = ["buyingOptions:{FIXED_PRICE}"]

        # 商品状態フィルター
        if condition == "New":
            filter_parts.append("conditionIds:{1000}")

        # 出品者所在地フィルター
        location_map = {
            "japan": "JP",
            "us": "US",
            "uk": "GB",
        }
        country_code = location_map.get(item_location.lower(), "")
        if country_code:
            filter_parts.append(f"itemLocationCountry:{country_code}")

        # 検索クエリを最適化: ノイズワードを除去し、ブランド名+型番+商品名に絞る
        # Browse APIのqパラメータは長すぎると精度が下がり、ノイズで関連性も低下する
        noise_words = {
            "new", "used", "brand", "free", "shipping", "ship", "from", "japan",
            "japanese", "authentic", "genuine", "original", "official", "sealed",
            "rare", "vintage", "limited", "edition", "oem", "nib", "nwt", "nwb",
            "f/s", "fs", "mint", "box", "with", "and", "the", "for",
        }
        words = ebay_title.split()
        filtered = [w for w in words if w.lower().strip("!.,()[]【】") not in noise_words]
        search_query = " ".join(filtered) if filtered else ebay_title
        # さらに長すぎる場合は先頭の重要な単語に絞る
        if len(search_query) > 80:
            search_query = " ".join(filtered[:10])

        params = {
            "q": search_query,
            "sort": "price",  # 最安値順
            "limit": 200,  # 最大200件取得（旧: 20件）
            "filter": ",".join(filter_parts)
        }

        url = f"{self.browse_url}/item_summary/search"

        # Gemini画像比較が利用可能か判定
        use_gemini_image = (
            gemini_client is not None
            and ebay_image_url
            and hasattr(gemini_client, 'compare_product_images')
        )

        # Geminiで商品特定に最適な短縮クエリも生成（候補が少ない場合の再検索用）
        gemini_short_query = ""
        if use_gemini_image and gemini_client and hasattr(gemini_client, 'model'):
            try:
                prompt = f'''eBayの商品タイトルから、同じ商品の別出品を検索するための最短キーワードを生成してください。

ルール:
- ブランド名 + 商品名/型番 のみ（5〜8語以内）
- 数量・サイズ・状態説明・送料情報は除外
- 英語のまま出力

商品タイトル: {ebay_title}

検索キーワード:'''
                response = gemini_client.model.generate_content(prompt)
                gemini_short_query = response.text.strip().split('\n')[0].strip()
                if len(gemini_short_query) > 100 or len(gemini_short_query) < 5:
                    gemini_short_query = ""
            except Exception:
                gemini_short_query = ""

        try:
            response = requests.get(url, headers=headers, params=params, timeout=15)

            if response.status_code != 200:
                print(f"  [最安値検索] Browse API error: HTTP {response.status_code}")
                return None

            data = response.json()
            items = data.get("itemSummaries", [])

            # 候補が少ない場合（5件以下）、Gemini短縮クエリで再検索して候補を追加
            if len(items) <= 5 and gemini_short_query:
                print(f"  [最安値検索] 候補{len(items)}件 → Gemini短縮クエリで再検索: '{gemini_short_query}'")
                retry_params = {**params, "q": gemini_short_query}
                try:
                    retry_response = requests.get(url, headers=headers, params=retry_params, timeout=15)
                    if retry_response.status_code == 200:
                        retry_data = retry_response.json()
                        retry_items = retry_data.get("itemSummaries", [])
                        # 既存のitem IDと重複しないものを追加
                        existing_ids = {item.get("itemId") for item in items}
                        new_items = [i for i in retry_items if i.get("itemId") not in existing_ids]
                        items.extend(new_items)
                        print(f"  [最安値検索] Gemini再検索で{len(new_items)}件追加 → 合計{len(items)}件")
                except Exception as e:
                    print(f"  [最安値検索] Gemini再検索失敗: {e}")

            if not items:
                print(f"  [最安値検索] アクティブリスティングなし")
                return None

            print(f"  [最安値検索] {len(items)}件の候補を検索 (Gemini画像比較: {'ON' if use_gemini_image else 'OFF'})")

            # USD変換レート
            usd_rates = {"GBP": 1.27, "EUR": 1.09, "USD": 1.0}

            # タイトル類似度で同一商品を判定し、最安値を選択
            # 閾値: 50%以上 → 即採用、30-50% → Gemini画像比較で判定
            SIMILARITY_AUTO_ACCEPT = 0.50  # タイトルだけで同一商品と判定
            SIMILARITY_IMAGE_CHECK = 0.30  # 画像比較の対象にする下限
            ebay_title_lower = ebay_title.lower().strip()
            best_match = None
            skipped_low_sim = 0
            gemini_checked = 0
            gemini_matched = 0

            for item in items:
                title = item.get("title", "")
                title_lower = title.lower().strip()

                # タイトル類似度チェック
                sim = SequenceMatcher(None, ebay_title_lower, title_lower).ratio()

                # 類似度30%未満は明らかに別商品 → スキップ
                if sim < SIMILARITY_IMAGE_CHECK:
                    skipped_low_sim += 1
                    continue

                # 類似度30-50%: Gemini画像比較で同一商品判定
                if sim < SIMILARITY_AUTO_ACCEPT:
                    if not use_gemini_image:
                        skipped_low_sim += 1
                        continue  # Gemini未使用時は従来通り50%で足切り

                    # 候補の画像URLを取得
                    candidate_image = item.get("image", {}).get("imageUrl", "")
                    if not candidate_image:
                        # サムネイルURLもチェック
                        candidate_image = item.get("thumbnailImages", [{}])[0].get("imageUrl", "") if item.get("thumbnailImages") else ""
                    if not candidate_image:
                        skipped_low_sim += 1
                        continue

                    # Gemini画像比較（APIコスト: 低）
                    gemini_checked += 1
                    try:
                        is_match = gemini_client.compare_product_images(
                            ebay_image_url=ebay_image_url,
                            source_image_url=candidate_image,
                            ebay_title=ebay_title,
                            source_title=title,
                        )
                    except Exception as e:
                        print(f"    [Gemini] 画像比較エラー: {e}")
                        is_match = None

                    if is_match is not True:
                        continue  # MISMATCH or UNCERTAIN → スキップ
                    gemini_matched += 1
                    print(f"    [Gemini] 画像MATCH: 類似度{sim:.0%} '{title[:50]}...'")

                # 価格取得
                price_info = item.get("price", {})
                price_local = float(price_info.get("value", 0))
                currency = price_info.get("currency", "USD")
                usd_rate = usd_rates.get(currency, 1.0)
                price_usd = price_local * usd_rate

                if price_usd <= 0:
                    continue

                # 送料取得
                shipping_cost = 0.0
                shipping_options = item.get("shippingOptions", [])
                if shipping_options:
                    shipping_info = shipping_options[0].get("shippingCost", {})
                    shipping_cost = float(shipping_info.get("value", 0))

                total_price_usd = price_usd + (shipping_cost * usd_rate)

                item_url = item.get("itemWebUrl", "")
                item_id = item.get("itemId", "")

                if best_match is None or total_price_usd < best_match["total_price_usd"]:
                    best_match = {
                        "item_id": item_id,
                        "title": title,
                        "url": item_url,
                        "price": price_usd,
                        "price_local": price_local,
                        "currency": currency,
                        "shipping": shipping_cost,
                        "total_price_usd": total_price_usd,
                        "similarity": sim,
                    }

            if gemini_checked > 0:
                print(f"  [最安値検索] Gemini画像比較: {gemini_checked}件チェック → {gemini_matched}件MATCH")

            if not best_match:
                print(f"  [最安値検索] 同一商品のアクティブリスティングなし（類似度30%未満: {skipped_low_sim}件）")
                return None

            print(f"  [最安値検索] 最安: ${best_match['total_price_usd']:.2f} (類似度{best_match['similarity']:.0%}) item={best_match['item_id']}")
            return best_match

        except requests.exceptions.RequestException as e:
            print(f"  [最安値検索] Browse APIエラー: {e}")
            return None

    def create_and_publish_listing(self, request: ListingRequest) -> ListingResult:
        """Create and publish listing (not implemented yet)."""
        raise NotImplementedError("Real eBay API integration not implemented yet.")


class MockEbayClient(EbayClient):
    def search_completed(self, keyword: str, market: str) -> List[ListingCandidate]:
        return [
            ListingCandidate(
                candidate_id=str(uuid.uuid4()),
                search_query=keyword,
                ebay_item_url=f"https://example.com/ebay/{keyword.replace(' ', '-').lower()}",
                ebay_price=45.0,
                ebay_shipping=8.0,
                sold_signal=72,
            )
        ]

    def create_and_publish_listing(self, request: ListingRequest) -> ListingResult:
        return ListingResult(
            listing_id=str(uuid.uuid4()),
            listed_url=f"https://example.com/ebay/listing/{request.candidate_id}",
            error_message="",
        )
