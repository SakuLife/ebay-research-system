"""SerpApi client for eBay sold items search and Amazon.co.jp search."""

import os
import re
from typing import List, Optional
from dataclasses import dataclass

try:
    from serpapi import GoogleSearch
    SERPAPI_AVAILABLE = True
except ImportError:
    SERPAPI_AVAILABLE = False


def clean_query_for_shopping(query: str, max_length: int = 100) -> str:
    """
    eBayタイトルをGoogle Shopping検索用に整形する.

    - 販売者情報・配送情報など関係ない言葉を削除
    - Bundle/Lot/Collection系を削除
    - PSA 10等のグレーディングは残す（重要な識別子）
    - 文字数制限

    Args:
        query: 元のクエリ（eBayタイトル）
        max_length: 最大文字数

    Returns:
        整形後のクエリ
    """
    if not query:
        return ""

    # 販売者・配送関連を削除（商品と無関係）
    noise_patterns = [
        r'\bFREE\s*SHIPPING\b',
        r'\bFAST\s*SHIPPING\b',
        r'\bUS\s*SELLER\b',
        r'\bUK\s*SELLER\b',
        r'\bJAPAN\s*IMPORT\b',
        r'\bJAPANESE\s*VERSION\b',
        r'\bFROM\s*JAPAN\b',
        r'\bSHIPS?\s*FROM\b',
        r'\bWORLDWIDE\b',
        # バンドル・まとめ売り系
        r'\b\d+\s*CARDS?\s*LOT\b',      # "180 Cards Lot"
        r'\bLOT\s*OF\s*\d+\b',           # "Lot of 50"
        r'\bBUNDLE\b',
        r'\bCOLLECTION\b',
        r'\bBULK\b',
        r'\bSET\s*OF\s*\d+\b',           # "Set of 10"
        # 宣伝文句
        r'\bMUST\s*SEE\b',
        r'\bLOOK\b',
        r'\bWOW\b',
        r'\bHOT\b',
        r'\bL@@K\b',
        r'\bNR\b',                        # No Reserve
        r'\bNO\s*RESERVE\b',
    ]
    for pattern in noise_patterns:
        query = re.sub(pattern, '', query, flags=re.IGNORECASE)

    # 余計な記号を削除（括弧内の短い記号表記など）
    query = re.sub(r'\([^)]{1,3}\)', '', query)   # (NM) (JP) など短いもの
    query = re.sub(r'\[[^\]]{1,3}\]', '', query)  # [NM] など短いもの

    # 複数のスペースを1つに
    query = re.sub(r'\s+', ' ', query).strip()

    # 文字数制限（単語単位で切る）
    if len(query) > max_length:
        words = query.split()
        result = []
        current_len = 0
        for word in words:
            if current_len + len(word) + 1 <= max_length:
                result.append(word)
                current_len += len(word) + 1
            else:
                break
        query = ' '.join(result)

    return query.strip()


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
    thumbnail: str = ""  # 商品サムネイル画像URL
    category_id: str = ""
    category_name: str = ""


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
class AliExpressItem:
    """AliExpress商品の情報."""
    title: str
    price: float
    currency: str
    link: str
    item_id: str
    thumbnail: str = ""
    rating: float = 0.0
    orders: int = 0  # 注文数


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

    # フリマ・中古系サイト（New条件時に除外）
    # これ以外のサイトは全て許可（AliExpress, Alibaba等も含む）
    FLEA_MARKET_DOMAINS = [
        "mercari.com",      # メルカリ
        "jp.mercari.com",
        "yahoo.co.jp",      # ヤフオク
        "auctions.yahoo.co.jp",
        "paypayfleamarket", # PayPayフリマ
        "fril.jp",          # ラクマ
        "rakuma.rakuten",
        "magi.camp",        # magi
        "2ndstreet",        # セカスト
        "bookoff",          # ブックオフ
        "hardoff",          # ハードオフ
        "suruga-ya.jp",     # 駿河屋（中古メイン）
        "mandarake",        # まんだらけ
        "lashinbang",       # らしんばん
        "komehyo.jp",       # コメ兵（ブランド中古品）
        "brandoff",         # ブランドオフ
        "brand-revalue",    # ブランドリバリュー
        # 中古ゴルフクラブ専門店
        "golfdo.com",       # ゴルフドゥ（中古専門）
        "golfkids.jp",      # ゴルフキッズ（中古専門）
        "golfeffort.com",   # ゴルフエフォート（中古専門）
        "golfpartner.co.jp",  # ゴルフパートナー（中古クラブ店）
        "golfpartner.jp",   # ゴルフパートナー
        "usedgolf",         # 中古ゴルフ系
        "putiers.cards",    # フリマ系サイト（.cards TLD）
    ]

    # 除外すべきサイト（検索結果に出てきても無視）
    EXCLUDED_DOMAINS = [
        "ebay.com",         # eBay自体
        "ebay.co.uk",
        "ebay.de",
        "google.com",       # Googleリダイレクト
    ]

    # 購入不可サイト（商品紹介のみ、通販機能なし）
    NON_PURCHASABLE_DOMAINS = [
        "imfinityapp.com",       # 空サイト（商品表示なし）
        "noseencontratron.com",  # 空サイト（商品表示なし）
        "hinkalihachapuri.com",  # スパム/偽ECサイト（商品一覧ページのみ）
        "shanthinethralaya.com", # 海外スパムサイト（商品なし）
        "themarinaatroweswharf.com",  # 海外スパム/ブログサイト
        "sgs109.com",            # 商品紹介ページ（購入不可）
        "saleflatet.click",      # スパム/偽ECサイト（商品一覧のみ）
        "rsrbooking.com",        # 謎サイト（商品なし）
        "trustedcoach.com",      # 危険なサイト（マルウェア疑い）
        "shobanarealty.com",     # 不動産サイト（ECサイトではない）
        "audio-st.jp",           # オーディオ買取実績ページ（販売なし）
        "bishoujoseries.com",    # ホラー美少女 商品紹介サイト
        "kotobukiya.co.jp",      # コトブキヤ公式（直販なし、店舗案内のみ）
        "goodsmile.info",        # グッドスマイルカンパニー公式（直販なし）
        "megahobby.jp",          # メガハウス公式（直販なし）
        "bandai-hobby.net",      # バンダイホビー公式（直販なし）
        "tamiya.com",            # タミヤ公式（製品紹介のみ、購入はtamiyashop.jp）
        "ktc.jp",                # KTC公式カタログ（製品紹介のみ、購入は販売店経由）
        "toyosteel.jp",          # TOYO STEEL公式（製品一覧のみ、購入は販売店経由）
        "tamashii.jp",           # 魂ウェブ（予約のみ、即購入不可が多い）
        "hobbystock.jp",         # ホビーストック（情報サイト）
        "amiami.jp",             # あみあみ公式（.comは通販OK、.jpは情報）
        "figure.fm",             # フィギュア情報サイト
        "myfigurecollection.net", # コレクション管理サイト
        "hlj.com",               # ホビーリンクジャパン海外向け（日本発送不可）
        "play-asia.com",         # プレイアジア海外向け
        "nin-nin-game.com",      # 海外向け（日本発送不可）
        "kodansha.co.jp/comic",  # 講談社コミック（商品紹介のみ、購入不可）
        "wikipedia.org",         # 百科事典
        "wikia.com",             # ファンサイト
        "fandom.com",            # ファンサイト
        "youtube.com",           # 動画サイト
        "twitter.com",           # SNS
        "x.com",                 # SNS
        "instagram.com",         # SNS
        "facebook.com",          # SNS
        "pinterest.com",         # 画像SNS
        "reddit.com",            # 掲示板
        "blog.",                 # 一般ブログ
        "ameblo.jp",             # アメブロ
        "fc2.com",               # FC2
        "livedoor.",             # ライブドア
        "hatena.",               # はてな
        "note.com",              # note
        "tsi-holdings.com",      # TSIホールディングス（ブランド紹介ページ、通販なし）
        "kaseifu-babysitter.com",  # 家政婦マッチングサイト（ECサイトではない、閉鎖済み）
        "kogopay.com",             # 謎のサイトマップ的ページ（商品ページなし）
        "rockettrailer.com",       # 404 Not Found（スパム/デッドサイト）
        "superdetodo.com",         # 海外スパムサイト（偽商品ページ）
        "etpur.com",               # アクセス不可サイト
        "e-aj.co.jp",              # 404エラーが多い
        "tenryu-site.com",         # 403 Forbiddenでアクセス不可
        "fullress.com",            # スニーカーニュース/リリース情報ブログ（購入不可）
        "stv.jp",                  # 札幌テレビ放送（テレビ局サイト、EC機能なし）
        "snkrdunk.com",            # スニーカーダンク（フリマ/二次流通）
        "ec.treasure-f.com",       # 中古品専門サイト（コンディションB等）
        "treasure-f.com",          # 中古品専門サイト（買取・中古販売）
        "retailjewellerindia.com", # 謎サイト（インド・ジュエリー系スパム）
    ]

    # スパム/詐欺サイトによく使われるTLD（トップレベルドメイン）
    # これらのTLDを持つサイトは基本的に除外する
    SUSPICIOUS_TLDS = [
        ".click",    # saleflatet.click, lookse.click 等
        ".xyz",      # スパムに多用されるTLD
        ".top",      # 中国系スパムに多い
        ".work",     # スパムサイトに多い
        ".site",     # 低信頼性
        ".online",   # 低信頼性
        ".icu",      # スパムに多用
        ".buzz",     # スパムに多用
        ".cards",    # putiers.cards 等（フリマ系スパム）
        ".clinic",   # 医療機関（ECサイトではない）
        ".dental",   # 歯科（ECサイトではない）
        ".hospital", # 病院（ECサイトではない）
        ".salon",    # サロン（ECサイトではない）
        ".fitness",  # フィットネス（ECサイトではない）
        ".travel",   # 旅行系（ECサイトではない）
        ".realty",   # 不動産（ECサイトではない）
        ".law",      # 法律事務所（ECサイトではない）
        ".attorney", # 弁護士（ECサイトではない）
        ".church",   # 教会（ECサイトではない）
        ".school",   # 学校（ECサイトではない）
        ".university", # 大学（ECサイトではない）
    ]

    # 海外国別TLD（日本国内仕入先ではないため除外）
    # .jp / .co.jp = 日本（許可）、.com / .net / .org = 汎用（許可）
    FOREIGN_COUNTRY_TLDS = [
        # 欧州
        ".dk",       # デンマーク（seramikku.dk等）
        ".de",       # ドイツ
        ".fr",       # フランス
        ".it",       # イタリア
        ".es",       # スペイン
        ".nl",       # オランダ
        ".be",       # ベルギー
        ".se",       # スウェーデン
        ".no",       # ノルウェー
        ".fi",       # フィンランド
        ".at",       # オーストリア
        ".ch",       # スイス
        ".pt",       # ポルトガル
        ".pl",       # ポーランド
        ".cz",       # チェコ
        ".ie",       # アイルランド
        ".hu",       # ハンガリー
        ".ro",       # ルーマニア
        ".co.uk",    # イギリス
        ".uk",       # イギリス
        # 北米・南米
        ".ca",       # カナダ
        ".mx",       # メキシコ
        ".br",       # ブラジル
        ".ar",       # アルゼンチン
        ".cl",       # チリ
        ".co",       # コロンビア
        # アジア太平洋（日本以外）
        ".nz",       # ニュージーランド（replete.nz等）
        ".au",       # オーストラリア
        ".kr",       # 韓国
        ".cn",       # 中国
        ".tw",       # 台湾
        ".hk",       # 香港
        ".sg",       # シンガポール
        ".my",       # マレーシア
        ".th",       # タイ
        ".in",       # インド
        ".ph",       # フィリピン
        ".id",       # インドネシア
        # その他
        ".ru",       # ロシア
        ".za",       # 南アフリカ
        ".tr",       # トルコ
        ".il",       # イスラエル
    ]

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

    # eBay Item Location codes
    EBAY_LOCATION_CODES = {
        "japan": "104",
        "us": "1",
        "uk": "3",
        "germany": "77",
        "china": "45",
        "worldwide": None,  # フィルタなし
    }

    def search_sold_items(
        self,
        keyword: str,
        market: str = "UK",
        min_price: float = 0,
        max_results: int = 20,
        item_location: str = "japan",
        condition: str = "any"
    ) -> List[SoldItem]:
        """
        eBayで売れた商品（完了したリスティング）を検索する.

        Args:
            keyword: 検索キーワード
            market: マーケット (UK, US, EU)
            min_price: 最低価格（現地通貨）
            max_results: 最大取得件数
            item_location: 商品所在地フィルタ ("japan", "us", "uk", "worldwide")
                           デフォルトは"japan"（日本から出品された商品のみ）
            condition: 商品状態 ("new", "used", "any")

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

        # New条件の場合、仕入れ困難な商品を除外ワードで除く
        search_keyword = keyword
        if condition.lower() == "new":
            exclude_terms = [
                "-card", "-cards", "-tcg", "-ccg",  # カード系
                "-kuji", "-lottery", "-prize",  # 一番くじ系
                "-PSA", "-BGS", "-CGC", "-graded",  # 鑑定品
                "-promo", "-promotional",  # プロモ
                "-used", "-junk",  # 中古
                "-set", "-bundle", "-lot", "-combo", "-complete",  # セット販売
            ]
            search_keyword = f"{keyword} {' '.join(exclude_terms)}"
            print(f"  [eBay] Auto-exclude for New: card, kuji, PSA, promo, set/bundle")

        params = {
            "engine": "ebay",
            "ebay_domain": ebay_domain,
            "_nkw": search_keyword,
            "LH_Sold": "1",       # 売れた商品
            "LH_Complete": "1",   # 完了したリスティング
            "_ipg": str(min(max_results * 2, 60)),  # 余裕を持って取得
            "api_key": self.api_key
        }

        # Add price filter if specified
        if min_price > 0:
            params["_udlo"] = str(int(min_price))  # Minimum price

        # Add item location filter (日本から出品された商品に限定)
        location_code = self.EBAY_LOCATION_CODES.get(item_location.lower())
        if location_code:
            params["_salic"] = location_code
            print(f"  [SerpApi] Filtering by location: {item_location} (code={location_code})")

        # Add condition filter
        # LH_ItemCondition: 1000=New, 1500=Open box, 3000=Used, 7000=For parts
        if condition.lower() == "new":
            params["LH_ItemCondition"] = "1000"
            print(f"  [SerpApi] Filtering by condition: New only (LH_ItemCondition=1000)")

        try:
            print(f"  [SerpApi] Searching sold items: '{search_keyword[:60]}...' on {ebay_domain}" if len(search_keyword) > 60 else f"  [SerpApi] Searching sold items: '{search_keyword}' on {ebay_domain}")
            search = GoogleSearch(params)
            results = search.get_dict()

            # Check for errors
            if "error" in results:
                print(f"  [ERROR] SerpApi error: {results['error']}")
                return []

            organic = results.get("organic_results", [])
            print(f"  [SerpApi] Found {len(organic)} sold items")

            sold_items = []
            seen_item_ids = set()  # 重複item_id除外用
            for item in organic[:max_results * 2]:  # 重複除外分を考慮して多めに取得
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

                    # 重複item_idをスキップ（同一商品の複数落札を除外）
                    if item_id and item_id in seen_item_ids:
                        continue
                    if item_id:
                        seen_item_ids.add(item_id)

                    # max_results件に達したら終了
                    if len(sold_items) >= max_results:
                        break

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

                    item_condition = item.get("condition", "")
                    shipping = item.get("shipping", "")
                    thumbnail = item.get("thumbnail", "")

                    # New条件の場合、USEDアイテムをスキップ
                    if condition.lower() == "new":
                        condition_lower = item_condition.lower()
                        title_lower = title.lower()

                        # コンディション欄で判定
                        if any(used_term in condition_lower for used_term in [
                            "used", "pre-owned", "pre owned", "refurbished",
                            "for parts", "not working", "中古",
                            "like new", "likenew", "like-new",  # ほぼ新品も中古扱い
                            "open box", "openbox",  # 開封済み
                            "seller refurbished",  # 出品者整備済み
                            "certified refurbished",  # 認定整備済み
                        ]):
                            continue

                        # タイトルで中古品判定（MINTは中古の状態表記）
                        # 「MINT」「NEAR MINT」「TOP MINT」等は中古品の良品を示す
                        used_title_patterns = [
                            r'\bmint\b',           # MINT（単独）
                            r'\bnear mint\b',      # NEAR MINT
                            r'\btop mint\b',       # TOP MINT
                            r'\bexcellent\+?\+?\b', # EXCELLENT, EXCELLENT+, EXCELLENT++
                            r'\bvery good\b',      # VERY GOOD
                            r'\bgood\b',           # GOOD（状態表記として）
                            r'\bjunk\b',           # JUNK
                            r'\bas is\b',          # AS IS
                            r'\bused\b',           # USED
                        ]
                        if any(re.search(pattern, title_lower) for pattern in used_title_patterns):
                            continue

                    # カテゴリ情報を取得（SerpApiの結果に含まれている場合）
                    category_id = item.get("category_id", "") or item.get("categoryId", "")
                    category_name = item.get("category_name", "") or item.get("categoryName", "")
                    # extensions内にカテゴリがある場合もある
                    extensions = item.get("extensions", [])
                    if not category_name and extensions:
                        # extensionsの最初の要素がカテゴリ名の場合がある
                        for ext in extensions:
                            if isinstance(ext, str) and not ext.startswith("Free"):
                                category_name = ext
                                break

                    # デバッグ: カテゴリ情報を表示
                    if category_id or category_name:
                        print(f"    [DEBUG] Category: {category_name} ({category_id})")
                    else:
                        # どのキーにカテゴリがあるか確認
                        cat_keys = [k for k in item.keys() if 'cat' in k.lower()]
                        if cat_keys:
                            print(f"    [DEBUG] Cat keys found: {cat_keys} = {[item.get(k) for k in cat_keys]}")

                    sold_items.append(SoldItem(
                        title=title,
                        price=price,
                        currency=currency,
                        link=link,
                        item_id=item_id,
                        condition=item_condition,
                        shipping=shipping,
                        thumbnail=thumbnail,
                        category_id=category_id,
                        category_name=category_name,
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
                        # Note: Don't remove commas before search to avoid merging multiple prices
                        import re
                        # Search for price pattern (digits with optional commas, max 7 digits)
                        match = re.search(r'[\d,]{1,9}', price_info)
                        if match:
                            price_str = match.group().replace(',', '')
                            # Sanity check: price should be reasonable (max 10 million yen)
                            if len(price_str) <= 8:
                                price = float(price_str)
                            else:
                                price = 0
                        else:
                            price = 0
                        raw_price = price_info
                    else:
                        price = 0
                        raw_price = ""

                    # If price not in main field, try price.raw
                    if price == 0 and "price" in item:
                        raw = item.get("price", {}).get("raw", "")
                        if raw:
                            import re
                            # Search for price pattern (digits with optional commas, limited length)
                            match = re.search(r'[\d,]{1,9}', raw)
                            if match:
                                price_str = match.group().replace(',', '')
                                # Sanity check: price should be reasonable (max 10 million yen)
                                if len(price_str) <= 8:
                                    price = float(price_str)

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

    def search_aliexpress(
        self,
        keyword: str,
        max_results: int = 10
    ) -> List[AliExpressItem]:
        """
        AliExpressで商品を検索する.
        中国からの仕入れ先として使用.

        Args:
            keyword: 検索キーワード（英語推奨）
            max_results: 最大取得件数

        Returns:
            AliExpressItemのリスト
        """
        if not self.is_enabled:
            print("  [WARN] SerpApi is not available")
            return []

        params = {
            "engine": "aliexpress",
            "q": keyword,
            "api_key": self.api_key
        }

        try:
            print(f"  [SerpApi] Searching AliExpress: '{keyword}'")
            search = GoogleSearch(params)
            results = search.get_dict()

            if "error" in results:
                print(f"  [ERROR] SerpApi AliExpress error: {results['error']}")
                return []

            organic = results.get("organic_results", [])
            print(f"  [SerpApi] Found {len(organic)} AliExpress items")

            items = []
            for item in organic[:max_results]:
                try:
                    title = item.get("title", "")
                    link = item.get("link", "")
                    item_id = item.get("product_id", "") or item.get("item_id", "")
                    thumbnail = item.get("thumbnail", "")

                    # Parse price (USD)
                    price = 0.0
                    price_info = item.get("price", {})
                    if isinstance(price_info, dict):
                        price = price_info.get("extracted", 0) or price_info.get("value", 0) or 0
                    elif isinstance(price_info, (int, float)):
                        price = float(price_info)
                    elif isinstance(price_info, str):
                        match = re.search(r'[\d.]+', price_info.replace(',', ''))
                        if match:
                            price = float(match.group())

                    # 価格0はスキップ
                    if price <= 0:
                        continue

                    # Rating and orders
                    rating = item.get("rating", 0) or 0
                    orders = 0
                    orders_str = item.get("orders", "") or item.get("sold", "")
                    if orders_str:
                        match = re.search(r'[\d,]+', str(orders_str).replace(',', ''))
                        if match:
                            orders = int(match.group().replace(',', ''))

                    items.append(AliExpressItem(
                        title=title,
                        price=price,
                        currency="USD",
                        link=link,
                        item_id=item_id,
                        thumbnail=thumbnail,
                        rating=rating,
                        orders=orders,
                    ))

                except Exception as e:
                    print(f"  [WARN] Failed to parse AliExpress item: {e}")
                    continue

            return items

        except Exception as e:
            print(f"  [ERROR] SerpApi AliExpress request failed: {e}")
            return []

    def _extract_url_from_google_redirect(self, google_url: str) -> Optional[str]:
        """
        Google.comのリダイレクトURLから実際の商品URLを抽出する.

        対応パターン:
        - google.com/url?q=https://actual-shop.com/...
        - google.com/aclk?adurl=https://...
        - google.co.jp/url?url=https://...

        Args:
            google_url: google.comを含むURL

        Returns:
            抽出された実URL、または抽出できない場合はNone
        """
        if not google_url or "google.com" not in google_url:
            return None

        try:
            from urllib.parse import urlparse, parse_qs, unquote

            # URLを複数回デコード（二重エンコード対策）
            decoded_url = google_url
            for _ in range(3):
                new_decoded = unquote(decoded_url)
                if new_decoded == decoded_url:
                    break
                decoded_url = new_decoded

            parsed = urlparse(decoded_url)
            query_params = parse_qs(parsed.query)

            # よくあるリダイレクトパラメータ
            redirect_params = ["url", "q", "u", "adurl", "dest", "redirect", "landing"]
            for param in redirect_params:
                if param in query_params:
                    extracted = query_params[param][0]
                    # 複数回デコード
                    for _ in range(3):
                        new_extracted = unquote(extracted)
                        if new_extracted == extracted:
                            break
                        extracted = new_extracted

                    # google.comでない実際のURLの場合は返す
                    if extracted and "google.com" not in extracted and "google.co.jp" not in extracted:
                        if extracted.startswith("http"):
                            return extracted
                        # httpで始まらない場合、ドメイン形式のみhttps://を付与
                        # 例: amazon.co.jp/item → OK
                        # 例: Figure 1/7 ver. → NG (検索クエリを誤って変換しない)
                        # ドメインパターン: xxx.xxx/... 形式かつ空白を含まない
                        if "." in extracted and "/" in extracted and " " not in extracted:
                            # 先頭がドメイン形式（英数字.英数字）であることを確認
                            domain_pattern = re.match(r'^[a-zA-Z0-9][\w.-]*\.[a-zA-Z]{2,}', extracted)
                            if domain_pattern:
                                return "https://" + extracted

            # パスにURLが埋め込まれているパターン
            # 例: /url/https://shop.com/item
            path = parsed.path
            if "/url/" in path:
                url_part = path.split("/url/", 1)[1]
                if url_part.startswith("http"):
                    return unquote(url_part)

            return None
        except Exception as e:
            return None

    def _extract_product_id_from_google_shopping(self, google_url: str) -> Optional[str]:
        """
        Google Shopping の商品ページURLから product_id を抽出する.

        Args:
            google_url: google.com/shopping/product/xxx 形式のURL

        Returns:
            product_id、または抽出できない場合はNone
        """
        if not google_url:
            return None

        # /shopping/product/12345678901234567890 形式からIDを抽出
        match = re.search(r'/shopping/product/(\d+)', google_url)
        if match:
            return match.group(1)
        return None

    def _fetch_seller_link_from_product_id(self, product_id: str) -> Optional[str]:
        """
        Google Product APIを使って商品IDから実際の販売者リンクを取得する.

        Args:
            product_id: Google Shopping の商品ID

        Returns:
            販売者の実際のURL、または取得できない場合はNone
        """
        if not self.is_enabled or not product_id:
            return None

        try:
            params = {
                "engine": "google_product",
                "product_id": product_id,
                "hl": "ja",
                "gl": "jp",
                "api_key": self.api_key
            }

            search = GoogleSearch(params)
            results = search.get_dict()

            if "error" in results:
                return None

            # sellersセクションから最初のリンクを取得
            sellers = results.get("sellers_results", {}).get("online_sellers", [])
            if sellers and len(sellers) > 0:
                link = sellers[0].get("link", "")
                if link and "google.com" not in link:
                    return link

            # 代替: product_results.source から
            product_results = results.get("product_results", {})
            source_link = product_results.get("source", "")
            if source_link and "google.com" not in source_link:
                return source_link

            return None
        except Exception as e:
            print(f"  [WARN] Failed to fetch seller link for product_id {product_id}: {e}")
            return None

    def search_google_shopping_jp(
        self,
        keyword: str,
        max_results: int = 10,
        global_search: bool = False
    ) -> List[ShoppingItem]:
        """
        Google Shoppingで商品を検索する.
        Amazon, 楽天, AliExpress等の結果を取得.

        Args:
            keyword: 検索キーワード
            max_results: 最大取得件数
            global_search: True=グローバル検索（US拠点）、False=日本限定

        Returns:
            ShoppingItemのリスト
        """
        if not self.is_enabled:
            print("  [WARN] SerpApi is not available")
            return []

        if global_search:
            # グローバル検索（AliExpress, Amazon.com等も含む）
            params = {
                "engine": "google_shopping",
                "q": keyword,
                "hl": "en",
                "gl": "us",
                "api_key": self.api_key
            }
            print(f"  [SerpApi] Searching Google Shopping (Global): '{keyword}'")
        else:
            # 日本限定検索
            params = {
                "engine": "google_shopping",
                "q": keyword,
                "location": "Japan",
                "hl": "ja",
                "gl": "jp",
                "api_key": self.api_key
            }
            print(f"  [SerpApi] Searching Google Shopping (JP): '{keyword}'")

        try:
            search = GoogleSearch(params)
            results = search.get_dict()

            if "error" in results:
                print(f"  [ERROR] SerpApi Google Shopping error: {results['error']}")
                return []

            shopping_results = results.get("shopping_results", [])
            print(f"  [SerpApi] Found {len(shopping_results)} shopping items")

            items = []
            skipped_google_urls = 0
            product_api_calls = 0
            max_product_api_calls = 3  # API使用量を制限（追加のクレジット消費を抑える）

            for item in shopping_results[:max_results * 2]:  # 余裕を持って取得
                try:
                    title = item.get("title", "")
                    source = item.get("source", "")
                    thumbnail = item.get("thumbnail", "")

                    # リンク取得（google.com以外のURLを優先）
                    # linkがgoogle.comの場合はproduct_linkやsource_linkを先に使う
                    link_field = item.get("link", "")
                    product_link_field = item.get("product_link", "")
                    source_link_field = item.get("source_link", "")

                    # google.com以外のURLを優先的に選択
                    if product_link_field and "google.com" not in product_link_field:
                        link = product_link_field
                    elif source_link_field and "google.com" not in source_link_field:
                        link = source_link_field
                    elif link_field and "google.com" not in link_field:
                        link = link_field
                    else:
                        # すべてgoogle.comの場合は後続の処理で抽出を試みる
                        link = link_field or product_link_field or source_link_field

                    # sellersがあればそこから直接リンクを取得
                    if not link or "google.com" in link:
                        sellers = item.get("sellers", [])
                        if sellers and isinstance(sellers, list) and len(sellers) > 0:
                            seller_link = sellers[0].get("link", "")
                            if seller_link and "google.com" not in seller_link:
                                link = seller_link

                    # google.comのリダイレクトURLから実URLを抽出を試みる
                    if link and "google.com" in link:
                        extracted_url = self._extract_url_from_google_redirect(link)
                        if extracted_url:
                            link = extracted_url

                    # /shopping/product/ 形式のURLの場合、product_idを抽出してAPI経由で実URLを取得
                    # ただしAPI使用量を制限
                    if link and "google.com" in link and "/shopping/product/" in link:
                        if product_api_calls < max_product_api_calls:
                            product_id = self._extract_product_id_from_google_shopping(link)
                            if product_id:
                                product_api_calls += 1
                                seller_link = self._fetch_seller_link_from_product_id(product_id)
                                if seller_link:
                                    link = seller_link
                                    print(f"  [SerpApi] Resolved product_id {product_id} -> {seller_link[:50]}...")

                    # 最終的にgoogle.comのURLしかない場合はスキップ
                    if not link or "google.com" in link:
                        skipped_google_urls += 1
                        # デバッグ: スキップされたURL と利用可能なフィールドを出力
                        if skipped_google_urls <= 3:  # 最初の3件だけ表示
                            original_link = item.get("product_link", "") or item.get("source_link", "")
                            available_keys = [k for k in item.keys() if 'link' in k.lower() or 'url' in k.lower()]
                            print(f"    [DEBUG] Skipped: {original_link[:60]}...")
                            print(f"    [DEBUG] Available URL fields: {available_keys}, source={source}")
                        continue

                    # Parse price
                    price = 0.0
                    extracted_price = item.get("extracted_price", 0)
                    if extracted_price:
                        price = float(extracted_price)
                    else:
                        price_str = item.get("price", "")
                        if price_str:
                            # 数値を抽出（カンマ除去）
                            match = re.search(r'[\d,]+', price_str.replace(',', ''))
                            if match:
                                price = float(match.group().replace(',', ''))

                    # 価格0円はスキップ
                    if price <= 0:
                        continue

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

                    if len(items) >= max_results:
                        break

                except Exception as e:
                    print(f"  [WARN] Failed to parse shopping item: {e}")
                    continue

            if skipped_google_urls > 0:
                print(f"  [SerpApi] Skipped {skipped_google_urls} items with google.com URLs")
            print(f"  [SerpApi] Returning {len(items)} valid shopping items")

            return items

        except Exception as e:
            print(f"  [ERROR] SerpApi Google Shopping request failed: {e}")
            return []

    def _is_excluded_site(self, url: str, condition: str = "any") -> bool:
        """
        URLが除外すべきサイトかどうか判定する.

        Args:
            url: チェックするURL
            condition: "new"=フリマ除外, "used"/"any"=除外なし

        Returns:
            True=除外すべき, False=許可
        """
        url_lower = url.lower()

        # 常に除外するサイト（eBay自体、Googleリダイレクト等）
        if any(domain in url_lower for domain in self.EXCLUDED_DOMAINS):
            return True

        # 購入不可サイト（商品紹介のみ、通販機能なし）を除外
        if any(domain in url_lower for domain in self.NON_PURCHASABLE_DOMAINS):
            return True

        # 怪しいTLD / 海外国別TLDを除外
        # URLからドメイン部分を抽出してTLDをチェック
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url_lower)
            domain = parsed.netloc
            if any(domain.endswith(tld) for tld in self.SUSPICIOUS_TLDS):
                return True
            # 海外サイトを除外（国内仕入先のみ対象）
            if any(domain.endswith(tld) for tld in self.FOREIGN_COUNTRY_TLDS):
                return True
        except:
            pass

        # 商品一覧ページ（個別商品ページではない）を除外
        list_page_patterns = [
            "productlist",      # ProductList.aspx等
            "/search.html",     # 検索結果ページ
            "/search.aspx",     # 検索結果ページ（.NET系）
            "goods/search",     # 商品検索ページ（samantha.co.jp等）
            "/search?",         # 検索クエリ
            "/ksearch",         # askul.co.jp/ksearch/ 等
            "?swrd=",           # 検索ワード
            "?keywords=",       # キーワード検索
            "?searchword=",     # 検索ワード（askul等）
            "/category/",       # カテゴリページ
            "/collections/",    # Shopify系一覧ページ（ironheart.jp等）
            "coordinate_detail",  # JINSコーディネートページ（商品ページではない）
            "/stores/",           # Amazonストアページ（個別商品ではない）
        ]
        if any(pattern in url_lower for pattern in list_page_patterns):
            return True

        # スパム/SEOハッキングサイトのパターンを除外
        # 例: ?e=70810289948602 のようなeBay商品IDをパラメータに持つURL
        spam_url_patterns = [
            r"\?e=\d{10,}",       # ?e= + 10桁以上の数字（eBay ID風）
            r"\?item=\d{10,}",    # ?item= + 10桁以上の数字
            r"\?id=\d{10,}",      # ?id= + 10桁以上の数字
        ]
        if any(re.search(pattern, url_lower) for pattern in spam_url_patterns):
            return True

        # New条件の場合、フリマ・中古系を除外
        if condition.lower() == "new":
            if any(domain in url_lower for domain in self.FLEA_MARKET_DOMAINS):
                return True

            # URLパスに中古・アウトレットを示すパターンが含まれる場合も除外
            # 例: shop.golfdigest.co.jp/used/ → 中古品ページ
            used_path_patterns = [
                "/used/",           # 中古品カテゴリ（GDO等）
                "/useditems/",      # 中古品アイテム
                "/secondhand/",     # セカンドハンド
                "/pre-owned/",      # 認定中古
                "/refurbished/",    # リファービッシュ
                "/outlet/",         # アウトレット品
                "condition=used",   # Amazon等の中古品パラメータ
            ]
            if any(pattern in url_lower for pattern in used_path_patterns):
                return True

        return False

    def _extract_source_name(self, url: str) -> str:
        """URLからソース名を抽出する."""
        url_lower = url.lower()

        # 日本のサイト
        if "amazon.co.jp" in url_lower or "amazon.jp" in url_lower:
            return "Amazon"
        if "rakuten.co.jp" in url_lower:
            return "楽天"
        if "yahoo" in url_lower:
            return "Yahoo"
        if "mercari" in url_lower:
            return "メルカリ"
        if "yodobashi" in url_lower:
            return "ヨドバシ"
        if "biccamera" in url_lower:
            return "ビックカメラ"
        if "suruga-ya" in url_lower:
            return "駿河屋"

        # 海外サイト
        if "aliexpress" in url_lower:
            return "AliExpress"
        if "alibaba" in url_lower:
            return "Alibaba"
        if "amazon.com" in url_lower:
            return "Amazon US"
        if "walmart" in url_lower:
            return "Walmart"
        if "target.com" in url_lower:
            return "Target"

        # ドメインから抽出
        try:
            from urllib.parse import urlparse
            domain = urlparse(url).netloc
            # www.を除去
            if domain.startswith("www."):
                domain = domain[4:]
            # 最初のドット前を取得
            return domain.split(".")[0].capitalize()
        except:
            return "その他"

    def search_by_image(
        self,
        image_url: str,
        condition: str = "any",
        max_results: int = 10
    ) -> List[ShoppingItem]:
        """
        Google Lensで画像検索する.
        eBay商品画像から仕入先を探す（フリマ除外はconditionで制御）.

        Args:
            image_url: eBay商品の画像URL
            condition: "new"=フリマ除外, "used"/"any"=全サイト対象
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

            # デバッグ: 最初の3件のデータ構造を表示
            for i, sample in enumerate(visual_matches[:3]):
                price_data = sample.get("price", "N/A")
                print(f"    [DEBUG] Sample {i+1}: price={price_data}, keys={list(sample.keys())[:5]}")

            items = []
            excluded_count = 0

            google_url_count = 0
            for item in visual_matches[:max_results * 3]:  # 余裕を持って取得
                try:
                    title = item.get("title", "")
                    # リンクは複数フィールドを確認
                    link = item.get("link", "") or item.get("product_link", "") or item.get("source_link", "")

                    # リンクが空の場合はスキップ
                    if not link:
                        continue

                    # google.comのURLはスキップ（実際の商品ページではない）
                    if "google.com" in link:
                        # リダイレクトURLから実URLを抽出を試みる
                        extracted_url = self._extract_url_from_google_redirect(link)
                        if extracted_url:
                            link = extracted_url
                        else:
                            google_url_count += 1
                            continue

                    # 除外サイトチェック（フリマ除外 or 常に除外）
                    if self._is_excluded_site(link, condition):
                        excluded_count += 1
                        continue

                    # ソース名を抽出
                    source = self._extract_source_name(link)

                    # 価格取得（複数の形式に対応）
                    price = 0.0
                    price_info = item.get("price", {})
                    if isinstance(price_info, dict):
                        # extracted_value を優先
                        price = price_info.get("extracted_value", 0) or 0
                        # value も試す
                        if price == 0:
                            price = price_info.get("value", 0) or 0
                        # raw から抽出（"￥17,900" 形式）
                        if price == 0:
                            raw = price_info.get("raw", "") or price_info.get("currency", "")
                            if raw:
                                match = re.search(r'[\d,]+', str(raw).replace(',', ''))
                                if match:
                                    price = float(match.group().replace(',', ''))
                    elif isinstance(price_info, (int, float)):
                        price = float(price_info)
                    elif isinstance(price_info, str):
                        # 文字列から価格を抽出
                        match = re.search(r'[\d,]+', price_info.replace(',', ''))
                        if match:
                            price = float(match.group().replace(',', ''))

                    # price_info がない場合、タイトルから価格を抽出
                    # ただし割引・クーポン金額は除外
                    if price == 0:
                        # まず割引パターンを除去したタイトルで検索
                        # "7,000円引き", "7000円OFF", "7,000円クーポン" などを除外
                        discount_patterns = [
                            r'[\d,]+\s*円\s*引き',
                            r'[\d,]+\s*円\s*OFF',
                            r'[\d,]+\s*円\s*off',
                            r'[\d,]+\s*円\s*クーポン',
                            r'[\d,]+\s*円\s*ポイント',
                            r'最大\s*[\d,]+\s*円',
                        ]
                        clean_title = title
                        for dp in discount_patterns:
                            clean_title = re.sub(dp, '', clean_title)

                        price_patterns = [
                            r'[¥￥]\s*([\d,]+)\s*円',
                            r'([\d,]+)\s*円',
                            r'[¥￥]\s*([\d,]+)',
                        ]
                        for pattern in price_patterns:
                            match = re.search(pattern, clean_title)
                            if match:
                                extracted = float(match.group(1).replace(',', ''))
                                if extracted >= 100:
                                    price = extracted
                                    break

                    thumbnail = item.get("thumbnail", "")

                    # デバッグ: 価格0のとき、price_infoの中身を表示
                    if price == 0 and price_info:
                        print(f"    [DEBUG] Lens: price=0, info={price_info}, title={title[:30]}")

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

            if google_url_count > 0:
                print(f"  [SerpApi] Skipped {google_url_count} google.com URLs")
            if excluded_count > 0:
                print(f"  [SerpApi] Excluded {excluded_count} sites (condition={condition})")
            print(f"  [SerpApi] Found {len(items)} valid matches")
            return items

        except Exception as e:
            print(f"  [ERROR] SerpApi Google Lens request failed: {e}")
            return []

    def search_google_web_jp(
        self,
        keyword: str,
        condition: str = "any",
        max_results: int = 10
    ) -> List[ShoppingItem]:
        """
        Google Web検索（日本）で商品を検索する.
        Shopping検索で結果がない場合のフォールバック用.
        ブラックリスト方式：フリマ系以外は全て許可.

        Args:
            keyword: 検索キーワード
            condition: 商品状態 "new"=フリマ除外, "used"/"any"=全サイト
            max_results: 最大取得件数

        Returns:
            ShoppingItemのリスト
        """
        if not self.is_enabled:
            print("  [WARN] SerpApi is not available")
            return []

        # 「通販」を追加して通販サイトを優先的にヒット
        search_query = f"{keyword} 通販"

        params = {
            "engine": "google",
            "q": search_query,
            "location": "Japan",
            "hl": "ja",
            "gl": "jp",
            "num": 50,  # 多めに取得してフィルタ
            "api_key": self.api_key
        }

        try:
            print(f"  [SerpApi] Google Web search: '{keyword[:50]}...' (+通販)")
            search = GoogleSearch(params)
            results = search.get_dict()

            if "error" in results:
                print(f"  [ERROR] SerpApi Google Web error: {results['error']}")
                return []

            organic_results = results.get("organic_results", [])
            print(f"  [SerpApi] Found {len(organic_results)} web results")

            items = []
            excluded_count = 0

            # デバッグ: 最初の3件の内容を表示
            for i, sample in enumerate(organic_results[:3]):
                print(f"    [DEBUG] Web {i+1}: {sample.get('title', '')[:40]}...")
                print(f"             snippet: {sample.get('snippet', '')[:60]}...")

            for item in organic_results:
                try:
                    link = item.get("link", "")

                    # 除外サイトチェック（フリマ除外 or 常に除外）
                    if self._is_excluded_site(link, condition):
                        excluded_count += 1
                        continue

                    title = item.get("title", "")
                    snippet = item.get("snippet", "")

                    # ソース名を抽出
                    source = self._extract_source_name(link)

                    # 価格を抽出（title + snippet + rich_snippet から検索）
                    price = 0.0
                    rich_snippet = item.get("rich_snippet", {})
                    rich_text = ""
                    if isinstance(rich_snippet, dict):
                        # rich_snippet内のテキストを全て結合
                        for key, val in rich_snippet.items():
                            if isinstance(val, str):
                                rich_text += f" {val}"
                            elif isinstance(val, list):
                                rich_text += " ".join(str(v) for v in val)

                    text_to_search = f"{title} {snippet} {rich_text}"

                    # 割引・クーポン金額を先に除去
                    discount_patterns = [
                        r'[\d,]+\s*円\s*引き',
                        r'[\d,]+\s*円\s*OFF',
                        r'[\d,]+\s*円\s*off',
                        r'[\d,]+\s*円\s*クーポン',
                        r'[\d,]+\s*円\s*ポイント',
                        r'最大\s*[\d,]+\s*円',
                    ]
                    clean_text = text_to_search
                    for dp in discount_patterns:
                        clean_text = re.sub(dp, '', clean_text)

                    # 複数の価格パターンを試す
                    price_patterns = [
                        r'[¥￥]\s*([\d,]+)\s*円',      # ¥1,234円
                        r'([\d,]+)\s*円\s*[（(税込]?',  # 1,234円（税込）
                        r'[¥￥]\s*([\d,]+)(?!\d)',     # ¥1,234
                        r'価格[：:]\s*[¥￥]?([\d,]+)', # 価格：1,234
                        r'([\d,]{4,})\s*円',           # 1234円 (4桁以上)
                    ]

                    for pattern in price_patterns:
                        price_match = re.search(pattern, clean_text)
                        if price_match:
                            price = float(price_match.group(1).replace(',', ''))
                            if price >= 100:  # 100円以上のみ有効
                                break
                            price = 0.0  # リセットして次のパターン

                    # 大手ECサイトの場合、価格0でも結果に含める
                    # （後で手動確認 or API連携で価格取得可能）
                    major_ec_sites = ["amazon.co.jp", "rakuten.co.jp", "shopping.yahoo.co.jp"]
                    is_major_ec = any(site in link for site in major_ec_sites)

                    # 価格0円で大手EC以外はスキップ
                    if price <= 0 and not is_major_ec:
                        continue

                    # 大手ECで価格なしの場合、結果には含めるが価格は推定不可としてマーク
                    if price <= 0 and is_major_ec:
                        print(f"    [INFO] Major EC site without price: {source} - {title[:40]}...")

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
                    print(f"  [WARN] Failed to parse web result: {e}")
                    continue

            if excluded_count > 0:
                print(f"  [SerpApi] Excluded {excluded_count} sites (condition={condition})")
            print(f"  [SerpApi] Found {len(items)} valid matches")
            return items

        except Exception as e:
            print(f"  [ERROR] SerpApi Google Web request failed: {e}")
            return []
