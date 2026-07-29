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


def _describe_request_error(exc: requests.RequestException) -> str:
    """API失敗の理由を1行で説明する（HTTPコードとAPI側のメッセージを含める）.

    「0件」と「障害で取れなかった」を取り違えないための表示用。
    """
    resp = getattr(exc, "response", None)
    if resp is None:
        return f"{type(exc).__name__}: {exc}"

    detail = ""
    try:
        body = resp.json()
        # 楽天は error/error_description、Amazon PA-API は Errors[].Message
        detail = str(
            body.get("error_description")
            or body.get("error")
            or (body.get("Errors") or [{}])[0].get("Message", "")
        )
    except ValueError:
        detail = resp.text[:120]

    return f"HTTP {resp.status_code}{' - ' + detail if detail else ''}"


# Google Shopping（gl=jp）でも海外通販が混ざる。実際に
# vvs-automatismes.fr（仏）や osbrankoradicevicobilic.edu.rs（セルビア）が
# 「国内最安」として採用されかけた。要件は国内仕入なので明示的に弾く。
#
# 判定方針: 日本の店は店名が日本語か、よく知られたブランド名で返る。
# 海外の雑多なサイトは生ドメイン文字列で返る、という差を使う。
_DOMESTIC_SHOP_HINTS = (
    "楽天", "rakuten", "amazon", "yahoo", "ヤフー", "paypay", "au pay",
    "ヨドバシ", "yodobashi", "ビックカメラ", "biccamera", "ジョーシン", "joshin",
    "エディオン", "edion", "ケーズデンキ", "ノジマ", "ヤマダ", "yamada",
    "コストコ", "costco", "ロハコ", "lohaco", "アスクル", "askul",
    "セブンネット", "ロフト", "loft", "東急ハンズ", "ハンズ", "hands",
    "ドン・キホーテ", "ドンキ", "qoo10", "roomclip", "モノタロウ", "monotaro",
    "サウンドハウス", "soundhouse", "駿河屋", "あみあみ", "amiami",
    "ヨドバシカメラ", "マツキヨ", "ウエルシア", "スギ薬局", "ココカラ",
    "公式", "オンライン", "ショッピング", "ストア", "市場", "本店", "通販",
    # 国内ECプラットフォーム（個人店がこの形で出てくる）
    "base.shop", "thebase", "stores.jp", "shop-pro", "makeshop", "ocnk",
    "buyee", "zozo", "magaseek", "locondo", "shoplist",
    # 国内量販・専門店（ASCII表記で返ることがある）
    "nitori", "ニトリ", "muji", "無印", "uniqlo", "ユニクロ", "hmv", "tower",
    "animate", "アニメイト", "sofmap", "ソフマップ", "tsukumo", "ツクモ",
    "dospara", "ドスパラ", "kojima", "コジマ", "cainz", "カインズ",
    "workman", "ワークマン", "kohnan", "コーナン", "dinos", "nissen",
)


def _is_domestic_source(source: str) -> bool:
    """SerpApiの source（店名）が国内ショップかを判定する.

    **国内である積極的な根拠がある場合だけ通す**（ホワイトリスト方式）。
    根拠は「日本語を含む」「既知の国内ショップ名」「.jp ドメイン」のいずれか。

    当初は「生ドメインらしい文字列だけ弾く」ブラックリスト方式にしていたが、
    `Dorothea Design`（万年筆を楽天の1/3の値段で出す欧州系）のような
    ASCII表記の海外ショップが素通りし、非現実的な利益額が出た。
    実データ上、国内店は「楽天市場 - 店名」「Amazon公式サイト」「ヤマダデンキ」
    のように日本語か既知ブランドで返るため、ホワイトリスト方式で実害が少ない。

    判定を誤って弾いた店は `_DOMESTIC_SHOP_HINTS` に追記して救済する
    （除外件数はログに出るので、取りこぼしが多ければ気付ける）。
    """
    if not source:
        return False
    s = source.strip().lower()

    # 日本語（ひらがな・カタカナ・漢字）を含めば国内
    if any("぀" <= ch <= "ヿ" or "一" <= ch <= "鿿" for ch in source):
        return True

    for hint in _DOMESTIC_SHOP_HINTS:
        if hint in s:
            return True

    if ".jp" in s:
        return True

    return False


class SourcingClient:
    def __init__(self) -> None:
        self.rakuten = RakutenClient(
            application_id=os.getenv("RAKUTEN_APPLICATION_ID"),
            affiliate_id=os.getenv("RAKUTEN_AFFILIATE_ID"),
            # 2026年のAPI刷新で必須になった。未設定なら楽天は自動的に無効化される
            access_key=os.getenv("RAKUTEN_ACCESS_KEY"),
            # アプリ登録時の Allowed websites と一致させること
            referer=os.getenv("RAKUTEN_REFERER"),
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
            # 環境変数名は SERP_API_KEY で全社統一（serpapi_client.py・workflow・.envと一致）。
            # ここだけ SERPAPI_API_KEY を読んでおり、SerpApi経路が恒久的に無効だった（2026-07-28修正）
            api_key=os.getenv("SERP_API_KEY"),
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

        # SerpApi(Google Shopping)は楽天・Amazon・Yahoo・ヨドバシ等を横断して引ける。
        #
        # ⚠️ 「直APIが0件のときだけ呼ぶ」フォールバック方式にすると、
        #    楽天に在庫があった時点で探索を打ち切るため、楽天より安い他店
        #    （コストコ・ヨドバシ等）を取り逃す。要件は「多数ある国内販売サイトから
        #    最安値を見つける」なので、既定では毎回呼んで全経路の和から最安を採る。
        #
        # SerpApiは有料。クレジットを絞りたい場合は SOURCING_SERPAPI_MODE=fallback で
        # 従来の「直APIが0件のときだけ」に戻せる。
        serp_mode = os.getenv("SOURCING_SERPAPI_MODE", "always").strip().lower()
        should_call_serp = self.serpapi.is_enabled and (
            serp_mode == "always" or not offers
        )
        if should_call_serp:
            reason = "全経路から最安を採るため" if offers else "直APIが0件のため"
            print(f"  [DEBUG] SerpApi(Google Shopping)検索: {reason}")
            serp_offers = self.serpapi.search_google_shopping(
                listing.search_query, max_results=max_results * 2
            )
            print(f"  [DEBUG] SerpApi検索結果: {len(serp_offers)}件")
            offers.extend(serp_offers)
        elif self.serpapi.is_enabled:
            print(f"  [DEBUG] SerpApi検索: スキップ（SOURCING_SERPAPI_MODE=fallback・直APIで取得済み）")

        if not offers:
            return []

        # 同一URLの重複を除去（楽天直APIとSerpApi経由で同じ商品が来ることがある）
        deduped: List[SourceOffer] = []
        seen_urls: set = set()
        for offer in offers:
            key = (offer.source_url or "").split("?")[0]
            if key and key in seen_urls:
                continue
            if key:
                seen_urls.add(key)
            deduped.append(offer)

        # Sort by total price (price + shipping) and return top N
        deduped.sort(key=lambda o: o.source_price_jpy + o.source_shipping_jpy)
        return deduped[:max_results]

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


# 楽天ウェブサービスは2026年にインフラ刷新され、旧エンドポイントは廃止された。
#   旧: https://app.rakuten.co.jp/services/api/IchibaItem/Search/20170706
#   新: https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260701
# ドメインだけでなくパス（/services/api/ → /ichibams/api/）とバージョンも変わり、
# 認証は applicationId に加えて accessKey が必須になった。
# 旧エンドポイントは全API一律で 503 "under maintenance" を返すため、
# 「一時的な障害」に見えるが実際は移行しないと永久に復旧しない（2026-07-29 実測）。
RAKUTEN_API_BASE = "https://openapi.rakuten.co.jp/ichibams/api"
RAKUTEN_ICHIBA_VERSION = "20260701"

# Referer は必須（2026-07-29 実測で確定）。
# アプリ登録時の Allowed websites に入れたドメインを送らないと
# 403 REQUEST_CONTEXT_BODY_HTTP_REFERRER_MISSING で弾かれる。
#
# ⚠️ 測定の落とし穴: 未登録のIDやダミーのaccessKeyで試すと、Refererを送らなくても
#    「Invalid Access Key」まで到達するため「Refererは不要」と誤解する。
#    正規キーで初めて要求される（検査はaccessKeyより前段）。
# ⚠️ 楽天所有ドメイン（rakuten.co.jp 等）を名乗ると 403 IP許可リスト違反。
#    アプリ登録フォームのプレースホルダにその例が薄字で出ているが真似ないこと。
#    localhost も 503 になるため使えない。
RAKUTEN_DEFAULT_REFERER = ""


class RakutenClient:
    def __init__(
        self,
        application_id: Optional[str],
        affiliate_id: Optional[str],
        access_key: Optional[str] = None,
        referer: Optional[str] = None,
    ) -> None:
        self.application_id = application_id
        self.affiliate_id = affiliate_id
        self.access_key = access_key
        # 既定は空（送らない）。必要な場合のみ Allowed websites と一致する値を入れる
        self.referer = referer if referer is not None else RAKUTEN_DEFAULT_REFERER
        # accessKey が無いと新APIは 400 を返すため、両方揃って初めて有効とする
        self.is_enabled = bool(self.application_id) and bool(self.access_key)

        if self.application_id and not self.access_key:
            print(
                "  [楽天] RAKUTEN_ACCESS_KEY が未設定のため無効化しました。"
                "2026年のAPI刷新で accessKey が必須になっています "
                "（楽天ウェブサービスのアプリ管理画面から取得）"
            )

    @property
    def search_url(self) -> str:
        return f"{RAKUTEN_API_BASE}/IchibaItem/Search/{RAKUTEN_ICHIBA_VERSION}"

    @property
    def request_headers(self) -> Dict[str, str]:
        """Referer検査用のヘッダー（未指定なら送らない）."""
        if not self.referer:
            return {}
        return {"Referer": self.referer, "Origin": self.referer.rstrip("/")}

    def _build_params(self, keyword: str, hits: int) -> Dict[str, str]:
        """新API仕様の共通パラメータを組み立てる."""
        params: Dict[str, str] = {
            "applicationId": self.application_id,
            "accessKey": self.access_key,
            "keyword": keyword,
            "hits": str(hits),
            "format": "json",
        }
        if self.affiliate_id:
            params["affiliateId"] = self.affiliate_id
        return params

    @staticmethod
    def _unwrap(entry: Dict) -> Dict:
        """商品1件を取り出す.

        旧仕様は {"Item": {...}} の入れ子、新仕様は平坦。
        どちらでも読めるようにしておく（新版のレスポンス形状は
        accessKey取得後に実データで要確認）。
        """
        inner = entry.get("Item")
        return inner if isinstance(inner, dict) else entry

    @staticmethod
    def _is_used_item(item_name: str, shop_name: str = "") -> bool:
        """中古品かどうかをタイトル・ショップ名から判定する."""
        text = (item_name + " " + shop_name).lower()

        # 中古を示すキーワード
        used_keywords = [
            "中古", "used", "ジャンク", "junk",
            "難あり", "訳あり", "傷あり",
            "プレイ用",
            # 状態ランク表記（中古品特有）
            "ランクa", "ランクb", "ランクc", "ランクs",
            "aランク", "bランク", "cランク", "sランク",
            "rank a", "rank b", "rank c",
            # アウトレット・展示品
            "展示品", "展示処分", "店頭展示",
            # 古書・レトロ
            "古書", "古本",
        ]

        # 中古ショップ名パターン
        used_shop_patterns = [
            "中古", "リサイクル", "買取", "質屋",
            "ブックオフ", "bookoff", "ハードオフ", "hardoff",
            "ゲオ", "geo", "セカンドストリート", "2ndstreet",
            "トレジャーファクトリー", "コメ兵", "komehyo",
            "まんだらけ", "mandarake", "駿河屋", "suruga-ya",
            # 個人間取引（C2C）＝原則中古。新品前提の利益計算に混ぜると数字が嘘になる
            "メルカリ", "mercari", "ヤフオク", "ラクマ", "rakuma",
            "paypayフリマ", "ジモティー",
        ]

        for kw in used_keywords:
            if kw in text:
                return True

        shop_lower = shop_name.lower()
        for pattern in used_shop_patterns:
            if pattern in shop_lower:
                return True

        return False

    def search(self, keyword: str) -> Optional[SourceOffer]:
        if not self.is_enabled:
            return None
        try:
            resp = requests.get(
                self.search_url,
                params=self._build_params(keyword, hits=5),
                headers=self.request_headers,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            # 握り潰すと障害が「0件」に化けて原因不明になる
            print(f"    [楽天] API失敗: {_describe_request_error(e)}")
            return None

        items = data.get("Items", [])
        if not items:
            return None
        # 中古品を除外してから最安値を選択
        new_items = [
            i for i in items
            if not self._is_used_item(
                self._unwrap(i).get("itemName", ""),
                self._unwrap(i).get("shopName", ""),
            )
        ]
        if not new_items:
            return None
        best = min(new_items, key=lambda i: self._unwrap(i).get("itemPrice", 10**12))
        item = self._unwrap(best)
        price = float(item.get("itemPrice", 0))
        url = item.get("itemUrl", "")
        availability = item.get("availability", 0)
        item_name = item.get("itemName", "")
        postage_flag = item.get("postageFlag", 0)
        shipping = 0.0 if postage_flag == 1 else 700.0
        image_urls = item.get("mediumImageUrls", [])
        image_url = image_urls[0].get("imageUrl", "") if image_urls else ""
        return SourceOffer(
            source_site="Rakuten",
            source_url=url,
            source_price_jpy=price,
            source_shipping_jpy=shipping,
            stock_hint="in_stock" if availability == 1 else "unknown",
            title=item_name,
            source_image_url=image_url,
        )

    def search_multiple(self, keyword: str, max_results: int = 5) -> List[SourceOffer]:
        """Search for multiple offers from Rakuten, sorted by price."""
        if not self.is_enabled:
            return []
        try:
            resp = requests.get(
                self.search_url,
                # Rakuten API max is 30
                params=self._build_params(keyword, hits=min(max_results, 30)),
                headers=self.request_headers,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            # 握り潰すと障害が「0件」に化けて原因不明になる
            print(f"    [楽天] API失敗: {_describe_request_error(e)}")
            return []

        items = data.get("Items", [])
        if not items:
            return []

        # 中古品を除外してからSourceOfferに変換
        offers = []
        used_excluded = 0
        for item_wrapper in items:
            item = self._unwrap(item_wrapper)
            item_name = item.get("itemName", "")
            shop_name = item.get("shopName", "")

            # 中古品フィルタ
            if self._is_used_item(item_name, shop_name):
                used_excluded += 1
                continue

            price = float(item.get("itemPrice", 0))
            url = item.get("itemUrl", "")
            availability = item.get("availability", 0)
            # 送料: postageFlag=1は送料無料、0は有料（デフォルト700円推定）
            postage_flag = item.get("postageFlag", 0)
            shipping = 0.0 if postage_flag == 1 else 700.0
            # 商品画像URL取得（Gemini画像比較用）
            image_urls = item.get("mediumImageUrls", [])
            image_url = image_urls[0].get("imageUrl", "") if image_urls else ""

            if price > 0 and url:
                offers.append(SourceOffer(
                    source_site="Rakuten",
                    source_url=url,
                    source_price_jpy=price,
                    source_shipping_jpy=shipping,
                    stock_hint="in_stock" if availability == 1 else "unknown",
                    title=item_name,
                    source_image_url=image_url,
                ))

        if used_excluded > 0:
            print(f"    [楽天] 中古品除外: {used_excluded}件")

        # Sort by total price (price + shipping) and return top N
        offers.sort(key=lambda o: o.source_price_jpy + o.source_shipping_jpy)
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
        except requests.RequestException as e:
            # 握り潰すと障害が「0件」に化けて原因不明になる（PA-APIは429/401を返す）
            print(f"    [Amazon] API失敗: {_describe_request_error(e)}")
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
        except requests.RequestException as e:
            # 握り潰すと障害が「0件」に化けて原因不明になる（PA-APIは429/401を返す）
            print(f"    [Amazon] API失敗: {_describe_request_error(e)}")
            return []

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
        used_excluded = 0
        foreign_excluded = 0
        foreign_names: List[str] = []
        for item in shopping_results:
            # 海外ショップを除外。要件は「国内仕入先」なので、
            # Google Shoppingに混ざる海外通販（.fr / .rs 等）を採用してはいけない
            src_name = item.get("source", "")
            if not _is_domestic_source(src_name):
                foreign_excluded += 1
                foreign_names.append(src_name)
                continue

            # 中古を除外（楽天クライアントと同じ判定を流用）。
            # Google Shoppingはメルカリ等のC2C中古が最安に並ぶため、
            # 除外しないと新品前提の利益計算が非現実的な数字になる
            if RakutenClient._is_used_item(item.get("title", ""), item.get("source", "")):
                used_excluded += 1
                continue

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

            # SerpApiのgoogle_shoppingは link を返さなくなり product_link に変わった。
            # link 必須のままだと全件スキップして「0件」に見える（2026-07-28修正）
            link = item.get("link") or item.get("product_link") or ""
            source = item.get("source", "Unknown")

            if price > 0 and link:
                offers.append(SourceOffer(
                    source_site=source,
                    source_url=link,
                    source_price_jpy=price,
                    source_shipping_jpy=0.0,
                    stock_hint="unknown",
                ))

        if used_excluded > 0:
            print(f"    [SerpApi] 中古品除外: {used_excluded}件")
        if foreign_excluded > 0:
            # 除外した店名を出す。国内店を誤って弾いていたら
            # _DOMESTIC_SHOP_HINTS に追記して救済できるようにするため
            names = ", ".join(dict.fromkeys(n for n in foreign_names if n))
            print(f"    [SerpApi] 国内以外として除外: {foreign_excluded}件 ({names[:90]})")

        # Sort by price
        offers.sort(key=lambda o: o.source_price_jpy)
        return offers[:max_results]
