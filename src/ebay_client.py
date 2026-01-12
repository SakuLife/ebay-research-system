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

    def search_active_listings(self, keyword: str, market: str = "UK", min_price_usd: float = 0) -> List[ListingCandidate]:
        """
        Search active listings using eBay Browse API.

        Note: Finding API (findCompletedItems) has been deprecated.
        This method uses Browse API to search active listings instead.

        Args:
            keyword: Search keyword
            market: Market (UK, US, EU) - default UK

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
        # Sort: Price ascending (lowest first)
        # Note: Browse API doesn't have "price + shipping" sort option
        filter_parts = ["buyingOptions:{FIXED_PRICE}", "conditionIds:{1000}"]

        # Currency conversion rates (approximate USD to local currency)
        currency_info = {
            "UK": {"rate": 0.79, "currency": "GBP"},  # USD to GBP
            "US": {"rate": 1.0, "currency": "USD"},   # USD to USD
            "EU": {"rate": 0.92, "currency": "EUR"},  # USD to EUR
        }

        # Add minimum price filter if specified (convert to local currency)
        if min_price_usd > 0:
            info = currency_info.get(market, {"rate": 1.0, "currency": "USD"})
            local_price = int(min_price_usd * info["rate"])
            # IMPORTANT: priceCurrency is required for price filter to work correctly
            filter_parts.append(f"priceCurrency:{info['currency']}")
            filter_parts.append(f"price:[{local_price}..]")
            print(f"  [INFO] Price filter: ${min_price_usd}+ ({info['currency']} {local_price}+)")

        params = {
            "q": keyword,
            "sort": "price",  # Sort by price ascending (lowest first)
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

                    candidate = ListingCandidate(
                        candidate_id=str(uuid.uuid4()),
                        search_query=keyword,
                        ebay_item_url=view_url,
                        ebay_price=price,
                        ebay_shipping=shipping_cost,
                        sold_signal=sold_signal,
                        category_id=category_id,
                        category_name=category_name,
                    )
                    candidates.append(candidate)

                    # Stop after finding 20 valid items
                    if len(candidates) >= 20:
                        break

                except (KeyError, ValueError, TypeError) as e:
                    print(f"  [WARN] Failed to parse item: {e}")
                    continue

            print(f"  [INFO] Found {len(candidates)} active listings for '{keyword}'")
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

    def find_current_lowest_price(self, keyword: str, market: str = "UK") -> Optional[Dict[str, Any]]:
        """
        Find current lowest price listing for a product.

        Args:
            keyword: Product search keyword
            market: Market (UK, US, EU)

        Returns:
            Dict with item details or None if not found
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

        # Search for current active listings
        params = {
            "q": keyword,
            "sort": "price",  # Sort by lowest price first
            "limit": 10
        }

        url = f"{self.browse_url}/item_summary/search"

        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            items = data.get("itemSummaries", [])
            if not items:
                print(f"  [INFO] No current listings found for: {keyword}")
                return None

            # Return the first item (lowest price)
            lowest_item = items[0]
            return {
                "item_id": lowest_item.get("itemId"),
                "title": lowest_item.get("title"),
                "url": lowest_item.get("itemWebUrl"),
                "price": float(lowest_item.get("price", {}).get("value", 0)),
                "currency": lowest_item.get("price", {}).get("currency", "USD"),
                "shipping": 0  # Browse API may not include shipping in summary
            }

        except requests.exceptions.RequestException as e:
            print(f"  [ERROR] Browse API search error: {e}")
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
