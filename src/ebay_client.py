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
        else:
            self.oauth_url = "https://api.ebay.com/identity/v1/oauth2/token"
            self.browse_url = "https://api.ebay.com/buy/browse/v1"

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

    def search_completed(self, keyword: str, market: str) -> List[ListingCandidate]:
        """Search completed listings (not implemented yet)."""
        raise NotImplementedError("Search completed listings not implemented yet.")

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
