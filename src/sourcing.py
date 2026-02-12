"""Sourcing adapters for Rakuten and Amazon PA-API."""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import os
from typing import Dict, List, Optional

import requests

from .models import ListingCandidate, SourceOffer


class SourcingClient:
    def __init__(self) -> None:
        self.rakuten = RakutenClient(
            application_id=os.getenv("RAKUTEN_APPLICATION_ID"),
            affiliate_id=os.getenv("RAKUTEN_AFFILIATE_ID"),
        )
        self.amazon = AmazonPaapiClient(
            access_key=os.getenv("AMAZON_ACCESS_KEY_ID"),
            secret_key=os.getenv("AMAZON_SECRET_ACCESS_KEY"),
            partner_tag=os.getenv("AMAZON_PARTNER_TAG"),
            marketplace=os.getenv("AMAZON_MARKETPLACE", "JP"),
        )
        self.yahoo = YahooShoppingClient(
            app_id=os.getenv("YAHOO_APP_ID"),
        )
        self.serpapi = SerpApiClient(
            api_key=os.getenv("SERPAPI_API_KEY"),
        )

    # Expose individual clients for direct access
    @property
    def rakuten_client(self):
        return self.rakuten

    @property
    def amazon_client(self):
        return self.amazon

    @property
    def yahoo_client(self):
        return self.yahoo

    @property
    def serpapi_client(self):
        return self.serpapi

    def search_best_offer(self, listing: ListingCandidate) -> Optional[SourceOffer]:
        offers = []
        if self.rakuten.is_enabled:
            offer = self.rakuten.search(listing.search_query)
            if offer:
                offers.append(offer)
        if self.amazon.is_enabled:
            offer = self.amazon.search(listing.search_query)
            if offer:
                offers.append(offer)
        if self.yahoo.is_enabled:
            offer = self.yahoo.search(listing.search_query)
            if offer:
                offers.append(offer)
        if not offers:
            return None
        return min(offers, key=lambda o: o.source_price_jpy + o.source_shipping_jpy)

    def search_multiple_offers(self, listing: ListingCandidate, max_results: int = 3) -> List[SourceOffer]:
        """Search for multiple sourcing offers from all enabled sources, sorted by total price."""
        offers = []

        # Get multiple offers from Rakuten
        if self.rakuten.is_enabled:
            print(f"  [DEBUG] Rakuten検索: 有効")
            rakuten_offers = self.rakuten.search_multiple(listing.search_query, max_results=max_results)
            print(f"  [DEBUG] Rakuten検索結果: {len(rakuten_offers)}件")
            offers.extend(rakuten_offers)
        else:
            print(f"  [DEBUG] Rakuten検索: 無効（APIキー未設定）")

        # Get multiple offers from Amazon
        if self.amazon.is_enabled:
            print(f"  [DEBUG] Amazon検索: 有効")
            amazon_offers = self.amazon.search_multiple(listing.search_query, max_results=max_results)
            print(f"  [DEBUG] Amazon検索結果: {len(amazon_offers)}件")
            offers.extend(amazon_offers)
        else:
            print(f"  [DEBUG] Amazon検索: 無効（APIキー未設定）")

        # Get multiple offers from Yahoo! Shopping
        if self.yahoo.is_enabled:
            print(f"  [DEBUG] Yahoo!ショッピング検索: 有効")
            yahoo_offers = self.yahoo.search_multiple(listing.search_query, max_results=max_results)
            print(f"  [DEBUG] Yahoo!ショッピング検索結果: {len(yahoo_offers)}件")
            offers.extend(yahoo_offers)
        else:
            print(f"  [DEBUG] Yahoo!ショッピング検索: 無効（APIキー未設定）")

        # Sort by total price (price + shipping) and return top N
        if not offers:
            return []

        offers.sort(key=lambda o: o.source_price_jpy + o.source_shipping_jpy)
        return offers[:max_results]

    def search_all_sites(self, keyword: str, max_results: int = 3) -> List[SourceOffer]:
        """Search all sites including SerpApi (Google Shopping) for comprehensive results."""
        offers = []

        # First try SerpApi for comprehensive Google Shopping results
        if self.serpapi.is_enabled:
            print(f"  [DEBUG] SerpApi (全サイト検索): 有効")
            serpapi_offers = self.serpapi.search_google_shopping(keyword, max_results=max_results * 2)
            print(f"  [DEBUG] SerpApi検索結果: {len(serpapi_offers)}件")
            offers.extend(serpapi_offers)

        # Also get direct API results for accuracy
        if self.rakuten.is_enabled:
            rakuten_offers = self.rakuten.search_multiple(keyword, max_results=max_results)
            offers.extend(rakuten_offers)

        if self.amazon.is_enabled:
            amazon_offers = self.amazon.search_multiple(keyword, max_results=max_results)
            offers.extend(amazon_offers)

        if self.yahoo.is_enabled:
            yahoo_offers = self.yahoo.search_multiple(keyword, max_results=max_results)
            offers.extend(yahoo_offers)

        # Sort by total price and return top N
        if not offers:
            return []

        offers.sort(key=lambda o: o.source_price_jpy + o.source_shipping_jpy)
        return offers[:max_results]


class MockSourcingClient(SourcingClient):
    def search_best_offer(self, listing: ListingCandidate) -> Optional[SourceOffer]:
        return SourceOffer(
            source_site="Rakuten",
            source_url="https://example.com/rakuten/item",
            source_price_jpy=2500.0,
            source_shipping_jpy=500.0,
            stock_hint="in_stock",
        )


class RakutenClient:
    def __init__(self, application_id: Optional[str], affiliate_id: Optional[str]) -> None:
        self.application_id = application_id
        self.affiliate_id = affiliate_id
        self.is_enabled = bool(self.application_id)

    def search(self, keyword: str) -> Optional[SourceOffer]:
        if not self.is_enabled:
            return None
        params: Dict[str, str] = {
            "applicationId": self.application_id,
            "keyword": keyword,
            "hits": "5",
            "format": "json",
        }
        if self.affiliate_id:
            params["affiliateId"] = self.affiliate_id
        try:
            resp = requests.get(
                "https://app.rakuten.co.jp/services/api/IchibaItem/Search/20170706",
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException:
            return None

        items = data.get("Items", [])
        if not items:
            return None
        # Pick the cheapest item by itemPrice.
        best = min(items, key=lambda i: i.get("Item", {}).get("itemPrice", 10**12))
        item = best.get("Item", {})
        price = float(item.get("itemPrice", 0))
        url = item.get("itemUrl", "")
        availability = item.get("availability", 0)
        item_name = item.get("itemName", "")
        image_urls = item.get("mediumImageUrls", [])
        image_url = image_urls[0].get("imageUrl", "") if image_urls else ""
        return SourceOffer(
            source_site="Rakuten",
            source_url=url,
            source_price_jpy=price,
            source_shipping_jpy=0.0,
            stock_hint="in_stock" if availability == 1 else "unknown",
            title=item_name,
            source_image_url=image_url,
        )

    def search_multiple(self, keyword: str, max_results: int = 5) -> List[SourceOffer]:
        """Search for multiple offers from Rakuten, sorted by price."""
        if not self.is_enabled:
            return []
        params: Dict[str, str] = {
            "applicationId": self.application_id,
            "keyword": keyword,
            "hits": str(min(max_results, 30)),  # Rakuten API max is 30
            "format": "json",
        }
        if self.affiliate_id:
            params["affiliateId"] = self.affiliate_id
        try:
            resp = requests.get(
                "https://app.rakuten.co.jp/services/api/IchibaItem/Search/20170706",
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException:
            return []

        items = data.get("Items", [])
        if not items:
            return []

        # Convert all items to SourceOffer and sort by price
        offers = []
        for item_wrapper in items:
            item = item_wrapper.get("Item", {})
            price = float(item.get("itemPrice", 0))
            url = item.get("itemUrl", "")
            availability = item.get("availability", 0)
            item_name = item.get("itemName", "")
            # 商品画像URL取得（Gemini画像比較用）
            image_urls = item.get("mediumImageUrls", [])
            image_url = image_urls[0].get("imageUrl", "") if image_urls else ""

            if price > 0 and url:
                offers.append(SourceOffer(
                    source_site="Rakuten",
                    source_url=url,
                    source_price_jpy=price,
                    source_shipping_jpy=0.0,
                    stock_hint="in_stock" if availability == 1 else "unknown",
                    title=item_name,
                    source_image_url=image_url,
                ))

        # Sort by price and return top N
        offers.sort(key=lambda o: o.source_price_jpy)
        return offers[:max_results]


class AmazonPaapiClient:
    def __init__(
        self,
        access_key: Optional[str],
        secret_key: Optional[str],
        partner_tag: Optional[str],
        marketplace: str,
    ) -> None:
        self.access_key = access_key
        self.secret_key = secret_key
        self.partner_tag = partner_tag
        self.marketplace = marketplace
        self.is_enabled = all([self.access_key, self.secret_key, self.partner_tag])

    def _sign(self, key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    def _get_signature_key(self, date_stamp: str, region: str, service: str) -> bytes:
        k_date = self._sign(("AWS4" + self.secret_key).encode("utf-8"), date_stamp)
        k_region = self._sign(k_date, region)
        k_service = self._sign(k_region, service)
        return self._sign(k_service, "aws4_request")

    def _build_headers(self, payload: str, host: str) -> Dict[str, str]:
        region = "us-west-2"
        service = "ProductAdvertisingAPI"
        t = datetime.datetime.utcnow()
        amz_date = t.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = t.strftime("%Y%m%d")

        canonical_uri = "/paapi5/searchitems"
        canonical_querystring = ""
        canonical_headers = (
            f"content-encoding:amz-1.0\n"
            f"content-type:application/json; charset=utf-8\n"
            f"host:{host}\n"
            f"x-amz-date:{amz_date}\n"
            "x-amz-target:com.amazon.paapi5.v1.ProductAdvertisingAPIv1.SearchItems\n"
        )
        signed_headers = "content-encoding;content-type;host;x-amz-date;x-amz-target"
        payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        canonical_request = "\n".join(
            [
                "POST",
                canonical_uri,
                canonical_querystring,
                canonical_headers,
                signed_headers,
                payload_hash,
            ]
        )

        algorithm = "AWS4-HMAC-SHA256"
        credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
        string_to_sign = "\n".join(
            [
                algorithm,
                amz_date,
                credential_scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )
        signing_key = self._get_signature_key(date_stamp, region, service)
        signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

        authorization_header = (
            f"{algorithm} Credential={self.access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )

        return {
            "content-encoding": "amz-1.0",
            "content-type": "application/json; charset=utf-8",
            "host": host,
            "x-amz-date": amz_date,
            "x-amz-target": "com.amazon.paapi5.v1.ProductAdvertisingAPIv1.SearchItems",
            "Authorization": authorization_header,
        }

    def search(self, keyword: str) -> Optional[SourceOffer]:
        if not self.is_enabled:
            return None
        host = "webservices.amazon.co.jp"
        endpoint = f"https://{host}/paapi5/searchitems"
        payload = {
            "Keywords": keyword,
            "PartnerTag": self.partner_tag,
            "PartnerType": "Associates",
            "Marketplace": "www.amazon.co.jp",
            "Resources": [
                "ItemInfo.Title",
                "Offers.Listings.Price",
            ],
        }
        payload_json = json.dumps(payload)
        headers = self._build_headers(payload_json, host)
        try:
            resp = requests.post(endpoint, data=payload_json, headers=headers, timeout=20)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException:
            return None

        items = data.get("SearchResult", {}).get("Items", [])
        if not items:
            return None
        item = items[0]
        detail_url = item.get("DetailPageURL", "")
        item_title = item.get("ItemInfo", {}).get("Title", {}).get("DisplayValue", "")
        price_info = (
            item.get("Offers", {})
            .get("Listings", [{}])[0]
            .get("Price", {})
        )
        amount = price_info.get("Amount")
        if amount is None:
            return None
        return SourceOffer(
            source_site="AmazonJP",
            source_url=detail_url,
            source_price_jpy=float(amount),
            source_shipping_jpy=0.0,
            stock_hint="unknown",
            title=item_title,
        )

    def search_multiple(self, keyword: str, max_results: int = 5) -> List[SourceOffer]:
        """Search for multiple offers from Amazon, sorted by price."""
        if not self.is_enabled:
            return []  # Silent - Amazon not configured

        host = "webservices.amazon.co.jp"
        endpoint = f"https://{host}/paapi5/searchitems"
        payload = {
            "Keywords": keyword,
            "PartnerTag": self.partner_tag,
            "PartnerType": "Associates",
            "Marketplace": "www.amazon.co.jp",
            "ItemCount": min(max_results, 10),  # Amazon PA-API max is 10
            "Resources": [
                "ItemInfo.Title",
                "Offers.Listings.Price",
            ],
        }
        payload_json = json.dumps(payload)
        headers = self._build_headers(payload_json, host)

        try:
            resp = requests.post(endpoint, data=payload_json, headers=headers, timeout=20)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException:
            return []  # Silent failure

        items = data.get("SearchResult", {}).get("Items", [])

        if not items:
            return []

        # Convert all items to SourceOffer
        offers = []
        for item in items:
            detail_url = item.get("DetailPageURL", "")
            item_title = item.get("ItemInfo", {}).get("Title", {}).get("DisplayValue", "")
            price_info = (
                item.get("Offers", {})
                .get("Listings", [{}])[0]
                .get("Price", {})
            )
            amount = price_info.get("Amount")

            if amount is not None and detail_url:
                offers.append(SourceOffer(
                    source_site="AmazonJP",
                    source_url=detail_url,
                    source_price_jpy=float(amount),
                    source_shipping_jpy=0.0,
                    stock_hint="unknown",
                    title=item_title,
                ))

        # Sort by price and return top N
        offers.sort(key=lambda o: o.source_price_jpy)
        return offers[:max_results]


class YahooShoppingClient:
    """Yahoo! Shopping Web Service API client."""

    def __init__(self, app_id: Optional[str]) -> None:
        self.app_id = app_id
        self.is_enabled = bool(self.app_id)

    def search(self, keyword: str) -> Optional[SourceOffer]:
        """Search for the cheapest item on Yahoo! Shopping."""
        if not self.is_enabled:
            return None

        params = {
            "appid": self.app_id,
            "query": keyword,
            "results": "5",
            "sort": "+price",  # Sort by price ascending
        }

        try:
            resp = requests.get(
                "https://shopping.yahooapis.jp/ShoppingWebService/V3/itemSearch",
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            print(f"  [Yahoo] Error: {e}")
            return None

        hits = data.get("hits", [])
        if not hits:
            return None

        # Get the cheapest item
        item = hits[0]
        price = float(item.get("price", 0))
        url = item.get("url", "")

        if price <= 0 or not url:
            return None

        return SourceOffer(
            source_site="Yahoo",
            source_url=url,
            source_price_jpy=price,
            source_shipping_jpy=0.0,
            stock_hint="unknown",
        )

    def search_multiple(self, keyword: str, max_results: int = 5) -> List[SourceOffer]:
        """Search for multiple items on Yahoo! Shopping."""
        if not self.is_enabled:
            return []

        params = {
            "appid": self.app_id,
            "query": keyword,
            "results": str(min(max_results, 50)),  # Yahoo API max is 50
            "sort": "+price",  # Sort by price ascending
        }

        try:
            resp = requests.get(
                "https://shopping.yahooapis.jp/ShoppingWebService/V3/itemSearch",
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            print(f"  [Yahoo] Error: {e}")
            return []

        hits = data.get("hits", [])
        if not hits:
            return []

        offers = []
        for item in hits:
            price = float(item.get("price", 0))
            url = item.get("url", "")

            if price > 0 and url:
                offers.append(SourceOffer(
                    source_site="Yahoo",
                    source_url=url,
                    source_price_jpy=price,
                    source_shipping_jpy=0.0,
                    stock_hint="unknown",
                ))

        return offers[:max_results]


class SerpApiClient:
    """
    SerpApi client for Google Shopping search (all sites).

    SerpApi pricing (as of 2024):
    - Free: 100 searches/month
    - Developer: $50/month - 5,000 searches
    - Business: $130/month - 15,000 searches

    This enables searching across ALL shopping sites at once via Google Shopping.
    """

    def __init__(self, api_key: Optional[str]) -> None:
        self.api_key = api_key
        self.is_enabled = bool(self.api_key)

    def search_google_shopping(self, keyword: str, max_results: int = 10) -> List[SourceOffer]:
        """
        Search Google Shopping via SerpApi.
        This returns results from multiple shopping sites including:
        - Amazon, Rakuten, Yahoo Shopping
        - Yodobashi, Bic Camera, etc.
        - Various other Japanese e-commerce sites
        """
        if not self.is_enabled:
            return []

        params = {
            "api_key": self.api_key,
            "engine": "google_shopping",
            "q": keyword,
            "location": "Japan",
            "hl": "ja",
            "gl": "jp",
            "num": str(max_results),
        }

        try:
            resp = requests.get(
                "https://serpapi.com/search",
                params=params,
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            print(f"  [SerpApi] Error: {e}")
            return []

        shopping_results = data.get("shopping_results", [])
        if not shopping_results:
            return []

        offers = []
        for item in shopping_results:
            # Extract price (format: "1,234円" or "$12.34")
            price_str = item.get("extracted_price", 0)
            if isinstance(price_str, str):
                # Remove currency symbols and commas
                price_str = price_str.replace("¥", "").replace("円", "").replace(",", "").strip()
                try:
                    price = float(price_str)
                except ValueError:
                    continue
            else:
                price = float(price_str) if price_str else 0

            link = item.get("link", "")
            source = item.get("source", "Unknown")

            if price > 0 and link:
                offers.append(SourceOffer(
                    source_site=source,
                    source_url=link,
                    source_price_jpy=price,
                    source_shipping_jpy=0.0,
                    stock_hint="unknown",
                ))

        # Sort by price
        offers.sort(key=lambda o: o.source_price_jpy)
        return offers[:max_results]
