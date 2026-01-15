"""Price Scraper - Scrape actual prices from EC site pages when API returns 0."""

import re
import requests
from typing import Optional
from dataclasses import dataclass


@dataclass
class ScrapedPrice:
    """スクレイピングで取得した価格情報."""
    price: float
    currency: str = "JPY"
    source: str = ""
    success: bool = False
    error_message: str = ""


class PriceScraper:
    """ECサイトから価格をスクレイピングで取得するクライアント."""

    # リクエストタイムアウト（秒）- 楽天は遅いことがあるので延長
    REQUEST_TIMEOUT = 15

    # User-Agent（ブロック回避用）
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "ja,en-US;q=0.7,en;q=0.3",
    }

    def __init__(self):
        """初期化."""
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)

    def scrape_price(self, url: str) -> ScrapedPrice:
        """
        URLから価格をスクレイピングで取得する.

        Args:
            url: 商品ページURL

        Returns:
            ScrapedPrice: スクレイピング結果
        """
        if not url:
            return ScrapedPrice(price=0, success=False, error_message="URL is empty")

        url_lower = url.lower()

        # サイトに応じたスクレイパーを選択
        if "rakuten.co.jp" in url_lower:
            return self._scrape_rakuten(url)
        elif "amazon.co.jp" in url_lower:
            return self._scrape_amazon(url)
        elif "shopping.yahoo.co.jp" in url_lower:
            return self._scrape_yahoo(url)
        else:
            return ScrapedPrice(price=0, success=False, error_message=f"Unsupported site: {url[:50]}")

    def _scrape_rakuten(self, url: str) -> ScrapedPrice:
        """
        楽天市場の商品ページから価格を取得する.

        楽天の価格表示パターン:
        - <span class="price2">17,900円</span>
        - <span class="price--OX_YW">17,900円</span>
        - data-price="17900"
        - "price":17900
        """
        try:
            print(f"    [Scrape] Fetching Rakuten page...")
            response = self.session.get(url, timeout=self.REQUEST_TIMEOUT)
            response.raise_for_status()
            html = response.text

            price = self._extract_rakuten_price(html)

            if price > 0:
                print(f"    [Scrape] Rakuten price found: JPY {price:,.0f}")
                return ScrapedPrice(price=price, currency="JPY", source="楽天", success=True)
            else:
                return ScrapedPrice(price=0, success=False, error_message="Price not found in HTML")

        except requests.exceptions.Timeout:
            return ScrapedPrice(price=0, success=False, error_message="Request timeout")
        except requests.exceptions.RequestException as e:
            return ScrapedPrice(price=0, success=False, error_message=f"Request failed: {str(e)[:50]}")
        except Exception as e:
            return ScrapedPrice(price=0, success=False, error_message=f"Scrape error: {str(e)[:50]}")

    def _extract_rakuten_price(self, html: str) -> float:
        """
        楽天HTMLから価格を抽出する.

        複数のパターンを試し、最も信頼性の高い価格を返す.
        """
        prices_found = []

        # パターン0: 構造化データ（JSON-LD）から抽出（最優先）
        # <script type="application/ld+json">{"@type":"Product","offers":{"price":17900}}</script>
        jsonld_matches = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
        for jsonld in jsonld_matches:
            try:
                import json
                data = json.loads(jsonld)
                # Product タイプの offers から価格を取得
                if isinstance(data, dict):
                    offers = data.get("offers", {})
                    if isinstance(offers, dict):
                        price = offers.get("price") or offers.get("lowPrice")
                        if price:
                            prices_found.append(float(str(price).replace(',', '')))
                    elif isinstance(offers, list):
                        for offer in offers:
                            price = offer.get("price") or offer.get("lowPrice")
                            if price:
                                prices_found.append(float(str(price).replace(',', '')))
            except:
                pass

        # パターン1: 販売価格 class="price2" や "price--xxxxx"
        # <span class="price2">17,900円</span>
        pattern1 = re.findall(r'class="price[^"]*"[^>]*>\s*[¥￥]?([\d,]+)\s*円?', html, re.IGNORECASE)
        for p in pattern1:
            try:
                prices_found.append(float(p.replace(',', '')))
            except:
                pass

        # パターン1b: data-testid属性（React/Next.js用）
        pattern1b = re.findall(r'data-testid="[^"]*price[^"]*"[^>]*>\s*[¥￥]?([\d,]+)', html, re.IGNORECASE)
        for p in pattern1b:
            try:
                prices_found.append(float(p.replace(',', '')))
            except:
                pass

        # パターン2: data-price属性
        # data-price="17900"
        pattern2 = re.findall(r'data-price="([\d]+)"', html)
        for p in pattern2:
            try:
                prices_found.append(float(p))
            except:
                pass

        # パターン3: JSON内の価格（商品データ）
        # "price":17900 or "price": 17900 or "price":"17900"
        pattern3 = re.findall(r'"price"\s*:\s*"?([\d]+)"?', html)
        for p in pattern3:
            try:
                val = float(p)
                if val >= 100:  # 100円以上
                    prices_found.append(val)
            except:
                pass

        # パターン3b: Next.js __NEXT_DATA__ からの価格抽出
        # "displayPrice":17900 or "unitPrice":17900 or "salePrice":17900
        pattern3b = re.findall(r'"(?:displayPrice|unitPrice|salePrice|originalPrice)"\s*:\s*([\d]+)', html)
        for p in pattern3b:
            try:
                val = float(p)
                if val >= 100:
                    prices_found.append(val)
            except:
                pass

        # パターン4: 価格表示の一般的なパターン
        # ￥17,900 or ¥17,900 or 17,900円
        pattern4 = re.findall(r'[¥￥]\s*([\d,]+)|(\d{1,3}(?:,\d{3})+)\s*円', html)
        for match in pattern4:
            for p in match:
                if p:
                    try:
                        prices_found.append(float(p.replace(',', '')))
                    except:
                        pass

        # パターン5: 販売価格を含むテキスト周辺
        # 販売価格：17,900円 or 価格(税込): 17,900円
        pattern5 = re.findall(r'(?:販売価格|価格|税込)[^0-9]*[¥￥]?([\d,]+)', html, re.IGNORECASE)
        for p in pattern5:
            try:
                prices_found.append(float(p.replace(',', '')))
            except:
                pass

        # パターン6: item-price / itemPrice クラス
        pattern6 = re.findall(r'(?:item-price|itemPrice)[^>]*>\s*[¥￥]?([\d,]+)', html, re.IGNORECASE)
        for p in pattern6:
            try:
                prices_found.append(float(p.replace(',', '')))
            except:
                pass

        # パターン7: Rakuten特有のPriceWrapper
        pattern7 = re.findall(r'(?:PriceWrapper|priceArea)[^>]*>\s*[¥￥]?([\d,]+)', html, re.IGNORECASE)
        for p in pattern7:
            try:
                prices_found.append(float(p.replace(',', '')))
            except:
                pass

        # 有効な価格をフィルタ（100円以上、1000万円以下）
        valid_prices = [p for p in prices_found if 100 <= p <= 10000000]

        if not valid_prices:
            return 0

        # 最頻出価格を返す（複数回出てくる価格が正しい可能性が高い）
        from collections import Counter
        price_counts = Counter(valid_prices)
        most_common = price_counts.most_common(1)

        if most_common:
            return most_common[0][0]

        # 頻度が同じなら最小価格を返す
        return min(valid_prices)

    def _scrape_amazon(self, url: str) -> ScrapedPrice:
        """
        Amazon.co.jpの商品ページから価格を取得する.

        Amazonは通常のスクレイピングが難しいため、
        基本的なパターンのみ試す.
        """
        try:
            print(f"    [Scrape] Fetching Amazon page...")
            response = self.session.get(url, timeout=self.REQUEST_TIMEOUT)
            response.raise_for_status()
            html = response.text

            price = self._extract_amazon_price(html)

            if price > 0:
                print(f"    [Scrape] Amazon price found: JPY {price:,.0f}")
                return ScrapedPrice(price=price, currency="JPY", source="Amazon", success=True)
            else:
                return ScrapedPrice(price=0, success=False, error_message="Price not found (Amazon anti-bot)")

        except requests.exceptions.RequestException as e:
            return ScrapedPrice(price=0, success=False, error_message=f"Request failed: {str(e)[:50]}")
        except Exception as e:
            return ScrapedPrice(price=0, success=False, error_message=f"Scrape error: {str(e)[:50]}")

    def _extract_amazon_price(self, html: str) -> float:
        """
        AmazonのHTMLから価格を抽出する.
        """
        prices_found = []

        # パターン1: priceblock_ourprice, priceblock_dealprice
        pattern1 = re.findall(r'id="priceblock_[^"]*"[^>]*>\s*[¥￥]?\s*([\d,]+)', html)
        for p in pattern1:
            try:
                prices_found.append(float(p.replace(',', '')))
            except:
                pass

        # パターン2: a-price-whole クラス
        pattern2 = re.findall(r'class="a-price-whole"[^>]*>([\d,]+)', html)
        for p in pattern2:
            try:
                prices_found.append(float(p.replace(',', '')))
            except:
                pass

        # パターン3: corePrice_feature_div 内の価格
        pattern3 = re.findall(r'corePrice[^>]*>\s*[¥￥]?\s*([\d,]+)', html)
        for p in pattern3:
            try:
                prices_found.append(float(p.replace(',', '')))
            except:
                pass

        # パターン4: twister-plus-price-data-price
        pattern4 = re.findall(r'data-a-color="price"[^>]*>\s*[¥￥]?\s*([\d,]+)', html)
        for p in pattern4:
            try:
                prices_found.append(float(p.replace(',', '')))
            except:
                pass

        # 有効な価格をフィルタ
        valid_prices = [p for p in prices_found if 100 <= p <= 10000000]

        if valid_prices:
            return min(valid_prices)  # 最安値を返す

        return 0

    def _scrape_yahoo(self, url: str) -> ScrapedPrice:
        """
        Yahoo!ショッピングの商品ページから価格を取得する.
        """
        try:
            print(f"    [Scrape] Fetching Yahoo Shopping page...")
            response = self.session.get(url, timeout=self.REQUEST_TIMEOUT)
            response.raise_for_status()
            html = response.text

            price = self._extract_yahoo_price(html)

            if price > 0:
                print(f"    [Scrape] Yahoo price found: JPY {price:,.0f}")
                return ScrapedPrice(price=price, currency="JPY", source="Yahoo", success=True)
            else:
                return ScrapedPrice(price=0, success=False, error_message="Price not found")

        except requests.exceptions.RequestException as e:
            return ScrapedPrice(price=0, success=False, error_message=f"Request failed: {str(e)[:50]}")
        except Exception as e:
            return ScrapedPrice(price=0, success=False, error_message=f"Scrape error: {str(e)[:50]}")

    def _extract_yahoo_price(self, html: str) -> float:
        """
        Yahoo!ショッピングHTMLから価格を抽出する.
        """
        prices_found = []

        # パターン1: ItemPrice クラス
        pattern1 = re.findall(r'class="[^"]*ItemPrice[^"]*"[^>]*>\s*[¥￥]?([\d,]+)', html, re.IGNORECASE)
        for p in pattern1:
            try:
                prices_found.append(float(p.replace(',', '')))
            except:
                pass

        # パターン2: elPrice クラス
        pattern2 = re.findall(r'class="[^"]*elPrice[^"]*"[^>]*>\s*[¥￥]?([\d,]+)', html, re.IGNORECASE)
        for p in pattern2:
            try:
                prices_found.append(float(p.replace(',', '')))
            except:
                pass

        # パターン3: 一般的な価格表記
        pattern3 = re.findall(r'[¥￥]\s*([\d,]+)\s*(?:円|税込)?', html)
        for p in pattern3:
            try:
                prices_found.append(float(p.replace(',', '')))
            except:
                pass

        # 有効な価格をフィルタ
        valid_prices = [p for p in prices_found if 100 <= p <= 10000000]

        if valid_prices:
            # 最頻出を優先
            from collections import Counter
            price_counts = Counter(valid_prices)
            most_common = price_counts.most_common(1)
            if most_common:
                return most_common[0][0]
            return min(valid_prices)

        return 0


# シングルトンインスタンス
_scraper: Optional[PriceScraper] = None


def get_scraper() -> PriceScraper:
    """PriceScraperのシングルトンインスタンスを取得."""
    global _scraper
    if _scraper is None:
        _scraper = PriceScraper()
    return _scraper


def scrape_price_for_url(url: str) -> ScrapedPrice:
    """
    URLから価格をスクレイピングで取得する（便利関数）.

    Args:
        url: 商品ページURL

    Returns:
        ScrapedPrice: スクレイピング結果
    """
    return get_scraper().scrape_price(url)
