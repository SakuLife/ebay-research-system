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

        # Sandbox or Production endpoints
        if self.use_sandbox:
            self.oauth_url = "https://api.sandbox.ebay.com/identity/v1/oauth2/token"
            self.browse_url = "https://api.sandbox.ebay.com/buy/browse/v1"
            self.finding_url = "https://svcs.sandbox.ebay.com/services/search/FindingService/v1"
        else:
            self.oauth_url = "https://api.ebay.com/identity/v1/oauth2/token"
            self.browse_url = "https://api.ebay.com/buy/browse/v1"
            self.finding_url = "https://svcs.ebay.com/services/search/FindingService/v1"

        self._access_token: Optional[str] = None

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

    def search_completed(self, keyword: str, market: str = "UK") -> List[ListingCandidate]:
        """
        Search completed (sold) listings using eBay Finding API.

        Args:
            keyword: Search keyword
            market: Market (UK, US, EU) - default UK

        Returns:
            List of ListingCandidate with sold items
        """
        from datetime import datetime, timedelta

        # Market to Global ID mapping
        global_id_map = {
            "UK": "EBAY-GB",
            "US": "EBAY-US",
            "EU": "EBAY-DE"  # Germany represents EU
        }
        global_id = global_id_map.get(market, "EBAY-GB")

        # Calculate 90 days ago
        days_ago_90 = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

        # Build Finding API request parameters
        params = {
            "OPERATION-NAME": "findCompletedItems",
            "SERVICE-VERSION": "1.0.0",
            "SECURITY-APPNAME": self.client_id,
            "RESPONSE-DATA-FORMAT": "JSON",
            "GLOBAL-ID": global_id,
            "keywords": keyword,
            "paginationInput.entriesPerPage": "100",
            # Filter 0: Sold items only
            "itemFilter(0).name": "SoldItemsOnly",
            "itemFilter(0).value": "true",
            # Filter 1: Condition (New or Used)
            "itemFilter(1).name": "Condition",
            "itemFilter(1).value(0)": "New",
            "itemFilter(1).value(1)": "Used",
            # Filter 2: Last 90 days
            "itemFilter(2).name": "EndTimeFrom",
            "itemFilter(2).value": days_ago_90,
        }

        try:
            response = requests.get(self.finding_url, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()

            # Parse Finding API response
            search_result = data.get("findCompletedItemsResponse", [{}])[0]
            search_result_items = search_result.get("searchResult", [{}])[0]
            items = search_result_items.get("item", [])

            if not items:
                print(f"  [INFO] No sold items found for keyword: {keyword}")
                return []

            candidates = []
            for item in items[:20]:  # Limit to top 20 sold items
                try:
                    item_id = item.get("itemId", [""])[0]
                    title = item.get("title", [""])[0]
                    view_url = item.get("viewItemURL", [""])[0]

                    # Get selling price
                    selling_status = item.get("sellingStatus", [{}])[0]
                    current_price = selling_status.get("currentPrice", [{}])[0]
                    price = float(current_price.get("__value__", 0))

                    # Get shipping cost
                    shipping_info = item.get("shippingInfo", [{}])[0]
                    shipping_cost_data = shipping_info.get("shippingServiceCost", [{}])[0]
                    shipping_cost = float(shipping_cost_data.get("__value__", 0))

                    # Sold signal: use quantity sold if available, otherwise 1
                    quantity_sold = int(selling_status.get("quantitySold", [1])[0])

                    candidate = ListingCandidate(
                        candidate_id=str(uuid.uuid4()),
                        search_query=keyword,
                        ebay_item_url=view_url,
                        ebay_price=price,
                        ebay_shipping=shipping_cost,
                        sold_signal=quantity_sold,
                    )
                    candidates.append(candidate)

                except (KeyError, ValueError, IndexError) as e:
                    print(f"  [WARN] Failed to parse item: {e}")
                    continue

            print(f"  [INFO] Found {len(candidates)} sold items for '{keyword}'")
            return candidates

        except requests.exceptions.RequestException as e:
            print(f"  [ERROR] Finding API error: {e}")
            return []

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
