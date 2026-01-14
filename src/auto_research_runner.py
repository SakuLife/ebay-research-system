"""Auto Research Runner - Pattern② full automation."""

import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

from dotenv import load_dotenv

from .ebay_client import EbayClient
from .sourcing import SourcingClient
from .profit import calculate_profit
from .sheets_client import GoogleSheetsClient
from .spreadsheet_mapping import INPUT_SHEET_COLUMNS
from .search_base_client import SearchBaseClient
from .config_loader import load_all_configs
from .weight_estimator import estimate_weight_from_title, detect_product_type
from .models import SourceOffer, ListingCandidate
from .serpapi_client import SerpApiClient, ShoppingItem, clean_query_for_shopping
from .gemini_client import GeminiClient
from .price_scraper import scrape_price_for_url


def encode_url_with_japanese(url: str) -> str:
    """
    日本語を含むURLを正しくエンコードする.
    スプレッドシートでリンクとして認識されるようにする.

    例: https://amazon.co.jp/ハイキュー/dp/xxx
      → https://amazon.co.jp/%E3%83%8F%E3%82%A4%E3%82%AD%E3%83%A5%E3%83%BC/dp/xxx
    """
    if not url:
        return ""

    try:
        from urllib.parse import urlparse, quote, urlunparse

        parsed = urlparse(url)
        # パス部分のみエンコード（既にエンコード済みの%は保持）
        encoded_path = quote(parsed.path, safe='/%')
        # クエリ部分もエンコード
        encoded_query = quote(parsed.query, safe='=&%')

        return urlunparse((
            parsed.scheme,
            parsed.netloc,
            encoded_path,
            parsed.params,
            encoded_query,
            parsed.fragment
        ))
    except Exception:
        return url  # エラー時は元のURLを返す


def clean_keyword_for_ebay(keyword: str) -> str:
    """
    キーワードをeBay検索用にクリーニングする.
    - 日本語（ひらがな・カタカナ・漢字）を除去
    - 括弧とその中身を除去
    - 余分なスペースを整理

    例: "HIKOKI（ハイコーキ）" → "HIKOKI"
        "Haikyu!!（ハイキュー）" → "Haikyu!!"
    """
    if not keyword:
        return ""

    # 括弧（全角・半角）とその中身を除去
    # （...）, (...), 【...】, [...] など
    cleaned = re.sub(r'[（(【\[][^）)\]】]*[）)\]】]', '', keyword)

    # 日本語文字を除去（ひらがな、カタカナ、漢字）
    cleaned = re.sub(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]+', '', cleaned)

    # 複数スペースを1つに、前後のスペースを除去
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()

    return cleaned


def extract_search_keywords(ebay_title: str) -> str:
    """
    eBayタイトルから日本の仕入先検索用のキーワードを抽出する。
    型番、ブランド名、シリーズ名などを優先的に抽出。
    """
    # 一般的なノイズワードを除去
    noise_words = {
        'new', 'used', 'rare', 'vintage', 'limited', 'edition', 'free', 'shipping',
        'japan', 'japanese', 'authentic', 'genuine', 'original', 'official',
        'sealed', 'mint', 'box', 'with', 'and', 'the', 'for', 'from',
    }

    # 型番パターン（英数字+ハイフン、例: RX-78-2, MG-100）
    model_numbers = re.findall(r'\b[A-Z]{1,4}[-]?\d{1,5}[-]?[A-Z0-9]*\b', ebay_title, re.IGNORECASE)

    # スケール表記（1/100, 1:144など）
    scales = re.findall(r'\b1[:/]\d{2,4}\b', ebay_title)

    # ブランド/シリーズ名（大文字始まりの単語）
    words = ebay_title.split()
    keywords = []

    for word in words:
        # クリーンアップ
        clean_word = re.sub(r'[^\w\s-]', '', word).strip()
        if not clean_word:
            continue
        lower_word = clean_word.lower()

        # ノイズワードをスキップ
        if lower_word in noise_words:
            continue

        # 2文字以上で、数字のみでないもの
        if len(clean_word) >= 2 and not clean_word.isdigit():
            keywords.append(clean_word)

    # 型番を優先的に追加
    result_parts = []
    if model_numbers:
        result_parts.extend(model_numbers[:2])
    if scales:
        result_parts.extend(scales[:1])

    # 残りのキーワードを追加（最大5単語）
    for kw in keywords[:5]:
        if kw not in result_parts:
            result_parts.append(kw)

    return ' '.join(result_parts[:5])


def extract_model_numbers(text: str) -> set:
    """
    テキストから型番を抽出する。
    工具、カード、フィギュア等の型番パターンに対応。
    """
    model_numbers = set()

    # 型番パターン（例: NV65HR2, G3618DA, RX-78-2, MG-100）
    patterns = [
        r'\b([A-Z]{1,3}\d{2,5}[A-Z0-9]*)\b',  # NV65HR2, G3618DA
        r'\b([A-Z]{1,4}-\d{1,5}[-A-Z0-9]*)\b',  # RX-78-2, MG-100
        r'\b(\d{3}/\d{3})\b',  # カード番号 217/187
    ]

    text_upper = text.upper()
    for pattern in patterns:
        matches = re.findall(pattern, text_upper)
        model_numbers.update(matches)

    return model_numbers


# pykakasi for Japanese to romaji conversion (handles kanji, hiragana, katakana)
try:
    import pykakasi
    # pykakasi 2.x uses new API
    _kakasi = pykakasi.kakasi()
    PYKAKASI_AVAILABLE = True
except ImportError:
    PYKAKASI_AVAILABLE = False
    _kakasi = None


def normalize_to_romaji(text: str) -> str:
    """
    テキストを正規化してローマ字に統一する.
    漢字・ひらがな・カタカナ → ローマ字に変換し、小文字に統一.

    pykakasi使用で漢字も変換可能:
    - "寂しい人が一番偉いんだ" → "sabishii hito ga ichiban erainda"
    - "沙棗 SASO オードパルファム" → "saso saso oodo parufamu"
    """
    if PYKAKASI_AVAILABLE and _kakasi:
        try:
            # pykakasi 2.x new API: convert() returns list of dicts
            result = _kakasi.convert(text)
            text = ''.join([item['hepburn'] for item in result])
        except Exception:
            pass  # Fall through to lowercase
    return text.lower()


def extract_quantities(text: str) -> set:
    """
    テキストから数量・容量を抽出する (50ml, 100g, 1.7oz など).
    """
    quantities = set()
    # 数字+単位パターン
    patterns = [
        r'(\d+(?:\.\d+)?)\s*(ml|l|oz|fl\.?\s*oz|g|kg|mg|cm|mm|m)\b',
        r'(\d+(?:\.\d+)?)\s*(ミリ|リットル|グラム|キロ|センチ|ミリメートル)',
    ]
    text_lower = text.lower()
    for pattern in patterns:
        matches = re.findall(pattern, text_lower)
        for num, unit in matches:
            # 単位を正規化
            unit = unit.replace(' ', '').replace('.', '')
            quantities.add(f"{num}{unit}")
    return quantities


# ブランド名マッピング（英語 ↔ 日本語）
BRAND_MAPPINGS = {
    'shiseido': '資生堂',
    'kanebo': 'カネボウ',
    'kose': 'コーセー',
    'kao': '花王',
    'lion': 'ライオン',
    'bandai': 'バンダイ',
    'takara': 'タカラ',
    'tomy': 'トミー',
    'sanrio': 'サンリオ',
    'nintendo': '任天堂',
    'sony': 'ソニー',
    'panasonic': 'パナソニック',
    'hitachi': '日立',
    'toyota': 'トヨタ',
    'honda': 'ホンダ',
    'yamaha': 'ヤマハ',
    'casio': 'カシオ',
    'seiko': 'セイコー',
    'citizen': 'シチズン',
    'kato': 'カトー',
    'tomix': 'トミックス',
}


def calculate_title_similarity(ebay_title: str, source_title: str) -> float:
    """
    eBayタイトルと仕入先タイトルの類似度を計算する。
    型番一致を最重視し、共通キーワードも加味する（0.0〜1.0+）。
    日本語（漢字・カタカナ・ひらがな）と英語のローマ字表記を比較可能。

    例:
    - "Dedede De Pupupu" ↔ "デデデでプププ" → 高類似度
    - "Sabishii Hito ga Ichiban" ↔ "寂しい人が一番偉いんだ" → 高類似度
    - "Saso 50ml Eau de Parfum" ↔ "沙棗 SASO 50ml" → 高類似度

    ボーナス:
    - 型番一致: +0.3
    - 数量一致 (50ml等): +0.2
    - ブランド一致: +0.2
    """
    if not ebay_title or not source_title:
        return 0.0

    bonus = 0.0

    # 1. 型番抽出・一致チェック
    ebay_models = extract_model_numbers(ebay_title)
    source_models = extract_model_numbers(source_title)
    if ebay_models and source_models:
        common_models = ebay_models & source_models
        if common_models:
            bonus += 0.3 * len(common_models)

    # 2. 数量・容量一致チェック (50ml, 100g など)
    ebay_quantities = extract_quantities(ebay_title)
    source_quantities = extract_quantities(source_title)
    if ebay_quantities and source_quantities:
        common_quantities = ebay_quantities & source_quantities
        if common_quantities:
            bonus += 0.2 * len(common_quantities)

    # 3. ブランド名一致チェック
    ebay_lower = ebay_title.lower()
    source_lower = source_title.lower()
    for eng, jpn in BRAND_MAPPINGS.items():
        if eng in ebay_lower and jpn in source_title:
            bonus += 0.2
            break
        if jpn in ebay_title and eng in source_lower:
            bonus += 0.2
            break

    # 4. ローマ字に統一して単語比較
    def normalize_to_words(text: str) -> set:
        romaji_text = normalize_to_romaji(text)
        words = re.findall(r'[a-z0-9]+', romaji_text)
        return set(w for w in words if len(w) >= 2)

    def normalize_to_string(text: str) -> str:
        romaji_text = normalize_to_romaji(text)
        return re.sub(r'[^a-z0-9]', '', romaji_text)

    ebay_words = normalize_to_words(ebay_title)
    source_words = normalize_to_words(source_title)

    if not ebay_words or not source_words:
        return min(bonus, 1.5)

    # 直接の単語一致
    common = ebay_words & source_words

    # 部分文字列マッチング
    ebay_string = normalize_to_string(ebay_title)
    source_string = normalize_to_string(source_title)

    substring_matches = 0
    for word in ebay_words:
        if len(word) >= 3 and word in source_string and word not in common:
            substring_matches += 1
    for word in source_words:
        if len(word) >= 3 and word in ebay_string and word not in common:
            substring_matches += 1

    # 直接一致 + 部分文字列一致（重み0.7）
    total_matches = len(common) + substring_matches * 0.7

    # 基本類似度
    base_similarity = total_matches / min(len(ebay_words), len(source_words))

    return min(base_similarity + bonus, 1.5)


# 許可する日本国内ECサイト（ホワイトリスト方式）
# 海外サイトは全て除外
ALLOWED_JAPANESE_DOMAINS = [
    # 大手EC
    "amazon.co.jp",
    "rakuten.co.jp",
    "shopping.yahoo.co.jp",
    # 家電量販店
    "yodobashi.com",
    "biccamera.com",
    "joshin.co.jp",
    "nojima.co.jp",
    "edion.com",
    "ksdenki.com",
    "yamada-denki",
    "kojima.net",
    # ホビー・カード
    "suruga-ya.jp",
    "amiami.jp",
    "hobby-wave",
    "goodsmile",
    "cardotaku",
    # 工具・DIY
    "komeri.com",
    "cainz.com",
    "kohnan-eshop",
    "dcm-ekurashi",
    "monotaro.com",
    "hikoki-powertools.jp",
    "makita-shop",
    # その他
    "askul.co.jp",
    "lohaco.jp",
]

# 除外すべきURL・ドメインパターン（海外サイト全般）
EXCLUDED_URL_PATTERNS = [
    ".pdf",           # PDFファイル
    "/pdf/",
    # 海外Amazon
    "amazon.com",     # アメリカ
    "amazon.co.uk",   # イギリス
    "amazon.de",      # ドイツ
    "amazon.fr",      # フランス
    "amazon.it",      # イタリア
    "amazon.es",      # スペイン
    "amazon.nl",      # オランダ
    "amazon.ca",      # カナダ
    "amazon.com.au",  # オーストラリア
    # eBay（仕入先にならない）
    "ebay.com",
    "ebay.co.uk",
    "ebay.de",
    # 海外小売
    "walmart.com",
    "target.com",
    "bestbuy.com",
    "wish.com",
    # 中国系
    "aliexpress",
    "alibaba",
    "shein.com",
    "temu.com",
    # 海外TLD（日本以外）
    ".ch/",           # スイス
    ".de/",           # ドイツ
    ".nl/",           # オランダ
    ".fr/",           # フランス
    ".it/",           # イタリア
    ".es/",           # スペイン
    ".co.uk/",        # イギリス
    ".com.au/",       # オーストラリア
    # 検索一覧ページ（個別商品ページではない）
    "search.rakuten.co.jp/search/",
    "search.yahoo.co.jp",
    "/search?q=",
    "/search?",
    "/s?k=",          # Amazon検索
    "/item/?",        # Bandai一覧ページ
    "/products?",     # 商品一覧
    "/category/",     # カテゴリページ
]


def is_allowed_source_url(url: str) -> bool:
    """
    URLが日本国内の仕入先かどうか判定する.

    許可されるサイト:
    - ALLOWED_JAPANESE_DOMAINSに含まれるサイト
    - .jpドメイン（未知の日本サイト）

    除外されるサイト:
    - 海外Amazon、AliExpress、Shein等の海外サイト
    - PDF、eBay等

    Returns:
        True: 許可（日本国内サイト）
        False: 除外（海外サイト）
    """
    if not url:
        return False

    url_lower = url.lower()

    # 除外パターンに一致したらNG
    for pattern in EXCLUDED_URL_PATTERNS:
        if pattern in url_lower:
            return False

    # 許可リスト（日本国内）に一致したらOK
    for domain in ALLOWED_JAPANESE_DOMAINS:
        if domain in url_lower:
            return True

    # どちらにも一致しない場合：
    # .jpドメインなら許可（未知の日本サイト）
    if ".co.jp" in url_lower or ".jp/" in url_lower:
        return True

    return False


# 仕入れ優先サイト（再現性が高い、在庫がある）
SOURCING_PRIORITY_SITES = [
    "amazon",
    "楽天",
    "rakuten",
    "駿河屋",
    "suruga",
    "ヨドバシ",
    "yodobashi",
    "ビックカメラ",
    "biccamera",
    "cardotaku",
    "カードショップ",
]

# 相場参考サイト（売り切れが多い、参考価格）
# ※New条件では除外対象
MARKET_REFERENCE_SITES = [
    "メルカリ",
    "mercari",
    "ヤフオク",
    "yahoo",
    "paypay",
    "ラクマ",
    "fril",
    "magi",
]


def is_valid_source_for_condition(source_site: str, source_url: str, condition: str) -> bool:
    """
    条件（New/Used）に基づいて仕入先が有効かどうか判定する.

    New条件の場合:
      - メルカリ、ヤフオク、PayPayフリマ等の中古系サイトは除外
      - Amazon、楽天、ヨドバシ等の新品系サイトのみ許可

    Used条件の場合:
      - 全サイト許可

    Args:
        source_site: サイト名（「メルカリ」「Amazon」等）
        source_url: サイトURL
        condition: "New" or "Used"

    Returns:
        True: 有効な仕入先, False: 除外
    """
    if condition != "New":
        return True  # Used条件は全て許可

    # New条件の場合、中古系サイトを除外
    site_lower = source_site.lower()
    url_lower = source_url.lower()

    # 中古系サイトのパターン
    used_patterns = [
        "mercari", "メルカリ",
        "yahoo", "ヤフオク", "paypay",
        "fril", "ラクマ",
        "magi",
        "2ndstreet", "セカスト",
    ]

    for pattern in used_patterns:
        if pattern in site_lower or pattern in url_lower:
            return False

    return True


def calculate_condition_score(title: str, source_site: str) -> float:
    """
    国内商品のconditionスコアを計算する.
    新品/未開封は加点、中古/難ありは減点.

    Returns:
        スコア（0.0〜2.0、1.0が基準）
    """
    title_lower = title.lower()
    score = 1.0

    # 新品/未開封系キーワード（加点）
    new_keywords = ["未開封", "新品", "sealed", "新品未開封", "未使用", "brand new", "factory sealed"]
    if any(kw in title_lower for kw in new_keywords):
        score += 0.3

    # 中古系キーワード（減点、ただし即除外しない）
    used_keywords = ["中古", "used", "難あり", "ジャンク", "訳あり", "傷あり", "プレイ用"]
    if any(kw in title_lower for kw in used_keywords):
        score -= 0.3

    # 状態良好系（軽い加点）
    good_condition = ["美品", "極美品", "良品", "mint", "excellent", "near mint"]
    if any(kw in title_lower for kw in good_condition):
        score += 0.1

    return max(0.1, min(2.0, score))


def calculate_source_priority(source_site: str) -> float:
    """
    仕入先の優先度を計算する.
    仕入れ可能なサイトは高スコア、相場参考サイトは低スコア.

    Returns:
        優先度スコア（0.0〜1.0）
    """
    site_lower = source_site.lower()

    # 仕入れ優先サイト
    for site in SOURCING_PRIORITY_SITES:
        if site.lower() in site_lower:
            return 1.0

    # 相場参考サイト
    for site in MARKET_REFERENCE_SITES:
        if site.lower() in site_lower:
            return 0.5

    return 0.7  # 不明なサイトは中間


@dataclass
class RankedSource:
    """スコア付き仕入先."""
    source: SourceOffer
    score: float
    similarity: float
    condition_score: float
    priority: float


def find_top_matching_sources(
    ebay_title: str,
    sources: List[SourceOffer],
    min_similarity: float = 0.2,
    prefer_sourcing: bool = True,
    require_price: bool = True,
    top_n: int = 3
) -> List[RankedSource]:
    """
    eBayタイトルにマッチする仕入先をスコア順に最大N件返す.
    類似度 × conditionスコア × 仕入れ優先度 で総合評価.

    Args:
        ebay_title: eBayの商品タイトル
        sources: 仕入先候補リスト
        min_similarity: 最低類似度
        prefer_sourcing: 仕入れ可能サイトを優先するか
        require_price: 価格が必須かどうか（Trueなら価格0円は除外）
        top_n: 返す件数（デフォルト3）

    Returns:
        RankedSourceのリスト（スコア降順）
    """
    if not sources:
        return []

    ranked_sources: List[RankedSource] = []
    excluded_urls = 0
    seen_urls = set()  # 重複URL除外用

    MAJOR_EC_DOMAINS = ["amazon.co.jp", "rakuten.co.jp", "shopping.yahoo.co.jp"]

    for source in sources:
        # 許可されていないURL（海外Amazon、PDF等）は除外
        if not is_allowed_source_url(source.source_url):
            excluded_urls += 1
            continue

        # 重複URL除外（同じ商品が複数回出てくることがある）
        if source.source_url in seen_urls:
            continue
        seen_urls.add(source.source_url)

        # 価格0円の処理
        # 大手ECサイトは価格0でも候補に含める（後で手動確認）
        is_major_ec = any(domain in source.source_url for domain in MAJOR_EC_DOMAINS)
        if require_price and source.source_price_jpy <= 0 and not is_major_ec:
            continue

        # 類似度（型番一致ボーナス込み）
        similarity = calculate_title_similarity(ebay_title, source.title)
        if similarity < min_similarity:
            continue

        # conditionスコア
        condition_score = calculate_condition_score(source.title, source.source_site)

        # 仕入れ優先度
        priority = calculate_source_priority(source.source_site) if prefer_sourcing else 1.0

        # 総合スコア = 類似度 × conditionスコア × 優先度
        total_score = similarity * condition_score * priority

        ranked_sources.append(RankedSource(
            source=source,
            score=total_score,
            similarity=similarity,
            condition_score=condition_score,
            priority=priority
        ))

    if excluded_urls > 0:
        print(f"    (海外/PDF除外: {excluded_urls}件)")

    # スコア降順でソートして上位N件を返す
    ranked_sources.sort(key=lambda x: x.score, reverse=True)
    return ranked_sources[:top_n]


def find_best_matching_source(
    ebay_title: str,
    sources: List[SourceOffer],
    min_similarity: float = 0.2,
    prefer_sourcing: bool = True,
    require_price: bool = True
) -> Optional[SourceOffer]:
    """
    eBayタイトルに最もマッチする仕入先を見つける（後方互換用）.

    Returns:
        最適な仕入先、見つからない場合はNone
    """
    top_sources = find_top_matching_sources(
        ebay_title, sources, min_similarity, prefer_sourcing, require_price, top_n=1
    )
    return top_sources[0].source if top_sources else None


def get_processed_ebay_ids(sheet_client) -> set:
    """
    スプレッドシートから処理済みeBay商品IDを取得する.
    eBayリンク列（N列）からitem IDを抽出.
    """
    try:
        worksheet = sheet_client.spreadsheet.worksheet("入力シート")
        # N列（eBayリンク）を取得
        ebay_urls = worksheet.col_values(14)  # N列 = 14番目

        processed_ids = set()
        for url in ebay_urls[1:]:  # ヘッダーをスキップ
            if not url:
                continue
            # URLからitem IDを抽出
            match = re.search(r'/itm/(\d+)', url)
            if match:
                processed_ids.add(match.group(1))

        return processed_ids
    except Exception as e:
        print(f"  [WARN] Failed to get processed IDs: {e}")
        return set()


def get_next_empty_row(sheet_client) -> int:
    """Get the next empty row number in the input sheet."""
    worksheet = sheet_client.spreadsheet.worksheet("入力シート")
    # Get all values in column A (date column)
    col_a_values = worksheet.col_values(1)
    # Return the next row after the last non-empty row (1-indexed)
    return len(col_a_values) + 1


def write_result_to_spreadsheet(sheet_client, data: dict):
    """Write research results to the next empty row in spreadsheet."""
    worksheet = sheet_client.spreadsheet.worksheet("入力シート")
    row_number = get_next_empty_row(sheet_client)

    # Prepare row data matching INPUT_SHEET_COLUMNS
    row_data = [""] * len(INPUT_SHEET_COLUMNS)

    # Map data to columns (19 columns: A-S)
    row_data[0] = datetime.now().strftime("%Y-%m-%d")  # 日付
    row_data[1] = data.get("keyword", "")  # キーワード
    row_data[2] = data.get("category_name", "")  # カテゴリ
    row_data[3] = data.get("category_id", "")  # カテゴリ番号

    # ソーシング結果（国内最安①②③）- 商品名、リンク、価格
    sourcing_results = data.get("sourcing_results", [])
    for idx, result in enumerate(sourcing_results[:3]):
        title_col = 4 + (idx * 3)  # 4, 7, 10 (商品名)
        url_col = 5 + (idx * 3)    # 5, 8, 11 (リンク)
        price_col = 6 + (idx * 3)  # 6, 9, 12 (価格)
        row_data[title_col] = result.get("title", "")
        row_data[url_col] = result.get("url", "")
        row_data[price_col] = str(result.get("price", "")) if result.get("price", 0) > 0 else ""

    # eBay情報
    row_data[13] = data.get("ebay_url", "")  # eBayリンク (N列)
    row_data[14] = str(data.get("ebay_price", ""))  # 販売価格（米ドル）(O列)
    row_data[15] = str(data.get("ebay_shipping", ""))  # 販売送料（米ドル）(P列)

    # 利益計算結果
    row_data[16] = str(data.get("profit_no_rebate", ""))  # 還付抜き利益額（円）(Q列)
    row_data[17] = str(data.get("profit_margin_no_rebate", ""))  # 利益率%（還付抜き）(R列)
    row_data[18] = str(data.get("profit_with_rebate", ""))  # 還付あり利益額（円）(S列)
    row_data[19] = str(data.get("profit_margin_with_rebate", ""))  # 利益率%（還付あり）(T列)

    # ステータスとメモ
    if data.get("error"):
        row_data[20] = "エラー"  # ステータス (U列)
        row_data[21] = f"ERROR: {data.get('error')}"  # メモ (V列)
    else:
        row_data[20] = "要確認"  # ステータス (U列)
        row_data[21] = f"自動処理 {datetime.now().strftime('%H:%M:%S')}"  # メモ (V列)

    # Write to specific row (A〜V列：22列)
    cell_range = f"A{row_number}:V{row_number}"
    worksheet.update(range_name=cell_range, values=[row_data])

    print(f"  [WRITE] Written to row {row_number}")
    return row_number


def main():
    parser = argparse.ArgumentParser(description="eBay Auto Research Pipeline (Pattern②)")
    args = parser.parse_args()

    print(f"="*60)
    print(f"eBay AUTO RESEARCH PIPELINE (Pattern②)")
    print(f"="*60)

    # SerpAPI使用履歴を追跡
    serpapi_usage_log = []

    def log_serpapi_call(search_type: str, query: str, results_count: int):
        """SerpAPI呼び出しを記録"""
        serpapi_usage_log.append({
            "type": search_type,
            "query": query[:50] + "..." if len(query) > 50 else query,
            "results": results_count
        })

    # Load environment
    load_dotenv()

    # Initialize clients
    ebay_client = EbayClient()
    sourcing_client = SourcingClient()
    configs = load_all_configs()

    # For Google Sheets, handle both file path and JSON content
    service_account_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if service_account_json:
        if service_account_json.strip().startswith("{"):
            # It's JSON content (GitHub Actions)
            import tempfile
            temp_dir = tempfile.gettempdir()
            temp_sa_file = Path(temp_dir) / "service_account.json"
            temp_sa_file.write_text(service_account_json)
            service_account_path = str(temp_sa_file)
        else:
            # It's a file path (Local development)
            service_account_path = service_account_json
    else:
        service_account_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON_PATH",
                                        "ebaysystem-837d6cedefa5.json")

    sheets_client = GoogleSheetsClient(
        service_account_file=service_account_path,
        spreadsheet_url=os.getenv("SHEETS_SPREADSHEET_ID")
    )

    # Initialize search base client for profit calculation
    search_base_client = SearchBaseClient(sheets_client)

    # Step 1: Read settings and keywords from '設定＆キーワード' sheet
    print(f"\n[1/6] Reading settings from '設定＆キーワード' sheet...")
    settings = sheets_client.read_settings()

    market = settings.get("market", "UK")
    min_price_str = settings.get("min_price", "100")
    min_profit_str = settings.get("min_profit", "フィルターなし")
    items_per_keyword_str = settings.get("items_per_keyword", "5")
    min_sold_str = settings.get("min_sold", "0")
    condition = settings.get("condition", "New")  # New or Used

    # Weight settings
    default_weight = settings.get("default_weight", "自動推定")
    packaging_weight_str = settings.get("packaging_weight", "500")
    size_multiplier_str = settings.get("size_multiplier", "1.0")

    # Parse values
    min_price_usd = float(min_price_str) if min_price_str.replace(".", "").isdigit() else 100.0
    items_per_keyword = int(items_per_keyword_str) if items_per_keyword_str.isdigit() else 5
    items_per_keyword = max(1, min(10, items_per_keyword))  # Clamp to 1-10
    min_sold = int(min_sold_str) if min_sold_str.isdigit() else 0
    packaging_weight_g = int(packaging_weight_str) if packaging_weight_str.isdigit() else 500
    size_multiplier = float(size_multiplier_str) if size_multiplier_str else 1.0

    print(f"  [INFO] Market: {market}")
    print(f"  [INFO] Min price: ${min_price_usd}")
    print(f"  [INFO] Items per keyword: {items_per_keyword}")
    print(f"  [INFO] Min sold: {min_sold}" if min_sold > 0 else "  [INFO] Min sold: (disabled)")

    # Parse min_profit (handle "フィルターなし" = no filter)
    if min_profit_str == "フィルターなし" or not min_profit_str:
        min_profit_jpy = None  # No filter
        print(f"  [INFO] Min profit: フィルターなし（全件出力）")
    else:
        min_profit_jpy = int(min_profit_str.replace("円", "").replace(",", ""))
        print(f"  [INFO] Min profit: JPY {min_profit_jpy}")

    print(f"  [INFO] Condition: {condition}")
    print(f"  [INFO] Weight: {default_weight}, Packaging: {packaging_weight_g}g, Size: x{size_multiplier}")

    print(f"\n[2/6] Reading keywords from '設定＆キーワード' sheet...")
    keywords = sheets_client.read_keywords_from_settings()

    if not keywords:
        print(f"  [ERROR] No keywords found in '設定＆キーワード' sheet!")
        sys.exit(1)

    print(f"  [INFO] Keywords: {', '.join(keywords)}")

    # 処理済みeBay商品IDを取得（重複スキップ用）
    print(f"\n[2.5/6] Loading processed eBay item IDs...")
    processed_ebay_ids = get_processed_ebay_ids(sheets_client)
    print(f"  [INFO] Found {len(processed_ebay_ids)} already processed items")

    # Step 2-5: Process each keyword
    total_processed = 0
    total_profitable = 0
    total_skipped = 0  # スキップ数カウント

    for raw_keyword in keywords:
        # キーワードをクリーニング（日本語・括弧を除去）
        keyword = clean_keyword_for_ebay(raw_keyword)
        if not keyword:
            print(f"\n[SKIP] Empty keyword after cleaning: '{raw_keyword}'")
            continue

        print(f"\n{'='*60}")
        if keyword != raw_keyword:
            print(f"Processing keyword: {raw_keyword} → {keyword}")
        else:
            print(f"Processing keyword: {keyword}")
        print(f"{'='*60}")

        # Step 3: Search eBay sold items
        # Priority: SerpApi (sold items) > Browse API (active listings)
        print(f"\n[3/5] Searching eBay sold items (${min_price_usd}+)...")

        # Currency conversion for local price filter
        currency_rates = {"UK": 0.79, "US": 1.0, "EU": 0.90}  # USD to local (conservative)
        local_rate = currency_rates.get(market, 0.79)
        min_price_local = min_price_usd * local_rate

        # Item location filter: "japan" by default (only items shipped from Japan)
        # This dramatically improves matching rate with domestic sources
        item_location = settings.get("item_location", "japan")

        # Try SerpApi first (sold items - past completed listings)
        serpapi_client = SerpApiClient()
        sold_items = []

        if serpapi_client.is_enabled:
            # Map condition to eBay condition filter
            ebay_condition = "new" if condition == "New" else "any"
            print(f"  [eBay] Location filter: {item_location}, Condition: {ebay_condition}")

            serpapi_results = serpapi_client.search_sold_items(
                keyword,
                market=market,
                min_price=min_price_local,
                max_results=items_per_keyword * 2,
                item_location=item_location,
                condition=ebay_condition
            )
            log_serpapi_call("eBay Sold", keyword, len(serpapi_results))

            # Convert SerpApi results to ListingCandidate format
            for sold_item in serpapi_results:
                # Convert price to USD
                usd_rates = {"GBP": 1.27, "EUR": 1.09, "USD": 1.0}
                usd_rate = usd_rates.get(sold_item.currency, 1.0)
                price_usd = sold_item.price * usd_rate

                sold_items.append(ListingCandidate(
                    candidate_id=sold_item.item_id,
                    search_query=keyword,
                    ebay_item_url=sold_item.link,
                    ebay_price=price_usd,
                    ebay_shipping=0.0,  # SerpApi doesn't provide shipping cost separately
                    sold_signal=1,  # It's a sold item
                    ebay_title=sold_item.title,
                    currency=sold_item.currency,
                    image_url=sold_item.thumbnail,  # サムネイル画像（Google Lens検索用）
                    category_id=sold_item.category_id,
                    category_name=sold_item.category_name,
                ))

        if sold_items:
            print(f"  [INFO] Using SerpApi (past sold items)")
            active_items = sold_items
        else:
            # Fall back to Browse API (active listings)
            print(f"  [INFO] Falling back to Browse API (active listings)")
            active_items = ebay_client.search_active_listings(
                keyword, market=market, min_price_usd=min_price_usd, min_sold=min_sold
            )

        if not active_items:
            print(f"  [WARN] No eBay listings found for '{keyword}'")
            continue

        # 余裕を持って取得（スキップされる分を考慮）
        # 実際に処理する件数は items_per_keyword まで
        items_processed_this_keyword = 0

        for item in active_items:
            # 指定件数に達したら終了
            if items_processed_this_keyword >= items_per_keyword:
                break
            ebay_url = item.ebay_item_url
            ebay_price = item.ebay_price
            ebay_shipping = item.ebay_shipping
            ebay_title = getattr(item, 'ebay_title', '') or ''
            item_currency = getattr(item, 'currency', 'USD')
            category_id = getattr(item, 'category_id', '') or ''
            category_name = getattr(item, 'category_name', '') or ''

            # eBay item IDを抽出
            item_id_match = re.search(r'/itm/(\d+)', ebay_url)
            ebay_item_id = item_id_match.group(1) if item_id_match else ""

            # カテゴリが取得できていない場合、Browse APIから取得
            if ebay_item_id and not category_id:
                try:
                    fetched_cat_id, fetched_cat_name = ebay_client.get_item_category(ebay_item_id, market)
                    if fetched_cat_id:
                        category_id = fetched_cat_id
                        category_name = fetched_cat_name
                        print(f"  [INFO] Category fetched from Browse API: {category_name} ({category_id})")
                except Exception as e:
                    print(f"  [WARN] Could not fetch category: {e}")

            # 処理済みならスキップ
            if ebay_item_id and ebay_item_id in processed_ebay_ids:
                print(f"\n  [SKIP] Already processed: {ebay_item_id}")
                total_skipped += 1
                continue

            # 画像URLも取得
            image_url = getattr(item, 'image_url', '') or ''

            print(f"\n  Processing: {ebay_url}")
            print(f"  [INFO] eBay title: {ebay_title[:60]}..." if len(ebay_title) > 60 else f"  [INFO] eBay title: {ebay_title}")
            print(f"  [INFO] eBay price: ${ebay_price:.2f} ({item_currency}) + ${ebay_shipping} shipping")
            if category_name:
                print(f"  [INFO] Category: {category_name} ({category_id})")
            if image_url:
                print(f"  [INFO] Image: {image_url[:60]}...")

            # Step 4: Search domestic sources (3段階検索 - 日本国内サイト限定)
            # 1. Google Lens画像検索（最も精度が高い）
            # 2. 英語のままでGoogle Shopping検索（日本向け）
            # 3. 日本語に翻訳してGoogle Shopping検索

            print(f"\n[4/5] Searching domestic sources...")

            all_sources = []
            best_source = None
            top_sources: List[RankedSource] = []  # トップ3の仕入先
            search_method = ""

            # === 1. Google Lens画像検索 ===
            if serpapi_client.is_enabled and image_url and not best_source:
                print(f"  [Step 1] Google Lens画像検索")
                print(f"    Image URL: {image_url[:80]}...")
                # conditionをserpapi側に渡す（フリマ除外はserpapi側で実行）
                serpapi_condition = "new" if condition == "New" else "any"
                print(f"    Condition: {serpapi_condition} → {'フリマ除外' if serpapi_condition == 'new' else '全サイト対象'}")
                image_results = serpapi_client.search_by_image(image_url, condition=serpapi_condition, max_results=10)
                log_serpapi_call("Google Lens", f"[IMAGE] {ebay_title[:30]}", len(image_results))

                # ShoppingItemをSourceOfferに変換
                # フィルタリング: 価格0円、New条件でのフリマ系サイトを除外
                # ただし大手ECサイト（Amazon/楽天/Yahoo）は価格0でも含める
                MAJOR_EC_DOMAINS = ["amazon.co.jp", "rakuten.co.jp", "shopping.yahoo.co.jp"]
                no_url_count = 0
                no_price_count = 0
                major_ec_no_price = 0
                condition_skipped = 0
                for shop_item in image_results:
                    if not shop_item.link:
                        no_url_count += 1
                        continue
                    # 大手ECサイトかどうか判定
                    is_major_ec = any(domain in shop_item.link for domain in MAJOR_EC_DOMAINS)
                    if shop_item.price <= 0:
                        if is_major_ec:
                            # 大手ECは価格0でも含める（後で手動確認）
                            major_ec_no_price += 1
                        else:
                            no_price_count += 1
                            continue
                    # New条件でフリマ系サイトを除外
                    if not is_valid_source_for_condition(shop_item.source, shop_item.link, condition):
                        condition_skipped += 1
                        continue
                    all_sources.append(SourceOffer(
                        source_site=shop_item.source,
                        source_url=encode_url_with_japanese(shop_item.link),
                        source_price_jpy=shop_item.price,
                        source_shipping_jpy=0,
                        stock_hint="画像検索",
                        title=shop_item.title,
                    ))

                # 詳細ログ
                print(f"    結果: {len(image_results)}件取得 → {len(all_sources)}件有効")
                if major_ec_no_price > 0:
                    print(f"    (URL無し: {no_url_count}, 価格0円: {no_price_count}, 大手EC価格なし: {major_ec_no_price}, フリマ除外: {condition_skipped})")
                else:
                    print(f"    (URL無し: {no_url_count}, 価格0円: {no_price_count}, フリマ除外: {condition_skipped})")

                if all_sources:
                    # 候補一覧を表示（類似度付き）
                    print(f"    --- 候補一覧 (スコア順) ---")
                    scored_sources = []
                    for src in all_sources:
                        sim = calculate_title_similarity(ebay_title, src.title)
                        cond_score = calculate_condition_score(src.title, src.source_site)
                        prio = calculate_source_priority(src.source_site)
                        total = sim * cond_score * prio
                        scored_sources.append((src, sim, cond_score, prio, total))
                    scored_sources.sort(key=lambda x: x[4], reverse=True)

                    for i, (src, sim, cond_score, prio, total) in enumerate(scored_sources[:5]):
                        prio_label = "仕入" if prio >= 0.9 else "相場" if prio <= 0.6 else "中"
                        print(f"    {i+1}. [{src.source_site}] JPY {src.source_price_jpy:,.0f}")
                        print(f"       類似度:{sim:.0%} × 状態:{cond_score:.1f} × 優先:{prio:.1f}({prio_label}) = {total:.2f}")
                        print(f"       {src.title[:50]}...")

                    # 画像検索でも類似度チェック（誤爆防止、ただし閾値は低め）
                    # 画像一致でも全く関係ない商品の場合があるため
                    MIN_IMAGE_SIMILARITY = 0.15  # 画像検索は閾値を低めに
                    valid_sources = [(src, sim, total) for src, sim, _, _, total in scored_sources if sim >= MIN_IMAGE_SIMILARITY]

                    if valid_sources:
                        # スコア順にソートして上位3件を取得
                        valid_sources.sort(key=lambda x: x[2], reverse=True)
                        for src, sim, total in valid_sources[:3]:
                            cond_score = calculate_condition_score(src.title, src.source_site)
                            prio = calculate_source_priority(src.source_site)
                            top_sources.append(RankedSource(
                                source=src, score=total, similarity=sim,
                                condition_score=cond_score, priority=prio
                            ))
                        best_source = top_sources[0].source
                        best_sim = top_sources[0].similarity
                        print(f"    → 選択: [{best_source.source_site}] JPY {best_source.source_price_jpy:,.0f} (類似度:{best_sim:.0%}, 計{len(top_sources)}件)")
                        search_method = "画像検索"
                    else:
                        print(f"    → 類似度閾値({MIN_IMAGE_SIMILARITY:.0%})未満のため選択なし、次のステップへ")
                else:
                    print(f"    → 有効な仕入先なし、次のステップへ")

            # === 2. 英語のままでGoogle Shopping検索（日本向け）===
            if not top_sources and serpapi_client.is_enabled and ebay_title:
                # クエリを整形（PSA番号、状態表記、ノイズワード除去）
                cleaned_query = clean_query_for_shopping(ebay_title)
                print(f"  [Step 2] Google Shopping検索 (英語/日本向け)")
                print(f"    元クエリ: {ebay_title[:60]}...")
                print(f"    整形後: {cleaned_query[:60]}...")
                print(f"    Condition: {condition} → {'新品系サイトのみ' if condition == 'New' else '全サイト対象'}")

                shopping_results = serpapi_client.search_google_shopping_jp(cleaned_query, max_results=10)
                log_serpapi_call("Shopping(EN)", cleaned_query, len(shopping_results))

                all_sources = []
                google_url_skipped = 0
                no_price_skipped = 0
                major_ec_no_price = 0
                condition_skipped = 0
                MAJOR_EC_DOMAINS = ["amazon.co.jp", "rakuten.co.jp", "shopping.yahoo.co.jp"]
                for shop_item in shopping_results:
                    # google.comのURLはスキップ（実際の商品ページではない）
                    if "google.com" in shop_item.link:
                        google_url_skipped += 1
                        continue
                    # New条件でフリマ系サイトを除外
                    if not is_valid_source_for_condition(shop_item.source, shop_item.link, condition):
                        condition_skipped += 1
                        continue

                    is_major_ec = any(domain in shop_item.link for domain in MAJOR_EC_DOMAINS)
                    if shop_item.price > 0:
                        price_jpy = shop_item.price
                        if shop_item.currency == "USD":
                            price_jpy = shop_item.price * 150

                        all_sources.append(SourceOffer(
                            source_site=shop_item.source,
                            source_url=encode_url_with_japanese(shop_item.link),
                            source_price_jpy=price_jpy,
                            source_shipping_jpy=0,
                            stock_hint="",
                            title=shop_item.title,
                        ))
                    elif is_major_ec:
                        major_ec_no_price += 1
                        all_sources.append(SourceOffer(
                            source_site=shop_item.source,
                            source_url=encode_url_with_japanese(shop_item.link),
                            source_price_jpy=0,
                            source_shipping_jpy=0,
                            stock_hint="要価格確認",
                            title=shop_item.title,
                        ))
                    else:
                        no_price_skipped += 1

                print(f"    結果: {len(shopping_results)}件取得 → {len(all_sources)}件有効")
                if major_ec_no_price > 0:
                    print(f"    (google.com: {google_url_skipped}, 価格無し: {no_price_skipped}, 大手EC価格なし: {major_ec_no_price}, フリマ除外: {condition_skipped})")
                else:
                    print(f"    (google.com: {google_url_skipped}, 価格無し: {no_price_skipped}, フリマ除外: {condition_skipped})")

                # Shoppingが0件の場合、Web検索へフォールバック
                if not all_sources:
                    print(f"  [Step 2b] Shopping結果なし → Web検索フォールバック")
                    # conditionに応じて検索対象サイトを変更
                    web_condition = "new" if condition == "New" else "used"
                    print(f"    condition: {web_condition} → 対象サイト: {'新品系' if web_condition == 'new' else '新品+中古系'}")
                    web_results = serpapi_client.search_google_web_jp(cleaned_query, condition=web_condition, max_results=10)
                    log_serpapi_call("Web(EN)", cleaned_query, len(web_results))

                    for shop_item in web_results:
                        # Web検索は価格が取れないことが多いので、URLのみ記録
                        all_sources.append(SourceOffer(
                            source_site=shop_item.source,
                            source_url=encode_url_with_japanese(shop_item.link),
                            source_price_jpy=shop_item.price if shop_item.price > 0 else 0,
                            source_shipping_jpy=0,
                            stock_hint="Web検索",
                            title=shop_item.title,
                        ))

                    priced = len([s for s in all_sources if s.source_price_jpy > 0])
                    print(f"    結果: {len(web_results)}件取得 → {len(all_sources)}件有効 (価格あり: {priced}件)")

                if all_sources:
                    # 候補一覧を表示（スコア付き）
                    print(f"    --- 候補一覧 (スコア順) ---")
                    scored_sources = []
                    for src in all_sources:
                        sim = calculate_title_similarity(ebay_title, src.title)
                        cond = calculate_condition_score(src.title, src.source_site)
                        prio = calculate_source_priority(src.source_site)
                        total = sim * cond * prio
                        scored_sources.append((src, sim, cond, prio, total))
                    scored_sources.sort(key=lambda x: x[4], reverse=True)

                    for i, (src, sim, cond, prio, total) in enumerate(scored_sources[:5]):
                        price_str = f"JPY {src.source_price_jpy:,.0f}" if src.source_price_jpy > 0 else "価格不明"
                        prio_label = "仕入" if prio >= 0.9 else "相場" if prio <= 0.6 else "中"
                        print(f"    {i+1}. [{src.source_site}] {price_str}")
                        print(f"       類似度:{sim:.0%} × 状態:{cond:.1f} × 優先:{prio:.1f}({prio_label}) = {total:.2f}")
                        print(f"       {src.title[:50]}...")

                    top_sources = find_top_matching_sources(ebay_title, all_sources, min_similarity=0.2, top_n=3)
                    if top_sources:
                        best_source = top_sources[0].source
                        search_method = "英語検索"
                        print(f"    → 選択: [{best_source.source_site}] (計{len(top_sources)}件)")
                    else:
                        print(f"    → 類似度閾値(20%)未満のため選択なし")

            # === 3. 日本語に翻訳してGoogle Shopping検索 ===
            if not top_sources and serpapi_client.is_enabled:
                print(f"  [Step 3] Google Shopping検索 (日本語翻訳)")

                # まずタイトルを整形してからGeminiで翻訳（ノイズを減らす）
                cleaned_title = clean_query_for_shopping(ebay_title) if ebay_title else ""
                print(f"    整形後タイトル: {cleaned_title[:60]}...")

                # Geminiで翻訳
                gemini_client = GeminiClient()
                japanese_query = None
                if gemini_client.is_enabled and cleaned_title:
                    japanese_query = gemini_client.translate_product_name(cleaned_title)
                    if japanese_query:
                        print(f"    Gemini翻訳結果: {japanese_query}")
                    else:
                        print(f"    Gemini翻訳失敗")
                else:
                    print(f"    Gemini無効 (APIキー未設定?)")

                # 翻訳失敗時は型番抽出
                if not japanese_query:
                    japanese_query = extract_search_keywords(ebay_title) if ebay_title else keyword
                    print(f"    フォールバック（型番抽出）: {japanese_query}")

                # 日本語で検索
                print(f"    検索クエリ: {japanese_query}")
                print(f"    Condition: {condition} → {'新品系サイトのみ' if condition == 'New' else '全サイト対象'}")
                shopping_results = serpapi_client.search_google_shopping_jp(japanese_query, max_results=10)
                log_serpapi_call("Shopping(JP)", japanese_query, len(shopping_results))

                all_sources = []
                google_url_skipped = 0
                no_price_skipped = 0
                major_ec_no_price = 0
                condition_skipped = 0
                MAJOR_EC_DOMAINS = ["amazon.co.jp", "rakuten.co.jp", "shopping.yahoo.co.jp"]
                for shop_item in shopping_results:
                    # google.comのURLはスキップ（実際の商品ページではない）
                    if "google.com" in shop_item.link:
                        google_url_skipped += 1
                        continue
                    # New条件でフリマ系サイトを除外
                    if not is_valid_source_for_condition(shop_item.source, shop_item.link, condition):
                        condition_skipped += 1
                        continue

                    is_major_ec = any(domain in shop_item.link for domain in MAJOR_EC_DOMAINS)
                    if shop_item.price > 0:
                        price_jpy = shop_item.price
                        if shop_item.currency == "USD":
                            price_jpy = shop_item.price * 150

                        all_sources.append(SourceOffer(
                            source_site=shop_item.source,
                            source_url=encode_url_with_japanese(shop_item.link),
                            source_price_jpy=price_jpy,
                            source_shipping_jpy=0,
                            stock_hint="",
                            title=shop_item.title,
                        ))
                    elif is_major_ec:
                        major_ec_no_price += 1
                        all_sources.append(SourceOffer(
                            source_site=shop_item.source,
                            source_url=encode_url_with_japanese(shop_item.link),
                            source_price_jpy=0,
                            source_shipping_jpy=0,
                            stock_hint="要価格確認",
                            title=shop_item.title,
                        ))
                    else:
                        no_price_skipped += 1

                print(f"    結果: {len(shopping_results)}件取得 → {len(all_sources)}件有効")
                if major_ec_no_price > 0:
                    print(f"    (google.com: {google_url_skipped}, 価格無し: {no_price_skipped}, 大手EC価格なし: {major_ec_no_price}, フリマ除外: {condition_skipped})")
                else:
                    print(f"    (google.com: {google_url_skipped}, 価格無し: {no_price_skipped}, フリマ除外: {condition_skipped})")

                # Shoppingが0件の場合、Web検索へフォールバック
                if not all_sources:
                    print(f"  [Step 3b] Shopping結果なし → Web検索フォールバック")
                    web_condition = "new" if condition == "New" else "used"
                    print(f"    condition: {web_condition}")
                    web_results = serpapi_client.search_google_web_jp(japanese_query, condition=web_condition, max_results=10)
                    log_serpapi_call("Web(JP)", japanese_query, len(web_results))

                    for shop_item in web_results:
                        all_sources.append(SourceOffer(
                            source_site=shop_item.source,
                            source_url=encode_url_with_japanese(shop_item.link),
                            source_price_jpy=shop_item.price if shop_item.price > 0 else 0,
                            source_shipping_jpy=0,
                            stock_hint="Web検索",
                            title=shop_item.title,
                        ))

                    priced = len([s for s in all_sources if s.source_price_jpy > 0])
                    print(f"    結果: {len(web_results)}件取得 → {len(all_sources)}件有効 (価格あり: {priced}件)")

                if all_sources:
                    # 候補一覧を表示（スコア付き）
                    print(f"    --- 候補一覧 (スコア順) ---")
                    scored_sources = []
                    for src in all_sources:
                        sim = calculate_title_similarity(ebay_title, src.title)
                        cond = calculate_condition_score(src.title, src.source_site)
                        prio = calculate_source_priority(src.source_site)
                        total = sim * cond * prio
                        scored_sources.append((src, sim, cond, prio, total))
                    scored_sources.sort(key=lambda x: x[4], reverse=True)

                    for i, (src, sim, cond, prio, total) in enumerate(scored_sources[:5]):
                        price_str = f"JPY {src.source_price_jpy:,.0f}" if src.source_price_jpy > 0 else "価格不明"
                        prio_label = "仕入" if prio >= 0.9 else "相場" if prio <= 0.6 else "中"
                        print(f"    {i+1}. [{src.source_site}] {price_str}")
                        print(f"       類似度:{sim:.0%} × 状態:{cond:.1f} × 優先:{prio:.1f}({prio_label}) = {total:.2f}")
                        print(f"       {src.title[:50]}...")

                    top_sources = find_top_matching_sources(ebay_title, all_sources, min_similarity=0.2, top_n=3)
                    if top_sources:
                        best_source = top_sources[0].source
                        search_method = "日本語検索"
                        print(f"    → 選択: [{best_source.source_site}] (計{len(top_sources)}件)")
                    else:
                        print(f"    → 類似度閾値(20%)未満のため選択なし")

            # 結果判定
            error_reason = None

            if not top_sources:
                if not all_sources:
                    print(f"  [WARN] No domestic sources found")
                    error_reason = "国内仕入先なし"
                else:
                    print(f"  [WARN] No matching product found (title similarity too low)")
                    print(f"         eBay: {ebay_title[:50]}...")
                    print(f"         Best candidate: {all_sources[0].title[:50]}...")
                    error_reason = "類似商品なし"

            # トップ3の仕入先を処理（価格スクレイピング含む）
            total_source_price = 0
            similarity = 0.0
            needs_price_check = False  # 大手ECで価格なしの場合

            # 全ての候補に対してスクレイピングを試みる
            if top_sources:
                print(f"\n  [INFO] Processing top {len(top_sources)} sources...")
                for rank, ranked_src in enumerate(top_sources, 1):
                    src = ranked_src.source
                    src_price = src.source_price_jpy + src.source_shipping_jpy

                    # 価格0の場合はスクレイピング
                    if src_price <= 0:
                        print(f"    [{rank}位] {src.source_site} - 価格0円、スクレイピング中...")
                        scraped = scrape_price_for_url(src.source_url)
                        if scraped.success and scraped.price > 0:
                            src.source_price_jpy = scraped.price
                            print(f"         → JPY {scraped.price:,.0f} (scraped)")
                        else:
                            print(f"         → 取得失敗: {scraped.error_message}")
                    else:
                        print(f"    [{rank}位] {src.source_site} - JPY {src_price:,.0f}")

                # 1位の情報を取得（利益計算用）
                best_source = top_sources[0].source
                total_source_price = best_source.source_price_jpy + best_source.source_shipping_jpy
                similarity = top_sources[0].similarity

                # 1位の価格がまだ0なら「要価格確認」
                if total_source_price <= 0:
                    needs_price_check = True
                    print(f"  [FOUND] via {search_method}: {best_source.source_site} - 要価格確認")
                else:
                    print(f"  [FOUND] via {search_method}: {best_source.source_site} - JPY {total_source_price:.0f}")

                print(f"  [INFO] Source title: {best_source.title[:50]}..." if len(best_source.title) > 50 else f"  [INFO] Source title: {best_source.title}")
                if search_method != "画像検索":
                    print(f"  [INFO] Title similarity: {similarity:.0%}")
                print(f"  [INFO] URL: {best_source.source_url}")

            # Step 5: Calculate profit (with weight estimation)
            profit_no_rebate = 0
            profit_margin_no_rebate = 0
            profit_with_rebate = 0
            profit_margin_with_rebate = 0

            # 仕入先がある場合のみ利益計算（価格確認必要な場合はスキップ）
            if best_source and needs_price_check:
                print(f"\n[5/5] Skipping profit calculation (price confirmation needed)")
                error_reason = "要価格確認"
            elif best_source:
                print(f"\n[5/5] Calculating profit...")

                # Estimate weight based on title and price (タイトルから商品タイプを判定)
                product_type = detect_product_type(ebay_title)
                weight_est = estimate_weight_from_title(ebay_title, ebay_price)

                # Apply size multiplier from settings
                adjusted_depth = weight_est.depth_cm * size_multiplier
                adjusted_width = weight_est.width_cm * size_multiplier
                adjusted_height = weight_est.height_cm * size_multiplier

                # 重量は推定値をそのまま使用（カテゴリ別上限が適用済み）
                adjusted_weight_g = weight_est.applied_weight_g

                print(f"  [INFO] Product type: {product_type}")
                print(f"  [INFO] Weight estimate: {adjusted_weight_g}g ({weight_est.estimation_basis})")
                print(f"  [INFO] Dimensions: {adjusted_depth:.1f}x{adjusted_width:.1f}x{adjusted_height:.1f}cm")

                try:
                    # Use search base client for accurate calculation
                    search_base_client.write_input_data(
                        source_price_jpy=total_source_price,
                        ebay_price_usd=ebay_price,
                        ebay_shipping_usd=ebay_shipping,
                        ebay_url=ebay_url,
                        weight_g=adjusted_weight_g,
                        depth_cm=adjusted_depth,
                        width_cm=adjusted_width,
                        height_cm=adjusted_height,
                        category_id=category_id
                    )

                    calc_result = search_base_client.read_calculation_results(max_wait_seconds=5)

                    if calc_result and calc_result.get("profit_no_rebate") is not None:
                        profit_no_rebate = calc_result["profit_no_rebate"]
                        profit_margin_no_rebate = calc_result.get("margin_no_rebate", 0)
                        profit_with_rebate = calc_result.get("profit_with_rebate", 0)
                        profit_margin_with_rebate = calc_result.get("margin_with_rebate", 0)
                    else:
                        # Fallback to Python calculation
                        profit_result = calculate_profit(
                            ebay_price=ebay_price,
                            ebay_shipping=ebay_shipping,
                            source_price_jpy=total_source_price,
                            fee_rules=configs.fee_rules
                        )
                        profit_no_rebate = profit_result.profit_jpy_no_rebate
                        profit_margin_no_rebate = profit_result.profit_margin_no_rebate
                        profit_with_rebate = profit_result.profit_jpy_with_rebate
                        profit_margin_with_rebate = profit_result.profit_margin_with_rebate

                except Exception as e:
                    print(f"  [ERROR] Profit calculation failed: {e}")
                    error_reason = f"計算エラー: {str(e)[:30]}"

                print(f"  [INFO] Profit (no rebate): JPY {profit_no_rebate:.0f} ({profit_margin_no_rebate:.1f}%)")

                # Check if profit meets minimum threshold
                if min_profit_jpy is not None and profit_no_rebate < min_profit_jpy:
                    print(f"  [SKIP] Profit JPY {profit_no_rebate:.0f} is below minimum JPY {min_profit_jpy} → スキップ")
                    items_processed_this_keyword += 1
                    continue  # スプレッドシートに書き込まずスキップ
            else:
                print(f"\n[5/5] Skipping profit calculation (no source found)")

            # Write to spreadsheet（利益がOKまたはエラー理由ありの場合のみ）
            # トップ3の仕入先を全てsourcing_resultsに追加（商品名含む）
            sourcing_results = []
            for ranked_src in top_sources:
                src = ranked_src.source
                src_price = src.source_price_jpy + src.source_shipping_jpy
                sourcing_results.append({
                    "title": src.title,  # 商品名
                    "url": src.source_url,
                    "price": src_price
                })

            result_data = {
                "keyword": keyword,
                "ebay_url": ebay_url,
                "ebay_price": ebay_price,
                "ebay_shipping": ebay_shipping,
                "sourcing_results": sourcing_results,
                "profit_no_rebate": profit_no_rebate,
                "profit_margin_no_rebate": profit_margin_no_rebate,
                "profit_with_rebate": profit_with_rebate,
                "profit_margin_with_rebate": profit_margin_with_rebate,
                "category_name": category_name,
                "category_id": category_id,
                "error": error_reason  # エラー理由（None or 文字列）
            }

            row_num = write_result_to_spreadsheet(sheets_client, result_data)
            total_processed += 1
            items_processed_this_keyword += 1  # このキーワードで処理した件数

            if profit_no_rebate > 0 and not error_reason:
                total_profitable += 1

            if error_reason:
                print(f"  [WRITTEN] Row {row_num} (Error: {error_reason})")
            else:
                print(f"  [SUCCESS] Result written to row {row_num}")

            # Rate limit protection: wait 2 seconds between items
            # Google Sheets API limit is 60 writes/minute
            import time
            time.sleep(2)

    # Summary
    print(f"\n{'='*60}")
    print(f"AUTO RESEARCH COMPLETED")
    print(f"{'='*60}")
    print(f"Total processed: {total_processed}")
    print(f"Skipped (already in sheet): {total_skipped}")
    print(f"Profitable items: {total_profitable}")

    # SerpAPI使用履歴サマリー
    if serpapi_usage_log:
        print(f"\n--- SerpAPI Usage Log ({len(serpapi_usage_log)} calls) ---")
        # タイプ別に集計
        type_counts = {}
        for log in serpapi_usage_log:
            t = log["type"]
            type_counts[t] = type_counts.get(t, 0) + 1

        for t, count in sorted(type_counts.items()):
            print(f"  {t}: {count} calls")

        print(f"\n  Details:")
        for i, log in enumerate(serpapi_usage_log, 1):
            print(f"  {i:2}. [{log['type']:12}] {log['query']} → {log['results']}件")

    print(f"{'='*60}")


if __name__ == "__main__":
    main()
