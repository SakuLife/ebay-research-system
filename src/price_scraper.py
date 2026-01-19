"""Price Scraper - Scrape actual prices from EC site pages when API returns 0."""

import re
import random
import time
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
    in_stock: bool = True  # 在庫あり（デフォルト）
    stock_status: str = ""  # "in_stock", "out_of_stock", "unknown"


class PriceScraper:
    """ECサイトから価格をスクレイピングで取得するクライアント."""

    # リクエストタイムアウト（秒）
    REQUEST_TIMEOUT = 20  # 楽天は遅いことがあるので延長

    # User-Agent リスト（ランダムに選択）
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    ]

    def __init__(self):
        """初期化."""
        self.session = requests.Session()
        self._update_headers()

    def _update_headers(self):
        """ヘッダーを更新（Anti-bot対策）."""
        headers = {
            "User-Agent": random.choice(self.USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }
        self.session.headers.update(headers)

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
            # 汎用スクレイパー（在庫切れ検出のみ）
            return self._scrape_generic(url)

    def _scrape_rakuten(self, url: str) -> ScrapedPrice:
        """
        楽天市場の商品ページから価格を取得する.

        楽天の価格表示パターン:
        - <span class="price2">17,900円</span>
        - <span class="price--OX_YW">17,900円</span>
        - data-price="17900"
        - "price":17900
        """
        max_retries = 2

        for attempt in range(max_retries):
            try:
                # リトライ時はヘッダーを更新
                if attempt > 0:
                    self._update_headers()
                    time.sleep(0.5)

                print(f"    [Scrape] Fetching Rakuten page..." + (f" (retry {attempt + 1})" if attempt > 0 else ""))
                response = self.session.get(url, timeout=self.REQUEST_TIMEOUT)
                response.raise_for_status()
                html = response.text

                price = self._extract_rakuten_price(html)
                in_stock, stock_status = self._check_rakuten_stock(html)

                if price > 0:
                    stock_msg = " (在庫切れ)" if not in_stock else ""
                    print(f"    [Scrape] Rakuten price found: JPY {price:,.0f}{stock_msg}")
                    return ScrapedPrice(
                        price=price, currency="JPY", source="楽天",
                        success=True, in_stock=in_stock, stock_status=stock_status
                    )

                # 在庫切れで価格が見つからない場合
                if not in_stock:
                    print(f"    [Scrape] Rakuten: 在庫切れ検出")
                    return ScrapedPrice(
                        price=0, success=False, error_message="Out of stock",
                        in_stock=False, stock_status="out_of_stock"
                    )

                # 価格が見つからない場合、リトライ
                if attempt < max_retries - 1:
                    continue

                # 楽天: 価格が見つからない場合は売り切れとみなす
                # （売り切れページでは価格表示が消えるパターンが多い）
                print(f"    [Scrape] Rakuten: 価格なし → 売り切れの可能性大")
                return ScrapedPrice(
                    price=0, success=False, error_message="Out of stock (no price)",
                    in_stock=False, stock_status="out_of_stock"
                )

            except requests.exceptions.Timeout:
                if attempt < max_retries - 1:
                    continue
                return ScrapedPrice(price=0, success=False, error_message="Request timeout")
            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    continue
                return ScrapedPrice(price=0, success=False, error_message=f"Request failed: {str(e)[:50]}")
            except Exception as e:
                return ScrapedPrice(price=0, success=False, error_message=f"Scrape error: {str(e)[:50]}")

        return ScrapedPrice(price=0, success=False, error_message="Max retries exceeded")

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

    def _check_rakuten_stock(self, html: str) -> tuple:
        """
        楽天HTMLから在庫状況を判定する.

        Returns:
            (in_stock: bool, stock_status: str)
        """
        html_lower = html.lower()

        # 在庫切れ/売り切れを示すパターン
        out_of_stock_patterns = [
            "在庫切れ",
            "売り切れ",
            "sold out",
            "品切れ",
            "完売",
            "入荷待ち",
            "再入荷待ち",
            "予約受付終了",
            "販売終了",
            "取扱終了",
            "お取り扱いできません",
            '"availability"\\s*:\\s*"outofstock"',
            '"availability"\\s*:\\s*"https://schema.org/outofstock"',
            'class="[^"]*sold-?out[^"]*"',
            'class="[^"]*out-?of-?stock[^"]*"',
        ]

        for pattern in out_of_stock_patterns:
            if re.search(pattern, html, re.IGNORECASE):
                return (False, "out_of_stock")

        # 在庫ありを示すパターン（確認用）
        in_stock_patterns = [
            "在庫あり",
            "在庫わずか",
            "残りわずか",
            "カートに入れる",
            "今すぐ買う",
            '"availability"\\s*:\\s*"instock"',
            '"availability"\\s*:\\s*"https://schema.org/instock"',
        ]

        for pattern in in_stock_patterns:
            if re.search(pattern, html, re.IGNORECASE):
                return (True, "in_stock")

        # 判定不能の場合はデフォルトで在庫ありとする
        return (True, "unknown")

    def _scrape_amazon(self, url: str) -> ScrapedPrice:
        """
        Amazon.co.jpの商品ページから価格を取得する.

        Amazonは通常のスクレイピングが難しいため、
        リトライとサイト固有ヘッダーで対策.
        """
        max_retries = 2

        for attempt in range(max_retries):
            try:
                # リトライ時はヘッダーを更新（User-Agentをランダム化）
                if attempt > 0:
                    self._update_headers()
                    time.sleep(1)  # リトライ前に少し待機

                # Amazon固有のヘッダー
                headers = {
                    "Referer": "https://www.google.com/",
                    "Host": "www.amazon.co.jp",
                }

                print(f"    [Scrape] Fetching Amazon page..." + (f" (retry {attempt + 1})" if attempt > 0 else ""))
                response = self.session.get(url, timeout=self.REQUEST_TIMEOUT, headers=headers)
                response.raise_for_status()
                html = response.text

                # 重要: 在庫状況を先にチェック
                # 在庫切れの場合、ページ内の関連商品等の価格を誤って取得しないようにする
                in_stock, stock_status = self._check_amazon_stock(html)

                # 在庫切れの場合は即座に返す（価格抽出をスキップ）
                if not in_stock:
                    print(f"    [Scrape] Amazon: 在庫切れ検出 → 価格取得スキップ")
                    return ScrapedPrice(
                        price=0, success=False, error_message="Out of stock",
                        in_stock=False, stock_status="out_of_stock"
                    )

                price = self._extract_amazon_price(html)

                if price > 0:
                    print(f"    [Scrape] Amazon price found: JPY {price:,.0f}")
                    return ScrapedPrice(
                        price=price, currency="JPY", source="Amazon",
                        success=True, in_stock=in_stock, stock_status=stock_status
                    )

                # 価格が見つからない場合、リトライ
                if attempt < max_retries - 1:
                    continue

                return ScrapedPrice(price=0, success=False, error_message="Price not found (Amazon anti-bot)")

            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    continue
                return ScrapedPrice(price=0, success=False, error_message=f"Request failed: {str(e)[:50]}")
            except Exception as e:
                return ScrapedPrice(price=0, success=False, error_message=f"Scrape error: {str(e)[:50]}")

        return ScrapedPrice(price=0, success=False, error_message="Max retries exceeded")

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

    def _check_amazon_stock(self, html: str) -> tuple:
        """
        AmazonのHTMLから在庫状況を判定する.

        Returns:
            (in_stock: bool, stock_status: str)
        """
        # Amazonの在庫切れパターン
        out_of_stock_patterns = [
            "現在在庫切れです",
            "この商品の再入荷予定は立っておりません",
            "入荷時期は未定です",
            "現在お取り扱いできません",
            "この商品は現在お取り扱いできません",
            "一時的に在庫切れ",
            "在庫切れ",
            "Out of Stock",
            "Currently unavailable",
            "id=\"outOfStock\"",
            'id="availability"[^>]*>\s*(?:<[^>]*>)*\s*(?:現在在庫切れ|在庫切れ)',
            '"availability"\\s*:\\s*"https://schema.org/OutOfStock"',
        ]

        for pattern in out_of_stock_patterns:
            if re.search(pattern, html, re.IGNORECASE):
                return (False, "out_of_stock")

        # 在庫ありパターン
        in_stock_patterns = [
            "在庫あり",
            "残り\\d+点",
            "カートに入れる",
            "今すぐ買う",
            "id=\"add-to-cart-button\"",
            "id=\"buy-now-button\"",
            '"availability"\\s*:\\s*"https://schema.org/InStock"',
        ]

        for pattern in in_stock_patterns:
            if re.search(pattern, html, re.IGNORECASE):
                return (True, "in_stock")

        # 判定不能
        return (True, "unknown")

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
            in_stock, stock_status = self._check_yahoo_stock(html)

            if price > 0:
                stock_msg = " (在庫切れ)" if not in_stock else ""
                print(f"    [Scrape] Yahoo price found: JPY {price:,.0f}{stock_msg}")
                return ScrapedPrice(
                    price=price, currency="JPY", source="Yahoo",
                    success=True, in_stock=in_stock, stock_status=stock_status
                )

            # 在庫切れで価格が見つからない場合
            if not in_stock:
                return ScrapedPrice(
                    price=0, success=False, error_message="Out of stock",
                    in_stock=False, stock_status="out_of_stock"
                )

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

    def _check_yahoo_stock(self, html: str) -> tuple:
        """
        Yahoo!ショッピングのHTMLから在庫状況を判定する.

        Returns:
            (in_stock: bool, stock_status: str)
        """
        # 在庫切れパターン
        out_of_stock_patterns = [
            "在庫切れ",
            "売り切れ",
            "品切れ",
            "sold out",
            "入荷待ち",
            "販売終了",
            "取り扱いを終了",
            "お取り扱いしておりません",
            '"availability"\\s*:\\s*"OutOfStock"',
        ]

        for pattern in out_of_stock_patterns:
            if re.search(pattern, html, re.IGNORECASE):
                return (False, "out_of_stock")

        # 在庫ありパターン
        in_stock_patterns = [
            "在庫あり",
            "カートに入れる",
            "今すぐ買う",
            '"availability"\\s*:\\s*"InStock"',
        ]

        for pattern in in_stock_patterns:
            if re.search(pattern, html, re.IGNORECASE):
                return (True, "in_stock")

        # 判定不能
        return (True, "unknown")

    def _scrape_generic(self, url: str) -> ScrapedPrice:
        """
        汎用スクレイパー: 任意のサイトから在庫切れを検出する.

        価格抽出は行わず、在庫切れの検出のみを行う.
        merdisney.jp等の小規模ECサイト向け.
        """
        try:
            print(f"    [Scrape] Fetching page (generic)...")
            response = self.session.get(url, timeout=self.REQUEST_TIMEOUT)
            response.raise_for_status()
            html = response.text

            in_stock, stock_status = self._check_generic_stock(html)

            if not in_stock:
                print(f"    [Scrape] 在庫切れ検出 (generic)")
                return ScrapedPrice(
                    price=0, success=False, error_message="Out of stock",
                    in_stock=False, stock_status="out_of_stock"
                )

            # 在庫ありの場合は価格不明として返す（価格抽出は行わない）
            return ScrapedPrice(
                price=0, success=False, error_message="Price extraction not supported",
                in_stock=True, stock_status=stock_status
            )

        except requests.exceptions.RequestException as e:
            return ScrapedPrice(price=0, success=False, error_message=f"Request failed: {str(e)[:50]}")
        except Exception as e:
            return ScrapedPrice(price=0, success=False, error_message=f"Scrape error: {str(e)[:50]}")

    def _check_generic_stock(self, html: str) -> tuple:
        """
        汎用的な在庫切れ検出.

        様々なECサイトで共通して使われる在庫切れパターンをチェック.

        Returns:
            (in_stock: bool, stock_status: str)
        """
        # 在庫切れを示す汎用パターン
        out_of_stock_patterns = [
            # 日本語
            "在庫切れ",
            "売り切れ",
            "品切れ",
            "完売",
            "sold out",
            "soldout",
            "入荷待ち",
            "再入荷待ち",
            "予約受付終了",
            "販売終了",
            "取扱終了",
            "取り扱い終了",
            "お取り扱いできません",
            "現在お取り扱いしておりません",
            "この商品は現在販売しておりません",
            "ご購入いただけません",
            "カートに入れることができません",
            "ご注文できない",           # books.rakuten.co.jp
            "注文できません",
            "ご注文いただけません",
            "お買い求めいただけません",
            # 英語
            "out of stock",
            "out-of-stock",
            "currently unavailable",
            "not available",
            "no longer available",
            # Schema.org / 構造化データ
            r'"availability"\s*:\s*"(?:https?://schema\.org/)?(?:OutOfStock|SoldOut|Discontinued)"',
            r'"availability"\s*:\s*"outofstock"',
            # CSSクラス
            r'class="[^"]*(?:sold-?out|out-?of-?stock|unavailable)[^"]*"',
            # data属性
            r'data-(?:stock|availability)="(?:0|false|out|none)"',
        ]

        for pattern in out_of_stock_patterns:
            if re.search(pattern, html, re.IGNORECASE):
                return (False, "out_of_stock")

        # 在庫ありを示すパターン
        in_stock_patterns = [
            "在庫あり",
            "在庫わずか",
            "残りわずか",
            r"残り\d+点",
            "カートに入れる",
            "カートに追加",
            "今すぐ購入",
            "購入する",
            r'"availability"\s*:\s*"(?:https?://schema\.org/)?InStock"',
            r'"availability"\s*:\s*"instock"',
        ]

        for pattern in in_stock_patterns:
            if re.search(pattern, html, re.IGNORECASE):
                return (True, "in_stock")

        # 判定不能
        return (True, "unknown")


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
