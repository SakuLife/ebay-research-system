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


# ---------------------------------------------------------------------------
# 同一商品判定のためのタイトル解析ヘルパー
#
# SequenceMatcher は長い商品タイトル同士の比較に弱く（語順違い・出品者独自の
# 前置き・全角半角ゆれで一気に落ちる）、同一商品を別商品と判定して取りこぼす
# 主因になっていた。文字bigramのDice係数＋型番トークン一致に置き換える。
# ---------------------------------------------------------------------------

# 商品の同一性に関係しない出品者側の常套句。比較前に落とす
_TITLE_NOISE_WORDS = {
    "new", "used", "brand", "free", "shipping", "ship", "shipped", "from",
    "japan", "japanese", "authentic", "genuine", "original", "official",
    "sealed", "rare", "vintage", "limited", "edition", "oem", "nib", "nwt",
    "nwb", "fs", "mint", "box", "boxed", "with", "and", "the", "for", "in",
    "excellent", "condition", "good", "very", "near", "unused", "tracking",
    "number", "fast", "import", "us", "seller", "lot", "set", "of",
}

# 本体ではなく付属品・部品・説明書だけの出品を示す語。
# これらを候補から外さないと、価格が安いため上位に並び、
# 同一商品判定（Gemini画像比較）の回数を食い潰して本命に到達できない
_ACCESSORY_MARKERS = (
    "manual only", "instruction manual", "instructions only", "owners manual",
    "user manual", "manual booklet", "box only", "empty box", "case only",
    "bag only", "strap only", "cable only", "adapter only", "battery only",
    "lid only", "part only", "parts only", "for parts", "not working",
    "repair", "junk", "screen protector", "replacement part",
    "取扱説明書", "説明書のみ", "箱のみ", "ジャンク", "部品取り",
)


def _normalize_title(title: str) -> str:
    """比較用にタイトルを正規化する（小文字化・記号を空白化・空白圧縮）."""
    lowered = title.lower()
    cleaned = re.sub(r"[^0-9a-z぀-ヿ一-鿿]+", " ", lowered)
    return " ".join(cleaned.split())


def _content_tokens(title: str) -> set:
    """商品の同一性に効くトークンだけ残す（ノイズ語と1文字を除去）."""
    return {
        t for t in _normalize_title(title).split()
        if t not in _TITLE_NOISE_WORDS and len(t) > 1
    }


# 寸法・規格・容量を表す接尾辞。型番ではないので除外する。
# 例: 52mm・210mm・70300mm（レンズ径やサイズ）を型番扱いすると、
# 無関係な商品どうしが「型番一致」になって候補の優先順位が壊れる
_MEASUREMENT_SUFFIXES = (
    "mm", "cm", "inch", "in", "ft", "ml", "oz", "kg", "lb", "hz", "khz",
    "mah", "gb", "tb", "mb", "cc", "mp", "fps", "rpm", "wh", "vdc",
)


def _model_tokens(title: str) -> set:
    """型番らしいトークンを抜き出す.

    英字と数字が混在する4文字以上のトークン（NS-TSC10 → nstsc10、F-91W → f91w）に加えて、
    角括弧やシャープ付きの品番（[56809]・#241）も拾う。
    型番が一致すれば同一商品である可能性が非常に高く、
    タイトルの書き方が全く違っていても拾える強い手がかりになる。
    """
    tokens = set()
    for raw in re.findall(r"[0-9a-zA-Z][0-9a-zA-Z\-/]{2,}", title.lower()):
        flat = raw.replace("-", "").replace("/", "")
        if len(flat) < 4:
            continue
        if not (any(c.isdigit() for c in flat) and any(c.isalpha() for c in flat)):
            continue
        # 数字＋単位だけのもの（52mm, 210mm）は寸法であって型番ではない
        stripped = flat.rstrip("0123456789")
        if not stripped and flat.isdigit():
            continue
        unit_part = flat.lstrip("0123456789")
        if unit_part in _MEASUREMENT_SUFFIXES:
            continue
        tokens.add(flat)

    # メーカー品番の慣用表記: [56809] / #241 / No.241
    # プラモデル・フィギュア系はこの数字が唯一の識別子になることが多い
    for code in re.findall(r"(?:\[|#|no\.?\s*)(\d{3,6})\b", title.lower()):
        tokens.add(code)

    return tokens


def models_match(models_a: set, models_b: set) -> bool:
    """型番集合どうしが同一商品を指すか判定する.

    完全一致に加えて、末尾2文字以内の差（バリアント記号）は同一とみなす。
    出品者は同じ商品を F-91W とも F91W-1 とも書くため、完全一致だけだと取りこぼす。
    ⚠️ NS-TSC10 と NS-TSC100 のような別型番も拾いうるが、
    型番一致は「候補に残す」判断にしか使わず、採否はGemini画像比較で決めるため許容する。
    """
    if not models_a or not models_b:
        return False
    for a in models_a:
        for b in models_b:
            if a == b:
                return True
            longer, shorter = (a, b) if len(a) >= len(b) else (b, a)
            if len(longer) - len(shorter) <= 2 and longer.startswith(shorter):
                return True
    return False


def _char_bigrams(text: str) -> set:
    """文字bigram集合（空白除去後）."""
    flat = text.replace(" ", "")
    return {flat[i:i + 2] for i in range(len(flat) - 1)}


def _dice(a: set, b: set) -> float:
    """Dice係数. 集合が空なら0."""
    if not a or not b:
        return 0.0
    return 2 * len(a & b) / (len(a) + len(b))


def title_similarity(title_a: str, title_b: str) -> float:
    """商品タイトルの類似度（0.0〜1.0）.

    文字bigramのDice係数と、内容語トークンのDice係数の大きい方を採る。
    語順が違うだけの同一商品を落とさないため、トークン側も見る。
    """
    norm_a, norm_b = _normalize_title(title_a), _normalize_title(title_b)
    if not norm_a or not norm_b:
        return 0.0
    char_score = _dice(_char_bigrams(norm_a), _char_bigrams(norm_b))
    token_score = _dice(_content_tokens(title_a), _content_tokens(title_b))
    return max(char_score, token_score)


def is_accessory_listing(title: str) -> bool:
    """本体ではなく付属品・部品・説明書のみの出品か判定する."""
    lowered = title.lower()
    return any(marker in lowered for marker in _ACCESSORY_MARKERS)


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
        exclude_item_id: str = "",
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
            exclude_item_id: 除外する商品ID（分析中の出品自身の自己マッチ防止）

        Returns:
            Dict with cheapest active listing details, or None if not found
        """
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

        url = f"{self.browse_url}/item_summary/search"

        # Gemini画像比較が利用可能か判定
        use_gemini_image = (
            gemini_client is not None
            and ebay_image_url
            and hasattr(gemini_client, 'compare_product_images')
        )

        # Geminiで商品特定に最適な短縮クエリも事前生成（毎回現行クエリと併用）
        gemini_short_query = ""
        if gemini_client and hasattr(gemini_client, 'model') and getattr(gemini_client, 'model', None):
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

        def _do_search(q: str, with_location: bool) -> list[dict]:
            fp = list(filter_parts)
            if not with_location:
                # itemLocationCountry除外（フォールバック用）
                fp = [x for x in fp if not x.startswith("itemLocationCountry:")]
            params = {
                "q": q,
                "sort": "price",
                "limit": 200,
                "filter": ",".join(fp),
            }
            try:
                resp = requests.get(url, headers=headers, params=params, timeout=15)
                if resp.status_code != 200:
                    print(f"  [最安値検索] Browse API error: HTTP {resp.status_code} (q={q[:40]!r})")
                    return []
                return resp.json().get("itemSummaries", [])
            except requests.exceptions.RequestException as e:
                print(f"  [最安値検索] Browse APIエラー: {e}")
                return []

        # 型番クエリ: 型番が一致する出品はタイトルの書き方が全く違っても同一商品なので、
        # 型番だけで引くと現行クエリ・Geminiクエリの両方が取りこぼす候補を拾える
        source_models = _model_tokens(ebay_title)
        model_query = ""
        if source_models:
            # 元タイトル中の表記のまま（ハイフン込み）で引く方がヒットしやすい
            raw_models = re.findall(r"[0-9a-zA-Z][0-9a-zA-Z\-/]{2,}", ebay_title)
            for raw in raw_models:
                if raw.replace("-", "").replace("/", "").lower() in source_models:
                    model_query = raw
                    break

        seen_ids: set[str] = set()
        items: list[dict] = []

        def _collect(sources: list[list[dict]]) -> None:
            for src in sources:
                for it in src:
                    iid = it.get("itemId", "")
                    if iid and iid not in seen_ids:
                        seen_ids.add(iid)
                        items.append(it)

        # 現行クエリ・Geminiクエリ・型番クエリの三段検索 → プール統合
        items_curr = _do_search(search_query, with_location=True)
        items_gem = _do_search(gemini_short_query, with_location=True) if gemini_short_query else []
        items_model = _do_search(model_query, with_location=True) if model_query else []
        _collect([items_curr, items_gem, items_model])

        # 所在地フォールバック: 日本所在地の候補が乏しいときは全所在地でも引く。
        # 0件のときだけ広げていたため、無関係な数件が混じると広げられず取りこぼしていた
        MIN_POOL_BEFORE_WIDENING = 25
        used_location_fallback = False
        if len(items) < MIN_POOL_BEFORE_WIDENING and "itemLocationCountry:JP" in ",".join(filter_parts):
            print(f"  [最安値検索] 日本所在地の候補{len(items)}件 → 全所在地でも検索（フォールバック）")
            used_location_fallback = True
            _collect([
                _do_search(search_query, with_location=False),
                _do_search(gemini_short_query, with_location=False) if gemini_short_query else [],
                _do_search(model_query, with_location=False) if model_query else [],
            ])

        if not items:
            print(f"  [最安値検索] アクティブリスティングなし")
            return None

        print(
            f"  [最安値検索] {len(items)}件の候補 "
            f"(現行Q:{len(items_curr)} GeminiQ:{len(items_gem)} 型番Q:{len(items_model)}"
            f" 画像比較:{'ON' if use_gemini_image else 'OFF'}"
            f"{' 所在地フォールバック' if used_location_fallback else ''})"
        )

        # USD変換レート（マーケット間比較用の概算。同一マーケット内では相対順位に影響しない）
        usd_rates = {"GBP": 1.27, "EUR": 1.09, "USD": 1.0, "AUD": 0.66, "CAD": 0.73}

        def _to_usd(value: float, currency: str) -> float:
            return value * usd_rates.get(currency, 1.0)

        # 類似度ゲート
        # 90%以上かつ価格差が穏やか(20%以内) → タイトル一致で即採用（コスト削減）
        # それ以外 → 安い順にGemini画像比較で同一商品判定（上位N件まで）
        # 20%未満 → スキップ
        # Geminiなし時は SIMILARITY_FALLBACK_ACCEPT 以上で採用
        # しきい値は文字bigram Dice基準（旧SequenceMatcher基準より素直に上がる）
        SIMILARITY_AUTO_ACCEPT = 0.85
        SIMILARITY_FALLBACK_ACCEPT = 0.45  # Geminiなし時の足切り
        SIMILARITY_IMAGE_CHECK = 0.20
        # 型番が一致していれば書き方が違っても同一商品の可能性が高いので、足切りを下げる
        SIMILARITY_IMAGE_CHECK_WITH_MODEL = 0.10
        SIMILARITY_FALLBACK_ACCEPT_WITH_MODEL = 0.35
        # 価格が現価格(sold_price_usd)の70%未満（=30%以上安い）なら、別バリアント疑いで必ず画像確認
        PRICE_GAP_VERIFY_RATIO = 0.70
        MAX_GEMINI_CHECKS = 20  # 画像比較の上限（APIコスト制御）
        # 自己マッチ防止用: Browse APIのitemIdは "v1|123456|0" 形式、除外IDは数値部分
        exclude_id = exclude_item_id.strip()

        # 各候補を「最安総額(本体+最安送料) + 採用時レコード」へ変換し、安い順にソート
        # 送料はオプションのうち最安を採用し、各々の通貨でUSD換算する（本体通貨流用の誤換算を防ぐ）
        candidates: list[dict] = []
        skipped_self = 0
        skipped_accessory = 0
        for item in items:
            item_id = item.get("itemId", "")
            if exclude_id and exclude_id in item_id:
                skipped_self += 1
                continue

            title = item.get("title", "")

            # 説明書のみ・箱のみ・部品取り等は本体ではないので候補から外す。
            # 安価なため最安側に並び、画像比較の回数を食い潰して本命に届かなくなる
            if is_accessory_listing(title):
                skipped_accessory += 1
                continue

            sim = title_similarity(ebay_title, title)
            # 型番一致は「書き方が違うだけの同一商品」を示す強い手がかり
            model_match = models_match(source_models, _model_tokens(title))
            gate = SIMILARITY_IMAGE_CHECK_WITH_MODEL if model_match else SIMILARITY_IMAGE_CHECK
            if sim < gate:
                continue

            price_info = item.get("price", {})
            price_local = float(price_info.get("value", 0) or 0)
            currency = price_info.get("currency", "USD")
            price_usd = _to_usd(price_local, currency)
            if price_usd <= 0:
                continue

            # 送料: 複数オプションのうち最安をUSDで採用（無料/未掲載は0扱い）
            ship_costs = []
            for opt in item.get("shippingOptions", []) or []:
                ci = opt.get("shippingCost", {}) or {}
                val = ci.get("value")
                if val is not None:
                    ship_costs.append(_to_usd(float(val), ci.get("currency", currency)))
            ship_usd = min(ship_costs) if ship_costs else 0.0
            total_usd = price_usd + ship_usd

            cand_img = item.get("image", {}).get("imageUrl", "")
            if not cand_img:
                thumbs = item.get("thumbnailImages", [])
                cand_img = thumbs[0].get("imageUrl", "") if thumbs else ""

            candidates.append({
                "total_usd": total_usd,
                "sim": sim,
                "model_match": model_match,
                "image": cand_img,
                "title": title,
                "result": {
                    "item_id": item_id,
                    "title": title,
                    "url": item.get("itemWebUrl", ""),
                    "price": price_usd,
                    "price_local": price_local,
                    "currency": currency,
                    "shipping": ship_usd,
                    "total_price_usd": total_usd,
                    "similarity": sim,
                },
            })

        # 走査順の決め方（ここを間違えると打率が落ちる）
        #
        # 単純な「安い順」だと、型番クエリで広がった候補プールの中の
        # 無関係な安物（互換アクセサリ・別バリアント）が先頭に並び、
        # Gemini画像比較の回数上限を食い潰して本命に到達できない。
        # 実測でも、プールを広げただけの版は打率が 63%→53% に低下した。
        #
        # そこで「もっともらしさ上位K件」に絞ってから、その中を安い順に見る。
        # 絞り込みで精度を、K件内の安い順で「最安を採る」目的を両立させる。
        PLAUSIBLE_POOL_SIZE = 40

        def _plausibility(c: dict) -> float:
            # 型番一致は強い手がかりなので加点する
            return c["sim"] + (0.25 if c["model_match"] else 0.0)

        candidates.sort(key=_plausibility, reverse=True)
        pruned = len(candidates) - PLAUSIBLE_POOL_SIZE
        candidates = candidates[:PLAUSIBLE_POOL_SIZE]
        candidates.sort(key=lambda c: c["total_usd"])  # 絞った中で安い順
        if pruned > 0:
            print(f"  [最安値検索] もっともらしさ上位{PLAUSIBLE_POOL_SIZE}件に絞り込み（{pruned}件を除外）")

        best_match: Optional[Dict[str, Any]] = None
        gemini_checked = 0
        gemini_matched = 0
        adopted_without_gemini = 0

        # 価格差判定の基準: sold_price_usd（呼び出し元の現価格）
        # 0以下なら無効（全件Gemini確認の方向に倒す）
        ref_price = sold_price_usd if sold_price_usd > 0 else 0.0

        # 安い順に走査し、最初に「同一商品」と判定されたものを採用
        for cand in candidates:
            total_usd = cand["total_usd"]
            sim = cand["sim"]
            cand_img = cand["image"]
            cand_title = cand["title"]

            # 価格差が大きい候補は別バリアントの疑い → 類似度が高くても画像確認を強制
            price_gap_large = ref_price > 0 and total_usd < ref_price * PRICE_GAP_VERIFY_RATIO

            # ケース1: 類似度が極めて高い & 価格差が穏やか → 画像確認なしで即採用
            if sim >= SIMILARITY_AUTO_ACCEPT and not price_gap_large:
                best_match = cand["result"]
                adopted_without_gemini = 1
                break

            # ケース2: Gemini画像比較が使えない → タイトル類似度フォールバック
            if not use_gemini_image:
                # 価格差が大きい候補は安全側に倒してスキップ（型番一致なら別バリアントの
                # 疑いが薄いので、そのまま類似度で判断する）
                if price_gap_large and not cand["model_match"]:
                    continue
                accept = (
                    SIMILARITY_FALLBACK_ACCEPT_WITH_MODEL if cand["model_match"]
                    else SIMILARITY_FALLBACK_ACCEPT
                )
                if sim >= accept:
                    best_match = cand["result"]
                    adopted_without_gemini = 1
                    break
                continue

            # ケース3: Gemini画像比較で確認
            if not cand_img:
                continue
            if gemini_checked >= MAX_GEMINI_CHECKS:
                print(f"  [最安値検索] 画像比較上限{MAX_GEMINI_CHECKS}件に到達 → 中断")
                break
            gemini_checked += 1
            try:
                is_match = gemini_client.compare_product_images(
                    ebay_image_url=ebay_image_url,
                    source_image_url=cand_img,
                    ebay_title=ebay_title,
                    source_title=cand_title,
                )
            except Exception as e:
                print(f"    [Gemini] 画像比較エラー: {e}")
                continue
            if is_match is not True:
                continue
            gemini_matched += 1
            print(f"    [Gemini] 画像MATCH: ${total_usd:.2f} 類似度{sim:.0%} '{cand_title[:50]}'")
            best_match = cand["result"]
            break  # 安い順に走査しているので最初のMATCHが最安

        if gemini_checked > 0:
            print(f"  [最安値検索] Gemini画像比較: {gemini_checked}件チェック → {gemini_matched}件MATCH")
        if adopted_without_gemini:
            print(f"  [最安値検索] 高類似度({SIMILARITY_AUTO_ACCEPT:.0%}以上)のため画像比較スキップ")

        if skipped_accessory:
            print(f"  [最安値検索] 付属品・部品のみの出品を除外: {skipped_accessory}件")

        if not best_match:
            print(f"  [最安値検索] 同一商品のアクティブリスティングなし（候補{len(candidates)}件中マッチなし）")
            return None

        print(f"  [最安値検索] 最安: ${best_match['total_price_usd']:.2f} (類似度{best_match['similarity']:.0%}) item={best_match['item_id']}")
        return best_match

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
