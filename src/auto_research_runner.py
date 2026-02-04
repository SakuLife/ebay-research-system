"""Auto Research Runner - Pattern② full automation."""

import argparse
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

from dotenv import load_dotenv

from .ebay_client import EbayClient
from .sourcing import SourcingClient
from .profit import calculate_profit
from .sheets_client import GoogleSheetsClient
from .spreadsheet_mapping import INPUT_SHEET_COLUMNS, COL_INDEX
from .search_base_client import SearchBaseClient
from .config_loader import load_all_configs
from .weight_estimator import estimate_weight_from_title, detect_product_type
from .models import SourceOffer, ListingCandidate
from .serpapi_client import SerpApiClient, ShoppingItem, clean_query_for_shopping
from .gemini_client import GeminiClient, reset_gemini_usage, get_gemini_usage_summary
from .price_scraper import scrape_price_for_url, scrape_price_with_fallback


# 日本時間 (UTC+9)
JST = timezone(timedelta(hours=9))


def now_jst() -> datetime:
    """現在の日本時間を取得."""
    return datetime.now(JST)


# 価格スクレイピング対象の大手ECサイト
MAJOR_EC_DOMAINS_FOR_SCRAPING = [
    "amazon.co.jp",
    "rakuten.co.jp",
    "shopping.yahoo.co.jp",
]


def unwrap_google_redirect_url(url: str) -> str:
    """
    google.com のリダイレクトURLから実際のURLを抽出する.

    例:
    - https://www.google.com/url?q=https://store.shopping.yahoo.co.jp/...
    - https://www.google.co.jp/url?url=https://item.rakuten.co.jp/...

    Args:
        url: google.comのリダイレクトURL

    Returns:
        実際のURL。抽出できない場合は元のURLをそのまま返す
    """
    if not url:
        return url

    # google.com/google.co.jp 以外は処理不要
    if "google.com" not in url and "google.co.jp" not in url:
        return url

    try:
        from urllib.parse import urlparse, parse_qs, unquote

        # URLを複数回デコード（二重エンコード対策）
        decoded_url = url
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
                    # ドメイン形式のみhttps://を付与
                    if "." in extracted and "/" in extracted and " " not in extracted:
                        domain_pattern = re.match(r'^[a-zA-Z0-9][\w.-]*\.[a-zA-Z]{2,}', extracted)
                        if domain_pattern:
                            return "https://" + extracted

        return url  # 抽出できない場合は元のURL
    except Exception:
        return url


def try_scrape_zero_price_items(sources: list, max_scrape: int = 5) -> int:
    """
    価格0円のアイテムに対してスクレイピングを試みる.

    大手ECサイト（Amazon/楽天/Yahoo）の0円アイテムに対してのみ実行.
    検索結果取得直後に呼び出すことで、より多くの有効な候補を得る.
    在庫ステータスも取得して保存する.

    Args:
        sources: SourceOfferのリスト（価格と在庫状態が更新される）
        max_scrape: 最大スクレイピング件数（API負荷軽減）

    Returns:
        価格取得成功件数
    """
    scraped_count = 0
    attempted = 0

    for source in sources:
        # 既に価格がある場合はスキップ
        if source.source_price_jpy > 0:
            continue

        # 大手ECサイト以外はスキップ
        is_major_ec = any(domain in source.source_url for domain in MAJOR_EC_DOMAINS_FOR_SCRAPING)
        if not is_major_ec:
            continue

        # 最大件数に達したら終了
        if attempted >= max_scrape:
            break

        attempted += 1
        print(f"    [Scrape] {source.source_site}: 価格0円 → スクレイピング中...")

        # 通常スクレイピング + Headlessフォールバック
        scraped = scrape_price_with_fallback(source.source_url, source.source_price_jpy)

        # 在庫ステータスを更新
        source.in_stock = scraped.in_stock
        source.stock_status = scraped.stock_status

        if scraped.success and scraped.price > 0:
            source.source_price_jpy = scraped.price
            stock_msg = " (在庫切れ)" if not scraped.in_stock else ""
            print(f"             → JPY {scraped.price:,.0f}{stock_msg} (成功)")
            scraped_count += 1
        else:
            error_msg = "在庫切れ" if not scraped.in_stock else scraped.error_message[:30]
            print(f"             → 失敗: {error_msg}")

    return scraped_count


# =============================================================================
# 数量抽出・マッチング関数
# =============================================================================

@dataclass
class QuantityInfo:
    """商品の数量情報."""
    quantity: int  # 数量（1 = 単品）
    is_set: bool  # セット商品かどうか
    is_complete: bool  # 全巻・コンプリートセットか
    vol_range: tuple  # (開始巻, 終了巻) または None
    pattern_matched: str  # マッチしたパターン（デバッグ用）


def extract_quantity_from_title(title: str, is_japanese: bool = False) -> QuantityInfo:
    """
    商品タイトルから数量を抽出する.

    英語パターン:
    - "Vol.0-45" → 46巻
    - "Set of 11" → 11個
    - "10 pcs" / "10pcs" → 10個
    - "Complete Set" → セット（数量不明）
    - "Bundle" / "Lot" → セット（数量不明）

    日本語パターン:
    - "全45巻" → 45巻
    - "1-45巻セット" → 45巻
    - "全巻セット" → 全巻（数量不明）
    - "〇〇セット" → セット

    Args:
        title: 商品タイトル
        is_japanese: 日本語タイトルかどうか

    Returns:
        QuantityInfo
    """
    if not title:
        return QuantityInfo(1, False, False, None, "no_title")

    title_lower = title.lower()

    # ===== 英語パターン =====
    if not is_japanese:
        # "Vol.0-45" / "Vol 1-45" / "Volume 1 - 45" → 範囲から数量計算
        vol_range_match = re.search(r'vol(?:ume)?\.?\s*(\d+)\s*[-~～]\s*(\d+)', title_lower)
        if vol_range_match:
            start = int(vol_range_match.group(1))
            end = int(vol_range_match.group(2))
            count = end - start + 1
            return QuantityInfo(count, True, True, (start, end), f"vol_range_{start}-{end}")

        # "Set of 11" / "Set of 3"
        set_of_match = re.search(r'set\s+of\s+(\d+)', title_lower)
        if set_of_match:
            count = int(set_of_match.group(1))
            return QuantityInfo(count, True, False, None, f"set_of_{count}")

        # "11 pcs" / "11pcs" / "11 pieces"
        pcs_match = re.search(r'(\d+)\s*(?:pcs|pieces?)\b', title_lower)
        if pcs_match:
            count = int(pcs_match.group(1))
            return QuantityInfo(count, True, False, None, f"pcs_{count}")

        # "Complete Set" / "Complete Manga Set"
        if 'complete' in title_lower and ('set' in title_lower or 'manga' in title_lower):
            return QuantityInfo(0, True, True, None, "complete_set")

        # "Bundle" / "Lot"
        if 'bundle' in title_lower or 'lot' in title_lower:
            # "lot of X" は上でキャッチされるので、ここは数量不明
            return QuantityInfo(0, True, False, None, "bundle_lot")

    # ===== 日本語パターン =====
    # "全45巻" / "全45冊"
    zen_match = re.search(r'全(\d+)[巻冊]', title)
    if zen_match:
        count = int(zen_match.group(1))
        return QuantityInfo(count, True, True, None, f"zen_{count}")

    # "1-45巻" / "1〜45巻" / "1～45巻セット"
    jp_range_match = re.search(r'(\d+)\s*[-~～]\s*(\d+)\s*[巻冊]', title)
    if jp_range_match:
        start = int(jp_range_match.group(1))
        end = int(jp_range_match.group(2))
        count = end - start + 1
        return QuantityInfo(count, True, True, (start, end), f"jp_range_{start}-{end}")

    # "全巻セット" / "全巻" （数量不明）
    if '全巻' in title:
        return QuantityInfo(0, True, True, None, "zenkan")

    # "〇〇セット" / "〇個セット"
    set_match = re.search(r'(\d+)\s*[個点枚体]?\s*セット', title)
    if set_match:
        count = int(set_match.group(1))
        return QuantityInfo(count, True, False, None, f"set_{count}")

    # 一般的なセット
    if 'セット' in title:
        return QuantityInfo(0, True, False, None, "set_generic")

    # 単品（上記いずれにもマッチしない）
    return QuantityInfo(1, False, False, None, "single")


def calculate_quantity_match_score(ebay_qty: QuantityInfo, source_qty: QuantityInfo) -> float:
    """
    eBayと仕入先の数量マッチ度を計算する.

    Returns:
        0.0 (不一致) ～ 1.0 (完全一致)
    """
    # 両方とも単品 → 完全一致
    if not ebay_qty.is_set and not source_qty.is_set:
        return 1.0

    # eBayがセットで、仕入先が単品 → 不一致（最も危険なケース）
    if ebay_qty.is_set and not source_qty.is_set:
        return 0.0

    # eBayが単品で、仕入先がセット → 弱い不一致
    if not ebay_qty.is_set and source_qty.is_set:
        return 0.3

    # 両方ともセット
    # 数量が分かっている場合
    if ebay_qty.quantity > 0 and source_qty.quantity > 0:
        if ebay_qty.quantity == source_qty.quantity:
            return 1.0  # 完全一致
        elif abs(ebay_qty.quantity - source_qty.quantity) <= 1:
            return 0.8  # 1個差は許容（巻数の数え方の違い）
        else:
            # 大きく異なる場合
            ratio = min(ebay_qty.quantity, source_qty.quantity) / max(ebay_qty.quantity, source_qty.quantity)
            return ratio * 0.5  # 比率ベースで減点

    # 数量不明だがセット同士 → 中程度の一致
    if ebay_qty.is_complete and source_qty.is_complete:
        return 0.7  # 両方ともコンプリート
    if ebay_qty.is_set and source_qty.is_set:
        return 0.5  # 両方ともセットだが詳細不明

    return 0.3  # それ以外


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


# カード番号パターン（Weiss Schwarz, Lorcana, Pokemon等）
CARD_ID_PATTERNS = [
    # Weiss Schwarz: DD/W84-001, Dds/S104-029SSP, GBS/S63-024SP など
    r'([A-Z]{2,4}/[A-Z]?\d{2,4}-\d{2,4}[A-Z]{0,4})',
    # シンプルなカード番号: S104-029, W84-001 など
    r'\b([A-Z]\d{2,4}-\d{2,4}[A-Z]{0,4})\b',
    # Lorcana / Pokemon: 242/204, 001/165 など
    r'\b(\d{1,3}/\d{1,3})\b',
    # レアリティ単独: SSP, SP, SR, RR, R など（後で補助的に使用）
    r'\b(SSP|SP|SR|RR|SEC|HR|UR|PR)\b',
]


def extract_card_identifiers(text: str) -> set:
    """
    テキストからカード番号/識別子を抽出する.

    対応フォーマット:
    - Weiss Schwarz: DD/W84-001, Dds/S104-029SSP
    - Lorcana/Pokemon: 242/204, 001/165
    - レアリティ: SSP, SP, SR, RR

    Args:
        text: 商品タイトル

    Returns:
        抽出された識別子のセット
    """
    if not text:
        return set()

    identifiers = set()
    text_upper = text.upper()

    for pattern in CARD_ID_PATTERNS:
        matches = re.findall(pattern, text_upper)
        identifiers.update(matches)

    return identifiers


def check_product_identifier_match(ebay_title: str, source_title: str) -> tuple:
    """
    eBayタイトルの商品識別子（カード番号/型番）が仕入先にあるかチェック.

    カード番号や型番がeBayタイトルにある場合、仕入先にも同じ番号がなければ
    別商品と判断する.

    例:
    - eBay: "Weiss Schwarz Dds/S104-029SSP" → 識別子: "DDS/S104-029SSP"
    - Source: "Weiss Schwarz S104-029" → 部分一致 → OK
    - Source: "Weiss Schwarz S104-030" → 不一致 → NG

    Args:
        ebay_title: eBayタイトル
        source_title: 仕入先タイトル

    Returns:
        (is_match, missing_ids):
        - is_match: True=一致または識別子なし, False=不一致
        - missing_ids: 見つからなかった識別子のリスト
    """
    ebay_ids = extract_card_identifiers(ebay_title)

    if not ebay_ids:
        # カード番号/型番がない場合は問題なし
        return (True, [])

    source_ids = extract_card_identifiers(source_title)
    source_upper = source_title.upper()

    missing_ids = []

    for ebay_id in ebay_ids:
        # レアリティ単独（SSP, SR等）はスキップ（補助情報のため）
        if ebay_id in {'SSP', 'SP', 'SR', 'RR', 'SEC', 'HR', 'UR', 'PR'}:
            continue

        found = False

        # 完全一致チェック
        if ebay_id in source_ids:
            found = True
        else:
            # 部分一致チェック（S104-029 in DDS/S104-029SSP）
            # 番号部分を抽出して比較
            # 例: DDS/S104-029SSP → S104-029 部分で比較
            ebay_core = re.sub(r'^[A-Z]{2,4}/', '', ebay_id)  # プレフィックス除去
            ebay_core = re.sub(r'[A-Z]{2,4}$', '', ebay_core)  # サフィックス除去

            for source_id in source_ids:
                source_core = re.sub(r'^[A-Z]{2,4}/', '', source_id)
                source_core = re.sub(r'[A-Z]{2,4}$', '', source_core)

                if ebay_core and source_core and ebay_core == source_core:
                    found = True
                    break

            # ソースタイトル全体にも番号が含まれているかチェック
            if not found and ebay_core:
                if ebay_core in source_upper:
                    found = True

        if not found:
            missing_ids.append(ebay_id)

    # 1つでも必須の識別子が見つからない場合は不一致
    is_match = len(missing_ids) == 0
    return (is_match, missing_ids)


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

# キャラクター名/固有名詞マッピング（英語 ↔ カタカナ）
# pykakasiでカタカナ→ローマ字変換後に照合するため、ローマ字表記も含む
CHARACTER_NAME_MAPPINGS = {
    # Disney / Pixar
    'nick wilde': ['ニック・ワイルド', 'ニックワイルド', 'nikku wairudo', 'nikkuwairudo'],
    'judy hopps': ['ジュディ・ホップス', 'ジュディホップス', 'judi hoppusu'],
    'zootopia': ['ズートピア', 'zuutopia'],
    'mickey mouse': ['ミッキーマウス', 'ミッキー', 'mikkii mausu', 'mikkii'],
    'minnie mouse': ['ミニーマウス', 'ミニー', 'minii mausu', 'minii'],
    'donald duck': ['ドナルドダック', 'ドナルド', 'donarudo dakku'],
    'winnie the pooh': ['くまのプーさん', 'プーさん', 'puusan'],
    'stitch': ['スティッチ', 'suticchi'],
    'tsum tsum': ['ツムツム', 'tsumutsuму', 'tsumutsumu'],
    'duffy': ['ダッフィー', 'daffii'],
    'shelliemay': ['シェリーメイ', 'sheriimei'],
    'gelatoni': ['ジェラトーニ', 'jeratooni'],
    'stellalou': ['ステラルー', 'suteraruu'],
    # Doraemon
    'doraemon': ['ドラえもん', 'doraemon'],
    'nobita': ['のび太', 'nobita'],
    # Pokemon
    'pikachu': ['ピカチュウ', 'pikachuu'],
    'pokemon': ['ポケモン', 'ポケットモンスター', 'pokemon', 'poketto monsutaa'],
    'charizard': ['リザードン', 'rizaadon'],
    'mewtwo': ['ミュウツー', 'myuutsuu'],
    # Sanrio
    'hello kitty': ['ハローキティ', 'キティ', 'haroo kiti', 'kiti'],
    'my melody': ['マイメロディ', 'マイメロ', 'mai merodi'],
    'cinnamoroll': ['シナモロール', 'shinamorooru'],
    'kuromi': ['クロミ', 'kuromi'],
    # Anime / Games
    'gundam': ['ガンダム', 'gandamu'],
    'evangelion': ['エヴァンゲリオン', 'エヴァ', 'evangerion', 'eva'],
    'one piece': ['ワンピース', 'wanpiisu'],
    'naruto': ['ナルト', 'naruto'],
    'dragon ball': ['ドラゴンボール', 'doragon booru'],
    'goku': ['悟空', 'ゴクウ', 'gokuu'],
    'demon slayer': ['鬼滅の刃', 'きめつのやいば', 'kimetsu no yaiba'],
    'jujutsu kaisen': ['呪術廻戦', 'jujutsu kaisen'],
    'attack on titan': ['進撃の巨人', 'shingeki no kyojin'],
    'studio ghibli': ['スタジオジブリ', 'ジブリ', 'sutajio jiburi', 'jiburi'],
    'totoro': ['トトロ', 'totoro'],
    'spirited away': ['千と千尋の神隠し', 'sen to chihiro'],
    # Horror Bishoujo / Horror figures
    'tiffany': ['ティファニー', 'tifanii'],
    'chucky': ['チャッキー', 'chakkii'],
    'freddy krueger': ['フレディ', 'freddy', 'furedi'],
    'jason voorhees': ['ジェイソン', 'jason', 'jeison'],
    'michael myers': ['マイケル・マイヤーズ', 'マイケルマイヤーズ', 'maikeru maiyaazu'],
    'pennywise': ['ペニーワイズ', 'peniwaizu'],
    'annabelle': ['アナベル', 'anaberu'],
    'sadako': ['貞子', 'sadako'],
    'kayako': ['伽椰子', 'kayako'],
    'leatherface': ['レザーフェイス', 'rezaafeisu'],
    'ghostface': ['ゴーストフェイス', 'goosutofeisu'],
    'pinhead': ['ピンヘッド', 'pinheddo'],
    'beetlejuice': ['ビートルジュース', 'biitorujuusu'],
    'edward scissorhands': ['エドワード・シザーハンズ', 'シザーハンズ', 'shizaahanzu'],
    'bride of chucky': ['チャイルドプレイ', 'チャッキーの花嫁', 'chairudopurei'],
    # Plush / Toy terms
    'plush': ['ぬいぐるみ', 'nuigurumi'],
    'plushie': ['ぬいぐるみ', 'nuigurumi'],
    'costume': ['コスチューム', 'kosuchuumu', '衣装'],
    'set': ['セット', 'setto'],
}

# カテゴリ別の除外キーワード（商品種類の不一致を防ぐ）
CATEGORY_EXCLUSION_KEYWORDS = {
    # Plush/ぬいぐるみカテゴリの場合、以下のキーワードを含む商品は除外
    'plush': ['編みぐるみ', 'あみぐるみ', 'amigurumi', '本', '雑誌', '全国版',
              'コレクション全', 'デアゴスティーニ', 'deagostini', 'magazine',
              'book', 'キット', 'kit', '手作り', '作り方', 'pattern'],
    # フィギュアカテゴリ
    'figure': ['本', '雑誌', 'book', 'magazine', 'カタログ', 'catalog'],
    # カードカテゴリ
    'card': ['スリーブ', 'sleeve', 'デッキケース', 'deck box', 'バインダー', 'binder'],
}


def extract_key_identifiers(title: str) -> List[tuple]:
    """
    タイトルからキーとなる固有名詞（キャラクター名等）を抽出する.

    CHARACTER_NAME_MAPPINGS に登録されている名前を検出し、
    (英語キー, 日本語バリエーションリスト) のタプルを返す.

    例:
    - "HORROR Bishoujo Tiffany Figure" → [('tiffany', ['ティファニー', 'tifanii'])]
    - "Pokemon Pikachu Plush" → [('pokemon', [...]), ('pikachu', [...])]

    Args:
        title: 商品タイトル

    Returns:
        抽出されたキー識別子のリスト [(eng_key, jp_variants), ...]
    """
    if not title:
        return []

    title_lower = title.lower()
    found_identifiers = []

    # CHARACTER_NAME_MAPPINGS から検索
    for eng_name, jp_variants in CHARACTER_NAME_MAPPINGS.items():
        # 英語名がタイトルに含まれているか
        if eng_name in title_lower:
            found_identifiers.append((eng_name, jp_variants))
            continue

        # 日本語バリエーションがタイトルに含まれているか
        for variant in jp_variants:
            if variant in title or variant.lower() in title_lower:
                found_identifiers.append((eng_name, jp_variants))
                break

    return found_identifiers


def check_key_identifier_match(ebay_title: str, source_title: str) -> tuple:
    """
    eBayタイトルのキー識別子が仕入先タイトルに含まれているかチェック.

    例:
    - eBay: "HORROR Bishoujo Tiffany" → キー: "tiffany"
    - Source: "HORROR美少女 チャッキー" → "ティファニー"なし → ❌
    - Source: "HORROR美少女 ティファニー" → "ティファニー"あり → ✓

    Args:
        ebay_title: eBayタイトル
        source_title: 仕入先タイトル

    Returns:
        (match_ratio, missing_keys):
        - match_ratio: 一致率（0.0〜1.0）
        - missing_keys: 見つからなかったキーのリスト
    """
    ebay_identifiers = extract_key_identifiers(ebay_title)

    if not ebay_identifiers:
        # キー識別子がない場合は問題なし
        return (1.0, [])

    source_lower = source_title.lower()
    matched = 0
    missing_keys = []

    for eng_key, jp_variants in ebay_identifiers:
        found = False

        # 英語名がソースに含まれているか
        if eng_key in source_lower:
            found = True
        else:
            # 日本語バリエーションがソースに含まれているか
            for variant in jp_variants:
                if variant in source_title or variant.lower() in source_lower:
                    found = True
                    break

        if found:
            matched += 1
        else:
            missing_keys.append(eng_key)

    match_ratio = matched / len(ebay_identifiers) if ebay_identifiers else 1.0
    return (match_ratio, missing_keys)


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

    # 4. キャラクター名/固有名詞一致チェック（英語 ↔ カタカナ）
    # 例: "Nick Wilde" in eBay ↔ "ニック・ワイルド" in source
    source_romaji = normalize_to_romaji(source_title)
    character_matches = 0
    for eng_name, jp_variants in CHARACTER_NAME_MAPPINGS.items():
        # eBayタイトルに英語名が含まれているか
        if eng_name in ebay_lower:
            # 仕入先タイトルに日本語バリエーションまたはローマ字が含まれているか
            for variant in jp_variants:
                if variant in source_title or variant.lower() in source_romaji:
                    character_matches += 1
                    break
        # 逆パターン: 仕入先に英語名が含まれている場合
        elif eng_name in source_lower:
            for variant in jp_variants:
                if variant in ebay_title:
                    character_matches += 1
                    break
    if character_matches > 0:
        bonus += 0.3 * min(character_matches, 3)  # 最大+0.9

    # 5. ローマ字に統一して単語比較
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
    "/product-group/",  # Merdisney一覧ページ
    "/character/",    # Disney Store キャラクターページ
    "?prefn",         # Disney Store フィルター
    "?prefv",         # Disney Store フィルター
    # ブログ・まとめサイト・ニュースサイト（仕入れ不可）
    "infotvlive.net",
    "matome.naver.jp",
    "togetter.com",
    "note.com",
    "ameblo.jp",
    "watch.impress.co.jp",  # ニュースサイト
    "gigazine.net",
    "itmedia.co.jp",
    "gizmodo.jp",
    "engadget.com",
    "4gamer.net",
    "famitsu.com",          # ゲームニュース/記事サイト（購入不可）
    "game.watch.impress.co.jp",  # ゲームニュース（購入不可）
    "dengekionline.com",
    "fullress.com",         # スニーカーニュース/リリース情報ブログ（購入不可）
    "uptodate.tokyo",       # スニーカーニュース/リリース情報ブログ（購入不可）
    "sneakerscout.jp",      # スニーカーニュース（購入不可）
    "stv.jp",               # テレビ局サイト（EC機能なし）
    "snkrdunk.com",         # スニーカーダンク（フリマ/二次流通）
    # 価格比較・相場サイト（仕入れ不可、購入不可）
    "kakaku.com",
    "price.com",
    "bestgate.net",
    "aucfan.com",           # オークション相場サイト
    "オークファン",
    "pricetar.com",
    # 口コミ・レビューページ（商品ページではない）
    "/community/",          # ヨドバシ等の口コミページ
    "/review/",
    "/reviews/",
    "/user-review/",
    # 電子書籍・Kindle（物理的な商品ではない）
    "/ebook/",
    "-ebook/",
    "ebook/dp/",
    "/DIGITAL/",
    "kindle",
    "digital-text",
    # Etsy（カスタム品・ハンドメイドが多く仕入先として不適切）
    "etsy.com",
    "etsy.jp",
    # スパム系TLD・海外不明サイト
    ".sale/",             # スパムサイトに多いTLD
    ".click/",            # スパムサイトに多いTLD
    ".click?",            # スパムサイトに多いTLD（クエリ付き）
    "tsavoequipment.com", # 海外機器サイト（日本の仕入先ではない）
    "rcsolutionspty.com", # スパム/リダイレクトサイト
    "ilikesecbcd",        # スパムサイト
    # プレスリリース・PR配信（購入不可）
    "prtimes.jp",
    # 公式サイト・メーカーサイト（直接購入不可、小売サイトではない）
    "pokemongoplusplus.com",  # Pokemon GO Plus+公式サイト
    "onepiece-base.com",      # ワンピース公式ショップ（小売購入不可）
    # 買取サイト（売却用、仕入れ不可）
    "kaitori",                # 買取系サイト全般
    "買取",                   # 買取系サイト（日本語URL）
    "satei",                  # 査定サイト
]


def is_allowed_source_url(url: str) -> bool:
    """
    URLが仕入先として許可されるかどうか判定する.

    ブラックリスト方式：除外パターンに一致しなければOK.
    これにより、未知のショップでも価格が取れていれば候補になる.

    除外されるサイト:
    - 海外Amazon、AliExpress、Shein等の海外サイト
    - PDF、eBay等
    - 検索/カテゴリページ（個別商品ではない）

    Returns:
        True: 許可
        False: 除外
    """
    if not url:
        return False

    url_lower = url.lower()

    # 除外パターンに一致したらNG
    for pattern in EXCLUDED_URL_PATTERNS:
        if pattern in url_lower:
            return False

    # 検索/カテゴリページは除外（個別商品ではない）
    search_page_patterns = [
        "/search?", "/search/",  # 検索ページ
        "/s?k=", "/s/",          # Amazon検索
        "/stores/",              # Amazonストアページ（個別商品ではない）
        "/category/", "/genre/", # カテゴリページ
        "/list/", "/browse/",    # 一覧ページ
        "/item_list/", "/item_list?",  # 商品一覧ページ（vector-parkなど）
        "/tag/", "/tags/",       # タグページ
        "search-showajax",       # Salesforce Commerce Cloud AJAX検索
        "/products-banner",      # WooCommerceバナー/アーカイブページ
        "/product-category/",    # WooCommerceカテゴリページ
        "/shop/page/",           # WooCommerceショップページネーション
        "/collections/",         # Shopifyコレクションページ
    ]
    for pattern in search_page_patterns:
        if pattern in url_lower:
            return False

    # 除外パターンに一致しなければOK（ブラックリスト方式）
    return True


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
        # 中古ゴルフクラブ専門店
        "golfdo.com", "golfkids", "golfeffort", "golfpartner",
    ]

    for pattern in used_patterns:
        if pattern in site_lower or pattern in url_lower:
            return False

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
        return False

    return True


def calculate_condition_score(title: str, source_site: str) -> float:
    """
    国内商品のconditionスコアを計算する.
    新品/未開封は加点、中古/難ありは減点.

    カード商品向け注意:
    - 「美品」「極美品」等はカードでは中古を意味する
    - PSAグレード品は過去に評価された商品なので中古扱い

    Returns:
        スコア（0.0〜2.0、1.0が基準）
    """
    title_lower = title.lower()
    score = 1.0

    # 新品/未開封系キーワード（加点）
    new_keywords = ["未開封", "新品", "sealed", "新品未開封", "未使用", "brand new", "factory sealed"]
    if any(kw in title_lower for kw in new_keywords):
        score += 0.3

    # 中古系キーワード（減点）
    used_keywords = [
        "中古", "used", "難あり", "ジャンク", "訳あり", "傷あり", "プレイ用",
        # アウトレット・展示品
        "アウトレット", "outlet", "展示品", "展示処分", "店頭展示",
        # カード向け中古キーワード
        "美品", "極美品", "良品", "並品",
        "psa", "bgs", "cgc", "graded",  # グレーディング済み = 中古
        "mint", "excellent", "near mint", "nm", "ex",  # 状態評価 = 中古
        "スリーブ", "白欠け", "初期傷", "微傷",
        # 古書・ヴィンテージ品（古い年代のものは中古扱い）
        "昭和", "平成",  # 古い年代の商品（昭和XX年等）
        "古書", "古本", "絶版", "廃盤",
    ]
    if any(kw in title_lower for kw in used_keywords):
        score -= 0.7  # 中古は大幅減点（New条件時は実質除外に近い）

    return max(0.1, min(2.0, score))


def is_accessory_product(title: str) -> tuple[bool, str]:
    """
    アクセサリー/周辺機器かどうか判定する.
    本体を探しているのにケース等が見つかった場合に除外するため.

    Args:
        title: 仕入先商品タイトル

    Returns:
        (判定結果, 検出されたキーワード)
    """
    title_lower = title.lower()

    # 「For [ブランド名]」パターン（英語のアクセサリー表記）
    # 例: "For Lenovo Legion", "For iPhone 15"
    import re
    if re.search(r'\bfor\s+[a-z]', title_lower):
        return (True, "For [Brand]")

    # アクセサリーキーワード
    accessory_keywords = [
        # ケース・カバー類
        "ケース", "カバー", "case", "cover", "保護", "フィルム", "film",
        "スクリーンプロテクター", "screen protector",
        # 充電・電源関連
        "充電器", "charger", "充電ケーブル", "cable", "アダプター", "adapter",
        "acアダプタ", "電源", "バッテリー", "battery",
        # スタンド・ホルダー
        "スタンド", "stand", "ホルダー", "holder", "マウント", "mount",
        # ストラップ・アクセサリ
        "ストラップ", "strap", "キーホルダー",
        # その他周辺機器
        "対応", "専用", "互換", "compatible",
    ]

    for kw in accessory_keywords:
        if kw in title_lower:
            return (True, kw)

    return (False, "")


def is_limited_edition_product(title: str) -> tuple[bool, str]:
    """
    限定品/プレミアム品/特典付き商品かどうか判定する.
    これらの商品は一般的なECサイトで新品入手が困難なためスキップ対象.

    Args:
        title: eBay商品タイトル

    Returns:
        (判定結果, 検出されたキーワード)
    """
    title_lower = title.lower()

    # 限定品を示すキーワード
    limited_keywords = [
        # 日本語
        "限定", "限定版", "限定盤", "特典", "初回", "先着", "予約特典",
        "数量限定", "期間限定", "店舗限定", "コレクターズ", "特別版",
        "プレミアム", "初版", "生産終了", "プロモ",
        "一番くじ",  # コンビニ抽選商品（フリマでしか入手不可）
        # 英語
        "limited edition", "limited", "bonus", "first edition", "pre-order",
        "collector's", "collector", "special edition", "exclusive",
        "premium edition", "deluxe edition", "japan exclusive",
        "store exclusive", "event exclusive", "convention exclusive",
        "promo", "promotional", "serial number",  # プロモカード、シリアル番号入り
        "ichiban kuji", "lottery prize", "last one prize", "prize figure",  # 一番くじ系
        # 日本語一番くじパターン
        "一番くじ", "いちばんくじ",
        "a賞", "b賞", "c賞", "d賞", "e賞", "f賞", "g賞", "h賞",
        "ラストワン賞", "ラストワン",
    ]

    for kw in limited_keywords:
        if kw in title_lower:
            return (True, kw)

    return (False, "")


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


def check_category_exclusion(source_title: str, category_name: str) -> tuple[bool, str]:
    """
    カテゴリベースの除外チェックを行う.
    eBayカテゴリと仕入先商品の種類が一致しない場合に除外.

    例: eBayが「Plush Toys」なのに、仕入先が「編みぐるみキット」や「本」の場合は除外

    Args:
        source_title: 仕入先の商品タイトル
        category_name: eBayのカテゴリ名

    Returns:
        (should_exclude: bool, reason: str)
    """
    if not category_name:
        return False, ""

    source_lower = source_title.lower()
    category_lower = category_name.lower()

    # カテゴリ名からキーを判定
    category_key = None
    if 'plush' in category_lower or 'ぬいぐるみ' in category_name:
        category_key = 'plush'
    elif 'figure' in category_lower or 'フィギュア' in category_name:
        category_key = 'figure'
    elif 'card' in category_lower or 'カード' in category_name:
        category_key = 'card'

    if category_key and category_key in CATEGORY_EXCLUSION_KEYWORDS:
        for keyword in CATEGORY_EXCLUSION_KEYWORDS[category_key]:
            if keyword.lower() in source_lower or keyword in source_title:
                return True, f"カテゴリ不一致({category_key}≠{keyword})"

    return False, ""


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
    min_similarity: float = 0.3,
    prefer_sourcing: bool = True,
    require_price: bool = True,
    top_n: int = 3,
    category_name: str = "",
    condition: str = "New"
) -> List[RankedSource]:
    """
    eBayタイトルにマッチする仕入先をスコア順に最大N件返す.
    類似度 × conditionスコア × 仕入れ優先度 × 価格ボーナス で総合評価.

    改善点:
    - 類似度は足切りではなく、スコアの一部として使用
    - 価格がある候補を価格不明より優先
    - 類似度が極端に低い（<5%）場合のみ除外
    - New条件時、中古品（conditionスコア0.5未満）は除外

    Args:
        ebay_title: eBayの商品タイトル
        sources: 仕入先候補リスト
        min_similarity: 最低類似度（デフォルト0.3、価格ありは0.55で判定）
        prefer_sourcing: 仕入れ可能サイトを優先するか
        require_price: 価格が必須かどうか（Trueなら価格0円は除外）
        top_n: 返す件数（デフォルト3）
        category_name: eBayのカテゴリ名（カテゴリベース除外に使用）
        condition: eBayの商品状態（"New" or "Used"）

    Returns:
        RankedSourceのリスト（スコア降順）
    """
    if not sources:
        return []

    ranked_sources: List[RankedSource] = []
    excluded_urls = 0
    category_excluded = 0
    low_similarity_excluded = 0
    used_excluded = 0  # 中古品除外カウント
    seen_urls = set()  # 重複URL除外用

    MAJOR_EC_DOMAINS = ["amazon.co.jp", "rakuten.co.jp", "shopping.yahoo.co.jp"]
    ABSOLUTE_MIN_SIMILARITY = 0.55  # 絶対最低類似度（これ以下は完全除外）

    for source in sources:
        # 許可されていないURL（海外Amazon、PDF等）は除外
        if not is_allowed_source_url(source.source_url):
            excluded_urls += 1
            continue

        # 重複URL除外（同じ商品が複数回出てくることがある）
        if source.source_url in seen_urls:
            continue
        seen_urls.add(source.source_url)

        # カテゴリベースの除外チェック（Plushなのに本/雑誌など）
        should_exclude, reason = check_category_exclusion(source.title, category_name)
        if should_exclude:
            category_excluded += 1
            continue

        # 在庫切れ・中古のみは除外（Step 2/3へフォールバックするため）
        if not source.in_stock or source.stock_status in ["out_of_stock", "used_only"]:
            continue

        # 価格の有無を判定
        has_price = source.source_price_jpy > 0
        is_major_ec = any(domain in source.source_url for domain in MAJOR_EC_DOMAINS)

        # 価格0円の処理
        # 大手ECサイトは価格0でも候補に含める（後で手動確認）
        if require_price and not has_price and not is_major_ec:
            continue

        # 類似度（型番一致ボーナス込み）
        similarity = calculate_title_similarity(ebay_title, source.title)

        # 類似度閾値の判定（価格がある場合は緩和）
        effective_min_similarity = ABSOLUTE_MIN_SIMILARITY if has_price else min_similarity
        if similarity < effective_min_similarity:
            low_similarity_excluded += 1
            continue

        # conditionスコア
        condition_score = calculate_condition_score(source.title, source.source_site)

        # New条件時、中古品（conditionスコア0.5未満）は除外
        # 「中古」「美品」「Aランク」等が含まれる場合、スコアは0.3程度になる
        if condition == "New" and condition_score < 0.5:
            used_excluded += 1
            continue

        # アクセサリー/周辺機器の除外（本体を探しているのにケースが出てきた場合等）
        is_accessory, accessory_kw = is_accessory_product(source.title)
        if is_accessory:
            print(f"    [SKIP] アクセサリー検出: '{accessory_kw}' → {source.title[:40]}...")
            continue

        # 仕入れ優先度
        priority = calculate_source_priority(source.source_site) if prefer_sourcing else 1.0

        # 価格ボーナス（価格がある候補を優先）
        # 価格あり: 1.5倍、価格なし: 0.3倍（大幅にペナルティ）
        price_bonus = 1.5 if has_price else 0.3

        # キー識別子（キャラクター名等）マッチチェック
        # eBayタイトルにあるキャラ名が仕入先にない場合はペナルティ
        key_match_ratio, missing_keys = check_key_identifier_match(ebay_title, source.title)
        key_match_bonus = 1.0
        if missing_keys:
            # 重要なキーワードが欠けている場合は大幅ペナルティ
            # 例: "Tiffany"が欲しいのに"チャッキー"が出てきた場合
            key_match_bonus = 0.1  # 90%減点
            print(f"    [WARN] キー不一致: {', '.join(missing_keys)} が見つからない → {source.title[:40]}...")

        # カード番号/型番の必須チェック（不一致は完全除外）
        # 例: "S104-029SSP"がeBayにあるのにソースに"S104-030"しかない場合
        product_id_match, missing_ids = check_product_identifier_match(ebay_title, source.title)
        if not product_id_match:
            # カード番号/型番が不一致の場合は完全に除外
            print(f"    [SKIP] 識別子不一致: {', '.join(missing_ids)} → {source.title[:40]}...")
            continue

        # 総合スコア = 類似度 × conditionスコア × 優先度 × 価格ボーナス × キー一致ボーナス
        total_score = similarity * condition_score * priority * price_bonus * key_match_bonus

        ranked_sources.append(RankedSource(
            source=source,
            score=total_score,
            similarity=similarity,
            condition_score=condition_score,
            priority=priority
        ))

    if excluded_urls > 0 or category_excluded > 0 or low_similarity_excluded > 0 or used_excluded > 0:
        parts = []
        if excluded_urls > 0:
            parts.append(f"海外/PDF除外: {excluded_urls}件")
        if category_excluded > 0:
            parts.append(f"カテゴリ除外: {category_excluded}件")
        if low_similarity_excluded > 0:
            parts.append(f"低類似度除外: {low_similarity_excluded}件")
        if used_excluded > 0:
            parts.append(f"中古品除外: {used_excluded}件")
        print(f"    ({', '.join(parts)})")

    # スコア降順でソートして上位N件を返す
    ranked_sources.sort(key=lambda x: x.score, reverse=True)
    return ranked_sources[:top_n]


def find_best_matching_source(
    ebay_title: str,
    sources: List[SourceOffer],
    min_similarity: float = 0.3,
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
    eBayリンク列（O列）からitem IDを抽出.
    """
    try:
        worksheet = sheet_client.spreadsheet.worksheet("入力シート")
        # O列（eBayリンク）を取得 - col_values()は1-indexed
        ebay_urls = worksheet.col_values(15)  # O列 = 15番目

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


def _find_base_keyword(raw_keyword: str, main_keywords: list[str]) -> str:
    """修飾語付きキーワードからE列の元キーワードを逆引きする."""
    # 完全一致を先にチェック
    if raw_keyword in main_keywords:
        return raw_keyword
    # 前方一致で探す（"Pokemon Japanese" → "Pokemon"）
    for mk in main_keywords:
        if raw_keyword.startswith(mk):
            return mk
    # 見つからなければraw_keywordそのものを返す
    return raw_keyword


def count_excluded_by_keyword(
    sheets_client,
    main_keywords: list[str]
) -> dict[str, int]:
    """
    入力シートからステータス「除外」の件数をキーワード×状態別に集計する.

    Returns:
        dict mapping "keyword|condition" -> excluded count
    """
    try:
        worksheet = sheets_client.spreadsheet.worksheet("入力シート")
        # B列(キーワード)=col0, E列(新品中古)=col3, V列(ステータス)=col20 in B:V range
        all_data = worksheet.get("B2:V1000")

        excluded_counts: dict[str, int] = {}
        for row in all_data:
            if not row or not row[0]:
                continue
            keyword = row[0].strip()
            condition = row[3].strip() if len(row) > 3 and row[3] else "New"
            status = row[20].strip() if len(row) > 20 and row[20] else ""

            if status == "除外":
                base_kw = _find_base_keyword(keyword, main_keywords)
                stats_key = f"{base_kw}|{condition}"
                excluded_counts[stats_key] = excluded_counts.get(stats_key, 0) + 1

        if excluded_counts:
            print(f"  [RANKING] 除外件数: {excluded_counts}")
        return excluded_counts
    except Exception as e:
        print(f"  [WARN] 除外件数の集計失敗: {e}")
        return {}


def update_keyword_ranking(
    sheet_client,
    keyword_stats: dict[str, dict[str, int]],
    excluded_counts: Optional[dict[str, int]] = None
) -> None:
    """
    設定＆キーワードシートのH〜N列にキーワードランキングを書き込む.

    キーは「キーワード|状態」形式（例: "Canon|New"）。
    既存データがあれば累積加算してランキングを更新する。
    更新日は今回処理したキーワードのみ更新する。
    有効出力率 = (出力件数 - 除外件数) / 処理件数。
    """
    if excluded_counts is None:
        excluded_counts = {}

    try:
        worksheet = sheet_client.spreadsheet.worksheet("設定＆キーワード")
        today = datetime.now(JST).strftime("%Y-%m-%d")

        # 既存データを読み込み（H4:N100）
        existing_data = worksheet.get("H4:N100")
        existing_stats: dict[str, dict] = {}

        if existing_data:
            for row in existing_data:
                if not row or not row[0] or not row[0].strip():
                    continue
                kw_raw = row[0].strip()
                cond = ""
                if len(row) > 1:
                    cond = row[1].strip() if row[1] else ""

                if cond in ("New", "Used"):
                    stats_key = f"{kw_raw}|{cond}"
                    try:
                        processed = int(row[3]) if len(row) > 3 and row[3] else 0
                        output = int(row[4]) if len(row) > 4 and row[4] else 0
                    except (ValueError, IndexError):
                        processed, output = 0, 0
                else:
                    # 旧形式: 状態列なし → Newとして移行
                    stats_key = f"{kw_raw}|New"
                    try:
                        processed = int(row[2]) if len(row) > 2 and row[2] else 0
                        output = int(row[3]) if len(row) > 3 and row[3] else 0
                    except (ValueError, IndexError):
                        processed, output = 0, 0

                # 既存の更新日を保持（N列 or M列、日付形式を探す）
                existing_date = ""
                for col_idx in [6, 5]:
                    if len(row) > col_idx and row[col_idx]:
                        val = row[col_idx].strip()
                        if re.match(r'\d{4}-\d{2}-\d{2}', val):
                            existing_date = val
                            break

                existing_stats[stats_key] = {
                    "processed": processed,
                    "output": output,
                    "date": existing_date,
                }

        # 今回の結果をマージ（累積加算 + 日付は今回処理分のみ更新）
        for stats_key, stats in keyword_stats.items():
            if stats_key in existing_stats:
                existing_stats[stats_key]["processed"] += stats["processed"]
                existing_stats[stats_key]["output"] += stats["output"]
                existing_stats[stats_key]["date"] = today
            else:
                existing_stats[stats_key] = {
                    "processed": stats["processed"],
                    "output": stats["output"],
                    "date": today,
                }

        # 有効出力率を計算してソート（降順）
        ranking: list[tuple[str, str, int, int, int, int, str]] = []
        for stats_key, stats in existing_stats.items():
            parts = stats_key.split("|", 1)
            kw = parts[0]
            cond = parts[1] if len(parts) > 1 else "New"
            processed = stats["processed"]
            output = stats["output"]
            excluded = excluded_counts.get(stats_key, 0)
            effective_output = max(0, output - excluded)
            rate = int(effective_output / processed * 100) if processed > 0 else 0
            date = stats.get("date", "")
            ranking.append((kw, cond, rate, processed, output, excluded, date))

        ranking.sort(key=lambda x: x[2], reverse=True)

        # ヘッダー書き込み
        worksheet.update(
            range_name="H1:N1",
            values=[["キーワード", "状態", "有効出力率", "処理件数", "出力件数", "除外件数", "更新日"]],
        )
        worksheet.update(
            range_name="H3",
            values=[["【キーワードランキング】"]],
        )

        # データ書き込み（H4以降）
        if ranking:
            rows = []
            for kw, cond, rate, processed, output, excluded, date in ranking:
                rows.append([kw, cond, f"{rate}%", processed, output, excluded, date])

            end_row = 3 + len(rows)
            worksheet.update(
                range_name=f"H4:N{end_row}",
                values=rows,
                value_input_option='USER_ENTERED',
            )

            # 古いデータが残らないよう、データ範囲の下をクリア
            clear_start = end_row + 1
            if clear_start <= 100:
                empty_rows = [["", "", "", "", "", "", ""]] * (100 - end_row)
                worksheet.update(
                    range_name=f"H{clear_start}:N100",
                    values=empty_rows,
                )

        print(f"\n  [RANKING] キーワードランキング更新: {len(ranking)}件")
        for i, (kw, cond, rate, processed, output, excluded, date) in enumerate(ranking[:5], 1):
            excl_str = f" 除外{excluded}" if excluded > 0 else ""
            print(f"    {i}. {kw}({cond}): {rate}% ({output}/{processed}{excl_str})")
        if len(ranking) > 5:
            print(f"    ... 他{len(ranking) - 5}件")

    except Exception as e:
        print(f"  [WARN] キーワードランキング更新失敗: {e}")
        import traceback
        traceback.print_exc()


def update_sheet_headers(sheet_client) -> bool:
    """
    入力シートのヘッダー行（1行目）をチェックする（読み取り専用）.
    ヘッダーの自動書き換えは行わない。

    Returns:
        ヘッダーが期待通りならTrue
    """
    try:
        worksheet = sheet_client.spreadsheet.worksheet("入力シート")

        # 現在のヘッダーを取得（チェックのみ、書き換えない）
        current_headers = worksheet.row_values(1)
        expected_headers = INPUT_SHEET_COLUMNS

        if current_headers[:len(expected_headers)] == expected_headers:
            print(f"  [INFO] Headers OK ({len(expected_headers)} columns)")
        else:
            print(f"  [WARN] Headers mismatch (expected {len(expected_headers)} cols, found {len(current_headers)} cols)")
            print(f"  [WARN] ヘッダーがコードの定義と異なります（自動修正しません）")
        return True

    except Exception as e:
        print(f"  [WARN] Failed to check headers: {e}")
        return False


def get_next_empty_row(sheet_client) -> int:
    """Get the next empty row number in the input sheet.

    V列（ステータス）を前方検索し、最初の空行を返す。
    V列はデータ行("要確認")・通知行("完了")の両方で値があるため、
    テーブル内の本当の空行を正確に検出できる。
    """
    worksheet = sheet_client.spreadsheet.worksheet("入力シート")
    # V列 = 22番目(1-indexed)。データ行・通知行の両方で値がある列
    col_v_values = worksheet.col_values(22)
    # ヘッダー(index 0)はスキップし、最初の空セルを探す
    for i in range(1, len(col_v_values)):
        if not col_v_values[i] or not col_v_values[i].strip():
            return i + 1  # 0-indexed → 1-indexed row number
    # 全行に値がある場合、最後の行の次
    return len(col_v_values) + 1


def _apply_row_validation(worksheet, row_number: int) -> None:
    """挿入行にV列プルダウンとW列プルダウンを設定する."""
    try:
        sheet_id = worksheet.id
        worksheet.spreadsheet.batch_update({
            "requests": [
                # V列: ステータスプルダウン
                {
                    "setDataValidation": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": row_number - 1,
                            "endRowIndex": row_number,
                            "startColumnIndex": 21,  # V列 (0-indexed)
                            "endColumnIndex": 22,
                        },
                        "rule": {
                            "condition": {
                                "type": "ONE_OF_LIST",
                                "values": [
                                    {"userEnteredValue": v}
                                    for v in ["要確認", "OK", "除外", "エラー", "保留", "完了"]
                                ],
                            },
                            "showCustomUi": True,
                            "strict": False,
                        },
                    }
                },
                # W列: 出品フラグプルダウン
                {
                    "setDataValidation": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": row_number - 1,
                            "endRowIndex": row_number,
                            "startColumnIndex": 22,  # W列 (0-indexed)
                            "endColumnIndex": 23,
                        },
                        "rule": {
                            "condition": {
                                "type": "ONE_OF_LIST",
                                "values": [
                                    {"userEnteredValue": v}
                                    for v in ["出品済", "出品中", "下書き", "様子見"]
                                ],
                            },
                            "showCustomUi": True,
                            "strict": False,
                        },
                    }
                },
            ]
        })
    except Exception as e:
        print(f"  [WARN] プルダウン設定失敗 (row {row_number}): {e}")


def write_result_to_spreadsheet(sheet_client, data: dict):
    """テーブル内に行を追加してリサーチ結果を書き込む."""
    worksheet = sheet_client.spreadsheet.worksheet("入力シート")

    # Prepare row data matching INPUT_SHEET_COLUMNS
    row_data = [""] * 24  # A〜X列：24列固定

    # Map data to columns (24 columns: A-X)
    row_data[0] = now_jst().strftime("%Y-%m-%d")  # A: 日付（日本時間）
    row_data[1] = data.get("keyword", "")  # B: キーワード
    row_data[2] = data.get("category_name", "")  # C: カテゴリ
    # カテゴリ番号（そのまま書き込み）
    cat_id = data.get("category_id", "")
    row_data[3] = str(cat_id) if cat_id else ""  # D: カテゴリ番号
    row_data[4] = data.get("condition", "")  # E: 新品中古

    # ソーシング結果（国内最安①②③）- 商品名、リンク、価格
    sourcing_results = data.get("sourcing_results", [])
    for idx, result in enumerate(sourcing_results[:3]):
        title_col = 5 + (idx * 3)  # 5, 8, 11 (商品名: F, I, L)
        url_col = 6 + (idx * 3)    # 6, 9, 12 (リンク: G, J, M)
        price_col = 7 + (idx * 3)  # 7, 10, 13 (価格: H, K, N)
        row_data[title_col] = result.get("title", "")
        row_data[url_col] = result.get("url", "")
        # 価格が0円の場合は「要確認」と表示
        price = result.get("price", 0)
        row_data[price_col] = str(int(price)) if price > 0 else "要確認"

    # eBay情報
    row_data[14] = data.get("ebay_url", "")  # O: eBayリンク
    # 販売価格・送料は小数第1位までにフォーマット（0も有効な値として扱う）
    ebay_price = data.get("ebay_price")
    ebay_shipping = data.get("ebay_shipping")
    row_data[15] = f"{float(ebay_price):.1f}" if ebay_price is not None and ebay_price != "" else ""  # P: 販売価格
    row_data[16] = f"{float(ebay_shipping):.1f}" if ebay_shipping is not None and ebay_shipping != "" else ""  # Q: 送料

    # 利益計算結果
    row_data[17] = str(data.get("profit_no_rebate", ""))  # R: 還付抜き利益額（円）
    row_data[18] = str(data.get("profit_margin_no_rebate", ""))  # S: 利益率%（還付抜き）
    row_data[19] = str(data.get("profit_with_rebate", ""))  # T: 還付あり利益額（円）
    row_data[20] = str(data.get("profit_margin_with_rebate", ""))  # U: 利益率%（還付あり）

    # ステータスとメモ（出品フラグは空）
    if data.get("error"):
        row_data[21] = "エラー"  # V: ステータス
        row_data[23] = f"ERROR: {data.get('error')}"  # X: メモ
    else:
        row_data[21] = "要確認"  # V: ステータス
        row_data[23] = f"自動処理 {now_jst().strftime('%H:%M:%S')}"  # X: メモ（日本時間）
    # W: 出品フラグは空（ユーザーが手動で入力）

    # insert_rowsで行を物理挿入 → テーブルが構造的に拡張される
    # （values.update/appendではテーブル自動拡張がトリガーされない）
    row_number = get_next_empty_row(sheet_client)
    worksheet.insert_rows([row_data], row=row_number, value_input_option="USER_ENTERED")

    # 挿入行にプルダウン（V列:ステータス、W列:出品フラグ）を設定
    _apply_row_validation(worksheet, row_number)

    print(f"  [WRITE] Inserted at row {row_number} (table insert)")
    return row_number


def main():
    parser = argparse.ArgumentParser(description="eBay Auto Research Pipeline (Pattern②)")
    args = parser.parse_args()

    print(f"="*60)
    print(f"eBay AUTO RESEARCH PIPELINE (Pattern②)")
    print(f"="*60)

    # タイムアウト管理（長時間実行対応）
    pipeline_start_time = time.time()
    MAX_RUNTIME_SECONDS = 6 * 60 * 60  # 6時間（GitHub Actions上限）
    timeout_reached = False

    # SerpAPI使用履歴を追跡
    serpapi_usage_log = []

    def log_serpapi_call(search_type: str, query: str, results_count: int):
        """SerpAPI呼び出しを記録"""
        serpapi_usage_log.append({
            "type": search_type,
            "query": query[:50] + "..." if len(query) > 50 else query,
            "results": results_count
        })

    # Gemini使用量をリセット
    reset_gemini_usage()

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

    # Update headers if needed
    print(f"\n[0/6] Checking spreadsheet headers...")
    update_sheet_headers(sheets_client)

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
    items_per_keyword = max(1, min(100, items_per_keyword))  # Clamp to 1-100
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

    # E列のメインキーワードを取得（ランキング集計用）
    main_keywords = sheets_client.read_main_keywords()
    print(f"  [INFO] Main keywords (E列): {', '.join(main_keywords)}")

    # 処理済みeBay商品IDを取得（重複スキップ用）
    print(f"\n[2.5/6] Loading processed eBay item IDs...")
    processed_ebay_ids = get_processed_ebay_ids(sheets_client)
    print(f"  [INFO] Found {len(processed_ebay_ids)} already processed items")

    # Step 2-5: Process each keyword
    total_processed = 0
    total_profitable = 0
    # スキップ理由別カウント
    skip_reasons: dict[str, int] = {
        "already_processed": 0,  # スプシに既存
        "out_of_stock": 0,       # 在庫切れ
        "negative_profit": 0,    # 利益マイナス
        "no_source": 0,          # 仕入先なし
        "gemini_reject": 0,      # Gemini検証NG
        "limited_product": 0,    # 限定品/プロモ
        "other": 0,              # その他
    }
    keyword_stats: dict[str, dict[str, int]] = {}  # E列キーワード別の集計
    processed_source_urls: set[str] = set()  # 仕入先URL重複チェック用

    for raw_keyword in keywords:
        # タイムアウトチェック（キーワードループ先頭）
        elapsed = time.time() - pipeline_start_time
        if elapsed >= MAX_RUNTIME_SECONDS:
            remaining_kw = len(keywords) - keywords.index(raw_keyword)
            print(f"\n[TIMEOUT] 経過時間 {elapsed/60:.1f}分 >= 制限 {MAX_RUNTIME_SECONDS/60:.0f}分")
            print(f"  残り {remaining_kw} キーワードをスキップしてクリーンアップに移行します")
            timeout_reached = True
            break

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

            # 出力目標数を達成するため、余裕を持って検索（処理済みスキップ分を考慮）
            # スキップが多いため、目標の10倍まで取得可能に
            search_buffer = min(items_per_keyword * 10, 60)
            serpapi_results = serpapi_client.search_sold_items(
                keyword,
                market=market,
                min_price=min_price_local,
                max_results=search_buffer,
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

            # 入力シートに通知行を書き込み
            worksheet = sheets_client.spreadsheet.worksheet("入力シート")
            notify_row = [""] * 24
            notify_row[0] = now_jst().strftime("%Y-%m-%d")
            notify_row[1] = raw_keyword
            notify_row[4] = condition
            notify_row[5] = "eBay検索結果なし"
            notify_row[COL_INDEX["ステータス"]] = "完了"
            notify_row[COL_INDEX["メモ"]] = "eBay検索結果なし（Sold/Active共に0件）"
            row_number = get_next_empty_row(sheets_client)
            worksheet.insert_rows([notify_row], row=row_number, value_input_option="USER_ENTERED")
            _apply_row_validation(worksheet, row_number)
            try:
                worksheet.format(f"A{row_number}:X{row_number}", {
                    "backgroundColor": {"red": 0.0, "green": 0.0, "blue": 0.0},
                    "textFormat": {
                        "foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
                        "bold": True
                    },
                    "wrapStrategy": "OVERFLOW_CELL"
                })
                print(f"  [NOTIFY] Row {row_number}: eBay検索結果なし (黒背景適用)")
            except Exception as e:
                print(f"  [NOTIFY] Row {row_number}: eBay検索結果なし (フォーマット失敗: {e})")

            # 設定シートのキーワードランキングにも記録
            base_kw = _find_base_keyword(raw_keyword, main_keywords)
            if base_kw:
                stats_key = f"{base_kw}|{condition}"
                if stats_key not in keyword_stats:
                    keyword_stats[stats_key] = {"processed": 0, "output": 0}
            continue

        # 出力目標: items_per_keyword 件をスプレッドシートに出力
        # 処理済みスキップや利益不足スキップがあっても、目標数に達するまで次の商品を試す
        items_output_this_keyword = 0
        skipped_this_keyword = 0  # このキーワードでのスキップ数
        output_ebay_titles = []   # このキーワードで出力済みのeBayタイトル（重複検出用）

        for item in active_items:
            # 出力目標に達したら終了
            if items_output_this_keyword >= items_per_keyword:
                break

            # タイムアウトチェック（アイテムループ先頭）
            elapsed = time.time() - pipeline_start_time
            if elapsed >= MAX_RUNTIME_SECONDS:
                print(f"\n  [TIMEOUT] 経過時間 {elapsed/60:.1f}分 → このキーワードの処理を中断")
                timeout_reached = True
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
            # SerpAPIがcategory_idのみ返しcategory_nameが空の場合もあるため両方チェック
            if ebay_item_id and (not category_id or not category_name):
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
                skip_reasons["already_processed"] += 1
                skipped_this_keyword += 1
                continue

            # 同一キーワード内でのタイトル重複チェック（出品者違いの同一商品を排除）
            if ebay_title and output_ebay_titles:
                title_lower = ebay_title.lower().strip()
                is_dup = False
                for prev_title in output_ebay_titles:
                    sim = SequenceMatcher(None, title_lower, prev_title.lower().strip()).ratio()
                    if sim >= 0.85:
                        print(f"\n  [SKIP] Duplicate eBay listing (similarity: {sim:.0%})")
                        print(f"         Current: {ebay_title[:60]}...")
                        print(f"         Prev:    {prev_title[:60]}...")
                        is_dup = True
                        break
                if is_dup:
                    skip_reasons["other"] += 1
                    skipped_this_keyword += 1
                    continue

            # 限定品/プレミアム品はスキップ（一般ECで新品入手困難）
            is_limited, limited_keyword = is_limited_edition_product(ebay_title)
            if is_limited:
                print(f"\n  [SKIP] Limited/Premium product detected: '{limited_keyword}'")
                print(f"         Title: {ebay_title[:60]}...")
                skip_reasons["limited_product"] += 1
                skipped_this_keyword += 1
                continue

            # PSA/BGS/CGC鑑定品はNew条件ではスキップ（鑑定済み=中古扱い）
            if condition == "New":
                ebay_title_lower = ebay_title.lower()
                graded_keywords = ["psa10", "psa 10", "psa9", "psa 9", "psa8", "psa 8",
                                   "bgs10", "bgs 10", "bgs9", "bgs 9",
                                   "cgc10", "cgc 10", "cgc9", "cgc 9",
                                   "graded", "鑑定済"]
                is_graded = any(kw in ebay_title_lower for kw in graded_keywords)
                if is_graded:
                    print(f"\n  [SKIP] Graded/PSA item (not New): {ebay_title[:50]}...")
                    skip_reasons["limited_product"] += 1
                    skipped_this_keyword += 1
                    continue

            # CCG/TCGカード系カテゴリはNew条件ではスキップ（大手ECで新品入手困難）
            # - CCG Individual Cards (183454)
            # - Non-Sport Trading Cards (183050)
            if condition == "New" and category_id:
                card_category_ids = ["183454", "183050"]
                if category_id in card_category_ids:
                    print(f"\n  [SKIP] Card category (hard to source new): {category_name}")
                    print(f"         Title: {ebay_title[:50]}...")
                    skip_reasons["limited_product"] += 1
                    skipped_this_keyword += 1
                    continue

            # 画像URLも取得
            image_url = getattr(item, 'image_url', '') or ''

            # === Gemini画像分析: カード/セット/一番くじ等を早期検出 ===
            # カテゴリスキップされなかった場合でも、画像から判定可能
            if condition == "New" and image_url:
                gemini_analyzer = GeminiClient()
                if gemini_analyzer.is_enabled:
                    image_analysis = gemini_analyzer.analyze_ebay_item_image(
                        image_url=image_url,
                        ebay_title=ebay_title,
                        condition=condition,
                        search_keyword=raw_keyword  # 検索キーワードとの不一致検出用
                    )
                    if image_analysis and image_analysis.should_skip and image_analysis.confidence in ["high", "medium"]:
                        print(f"\n  [SKIP] Gemini image analysis: {image_analysis.skip_reason}")
                        print(f"         Type: {image_analysis.product_type} (confidence: {image_analysis.confidence})")
                        print(f"         Title: {ebay_title[:50]}...")
                        skip_reasons["limited_product"] += 1
                        skipped_this_keyword += 1
                        continue

            print(f"\n  Processing: {ebay_url}")
            print(f"  [INFO] eBay title: {ebay_title[:60]}..." if len(ebay_title) > 60 else f"  [INFO] eBay title: {ebay_title}")
            print(f"  [INFO] eBay price: ${ebay_price:.2f} ({item_currency}) + ${ebay_shipping} shipping")
            if category_name:
                print(f"  [INFO] Category: {category_name} ({category_id})")
            if image_url:
                print(f"  [INFO] Image: {image_url[:60]}...")

            # Step 4: Search domestic sources (3段階検索 - 日本国内サイト限定)
            # 1. 楽天API（無料・高速）→ 見つかればLensスキップ
            # 2. Google Lens画像検索（SerpAPI消費）
            # 3. 英語でWeb検索（日本向け）
            # ※日本語Web検索は成功率2%のため削除（英語の方が11%で高い）

            print(f"\n[4/5] Searching domestic sources...")

            all_sources = []
            best_source = None
            top_sources: List[RankedSource] = []  # トップ3の仕入先
            search_method = ""
            skip_text_search = False  # Lens成功時に後続検索をスキップ
            skip_lens_and_en = False  # アパレル検出時にLens/EN検索をスキップ
            best_similarity_so_far = 0.0  # 最良の類似度を記録

            # === SerpAPI節約: アパレル商品の検出 ===
            # 日本のアパレルブランドはLens/EN検索の成功率が低いため、直接JP検索へ
            JAPANESE_APPAREL_BRANDS = [
                "liz lisa", "lizlisa", "axes femme", "axesfemme",
                "angelic pretty", "angelicpretty", "baby the stars",
                "metamorphose", "innocent world", "emily temple",
                "honey cinnamon", "ank rouge", "secret honey",
                "lolita", "harajuku", "kawaii dress", "kawaii skirt",
                "kawaii blouse", "kawaii coat", "kawaii jacket",
            ]
            title_lower = ebay_title.lower() if ebay_title else ""
            is_japanese_apparel = any(brand in title_lower for brand in JAPANESE_APPAREL_BRANDS)

            if is_japanese_apparel:
                skip_lens_and_en = True
                print(f"  [API節約] 日本アパレルブランド検出 → Lens/EN検索スキップ、JP検索へ")

            # === 1. Gemini翻訳（楽天API用に先に実行）===
            japanese_query = None
            if ebay_title:
                cleaned_title = clean_query_for_shopping(ebay_title)
                gemini_client = GeminiClient()
                if gemini_client.is_enabled and cleaned_title:
                    japanese_query = gemini_client.translate_product_name(cleaned_title)
                    if japanese_query:
                        print(f"  [Step 1] Gemini翻訳: {japanese_query}")
                    else:
                        print(f"  [Step 1] Gemini翻訳失敗")
                if not japanese_query:
                    japanese_query = extract_search_keywords(ebay_title) if ebay_title else keyword
                    print(f"  [Step 1] フォールバック（型番抽出）: {japanese_query}")

            # === 2. 楽天API検索（SerpAPIコスト: 0、最優先）===
            skip_lens = False  # 楽天で見つかったらLensスキップ
            if japanese_query:
                rakuten_client = sourcing_client.rakuten_client
                if rakuten_client and rakuten_client.is_enabled:
                    print(f"  [Step 2] 楽天API検索 (コスト0)")
                    print(f"    検索クエリ: {japanese_query}")
                    rakuten_offers = rakuten_client.search_multiple(japanese_query, max_results=10)
                    print(f"    結果: {len(rakuten_offers)}件")

                    # 0件で長いクエリの場合、Geminiで必須キーワードを抽出してリトライ
                    if not rakuten_offers:
                        words = japanese_query.split()
                        if len(words) > 4:
                            gemini_for_kw = GeminiClient()
                            if gemini_for_kw.is_enabled:
                                short_query = gemini_for_kw.extract_essential_keywords(japanese_query, max_keywords=4)
                                if short_query:
                                    print(f"    [リトライ] Gemini必須KW: {short_query}")
                                    rakuten_offers = rakuten_client.search_multiple(short_query, max_results=10)
                                    print(f"    [リトライ] 結果: {len(rakuten_offers)}件")

                    if rakuten_offers:
                        rakuten_sources = []
                        for offer in rakuten_offers:
                            if offer.source_price_jpy > 0:
                                if not is_valid_source_for_condition(offer.source_site, offer.source_url, condition):
                                    continue
                                rakuten_sources.append(offer)

                        if rakuten_sources:
                            print(f"    --- 候補一覧 (スコア順) ---")
                            scored_sources = []
                            for src in rakuten_sources:
                                sim = calculate_title_similarity(ebay_title, src.title)
                                cond_score = calculate_condition_score(src.title, src.source_site)
                                prio = calculate_source_priority(src.source_site)
                                total = sim * cond_score * prio
                                scored_sources.append((src, sim, cond_score, prio, total))
                            scored_sources.sort(key=lambda x: x[4], reverse=True)

                            for i, (src, sim, cond_score, prio, total) in enumerate(scored_sources[:5]):
                                print(f"    {i+1}. [{src.source_site}] JPY {src.source_price_jpy:,.0f}")
                                print(f"       類似度:{sim:.0%} × 状態:{cond_score:.1f} × 優先:{prio:.1f} = {total:.2f}")
                                print(f"       {src.title[:50]}...")

                            rakuten_top = find_top_matching_sources(
                                ebay_title, rakuten_sources, min_similarity=0.30, top_n=3,
                                category_name=category_name, condition=condition
                            )
                            if rakuten_top:
                                top_sources = rakuten_top
                                best_source = top_sources[0].source
                                best_similarity_so_far = top_sources[0].similarity
                                search_method = "楽天API"
                                print(f"    → 選択: [{best_source.source_site}] (計{len(top_sources)}件)")

                                # 楽天で見つかったらLensとEN検索をスキップ
                                RAKUTEN_SKIP_THRESHOLD = 0.30
                                if best_similarity_so_far >= RAKUTEN_SKIP_THRESHOLD and best_source.source_price_jpy > 0:
                                    skip_lens = True
                                    skip_text_search = True
                                    print(f"    [API節約] 楽天APIで候補あり(類似度:{best_similarity_so_far:.0%}) → Lens/SerpAPIスキップ")
                            else:
                                print(f"    → 類似度閾値(30%)未満のため選択なし")
                        else:
                            print(f"    → 有効な候補なし")
                    else:
                        print(f"    → 結果なし")

            # === 3. Google Lens画像検索 ===
            if serpapi_client.is_enabled and image_url and not best_source and not skip_lens_and_en and not skip_lens:
                print(f"  [Step 3] Google Lens画像検索")
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

                # 価格0円の大手ECアイテムをスクレイピングで再取得
                if all_sources:
                    zero_price_count = len([s for s in all_sources if s.source_price_jpy <= 0])
                    if zero_price_count > 0:
                        print(f"    [Step 3b] 価格0円アイテムを再取得中... ({zero_price_count}件)")
                        scraped_count = try_scrape_zero_price_items(all_sources, max_scrape=5)
                        if scraped_count > 0:
                            print(f"    → {scraped_count}件の価格を取得しました")

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

                    # 画像検索でも類似度チェック（誤爆防止）
                    # 画像一致でも全く関係ない商品の場合があるため
                    MIN_IMAGE_SIMILARITY = 0.35  # 画像検索の類似度閾値

                    # カテゴリベースの除外チェックも適用
                    # 在庫切れ・中古のみの商品も除外
                    # New条件時、中古品も除外
                    valid_sources = []
                    category_excluded_count = 0
                    stock_excluded_count = 0
                    used_excluded_count = 0
                    for src, sim, _, _, total in scored_sources:
                        if sim < MIN_IMAGE_SIMILARITY:
                            continue
                        # 在庫切れ・中古のみを除外（Step 2/3へフォールバック）
                        if not src.in_stock or src.stock_status in ["out_of_stock", "used_only"]:
                            stock_excluded_count += 1
                            continue
                        # New条件時、タイトルに中古キーワードがある商品を除外
                        if condition == "New":
                            cond_score = calculate_condition_score(src.title, src.source_site)
                            if cond_score < 0.5:
                                used_excluded_count += 1
                                continue
                        # アクセサリー/周辺機器の除外
                        is_acc, _ = is_accessory_product(src.title)
                        if is_acc:
                            continue
                        # カテゴリ除外チェック（Plushなのに本/雑誌など）
                        should_exclude, _ = check_category_exclusion(src.title, category_name)
                        if should_exclude:
                            category_excluded_count += 1
                            continue
                        valid_sources.append((src, sim, total))

                    if category_excluded_count > 0 or stock_excluded_count > 0 or used_excluded_count > 0:
                        print(f"    (カテゴリ除外: {category_excluded_count}件, 在庫なし除外: {stock_excluded_count}件, 中古品除外: {used_excluded_count}件)")

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
                        best_similarity_so_far = best_sim
                        print(f"    → 選択: [{best_source.source_site}] JPY {best_source.source_price_jpy:,.0f} (類似度:{best_sim:.0%}, 計{len(top_sources)}件)")
                        search_method = "画像検索"

                        # === SerpAPI節約: Lens成功時の早期終了 ===
                        # 類似度60%以上かつ価格ありなら、後続検索をスキップ
                        # ※40%だと誤爆が多い（画像類似でもタイトル全然違う商品を選んでしまう）
                        LENS_SKIP_THRESHOLD = 0.60
                        if best_sim >= LENS_SKIP_THRESHOLD and best_source.source_price_jpy > 0:
                            skip_text_search = True
                            print(f"    [API節約] 画像検索で良好な候補あり(類似度{best_sim:.0%}) → テキスト検索スキップ")
                    else:
                        print(f"    → 類似度閾値({MIN_IMAGE_SIMILARITY:.0%})未満のため選択なし、次のステップへ")
                else:
                    print(f"    → 有効な仕入先なし、次のステップへ")

            # === 4. 英語でWeb検索（日本向け）===
            # ※日本語Web検索は成功率2%のため削除（Web EN: 11%）
            if not top_sources and serpapi_client.is_enabled and ebay_title and not skip_text_search and not skip_lens_and_en:
                cleaned_query = clean_query_for_shopping(ebay_title)
                print(f"  [Step 4] Web検索 (英語/日本向け)")
                print(f"    整形後: {cleaned_query[:60]}...")
                web_condition = "new" if condition == "New" else "used"
                print(f"    Condition: {web_condition}")

                web_results = serpapi_client.search_google_web_jp(cleaned_query, condition=web_condition, max_results=10)
                log_serpapi_call("Web(EN)", cleaned_query, len(web_results))

                all_sources = []
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

                # 価格0円アイテムをスクレイピングで再取得
                if all_sources:
                    zero_price_count = len([s for s in all_sources if s.source_price_jpy <= 0])
                    if zero_price_count > 0:
                        print(f"    [Step 4b] 価格0円アイテムを再取得中... ({zero_price_count}件)")
                        scraped_count = try_scrape_zero_price_items(all_sources, max_scrape=5)
                        if scraped_count > 0:
                            print(f"    → {scraped_count}件の価格を取得しました")

                if all_sources:
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
                        price_str = f"JPY {src.source_price_jpy:,.0f}" if src.source_price_jpy > 0 else "価格不明"
                        prio_label = "仕入" if prio >= 0.9 else "相場" if prio <= 0.6 else "中"
                        print(f"    {i+1}. [{src.source_site}] {price_str}")
                        print(f"       類似度:{sim:.0%} × 状態:{cond_score:.1f} × 優先:{prio:.1f}({prio_label}) = {total:.2f}")
                        print(f"       {src.title[:50]}...")

                    top_sources = find_top_matching_sources(ebay_title, all_sources, min_similarity=0.3, top_n=3, category_name=category_name, condition=condition)
                    if top_sources:
                        best_source = top_sources[0].source
                        search_method = "英語検索"
                        print(f"    → 選択: [{best_source.source_site}] (計{len(top_sources)}件)")
                    else:
                        print(f"    → 類似度閾値(30%)未満のため選択なし")

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

            # トップ3の仕入先を処理（数量チェック + 価格スクレイピング）
            total_source_price = 0
            similarity = 0.0
            needs_price_check = False  # 大手ECで価格なしの場合
            working_candidates = []  # Gemini検証用に保持

            # eBayタイトルから数量を抽出
            ebay_quantity = extract_quantity_from_title(ebay_title, is_japanese=False)
            if ebay_quantity.is_set:
                qty_str = f"{ebay_quantity.quantity}個" if ebay_quantity.quantity > 0 else "数量不明"
                print(f"\n  [数量チェック] eBay: セット商品 ({qty_str}, パターン: {ebay_quantity.pattern_matched})")
            else:
                print(f"\n  [数量チェック] eBay: 単品")

            # 全ての候補に対して数量チェック + 価格スクレイピング
            if top_sources:
                print(f"\n  [INFO] Processing top {len(top_sources)} sources (数量チェック込み)...")
                candidates_with_qty = []

                for rank, ranked_src in enumerate(top_sources, 1):
                    src = ranked_src.source
                    src_price = src.source_price_jpy + src.source_shipping_jpy

                    # 大手ECかどうか判定
                    is_major_ec = any(domain in src.source_url for domain in ["amazon.co.jp", "rakuten.co.jp", "shopping.yahoo.co.jp"])

                    # 価格0の場合はスクレイピング（価格取得 + 在庫チェック）
                    if src_price <= 0:
                        print(f"    [{rank}位] {src.source_site} - 価格0円、スクレイピング中...")
                        scraped = scrape_price_with_fallback(src.source_url, src_price)
                        src.in_stock = scraped.in_stock
                        src.stock_status = scraped.stock_status
                        if scraped.success and scraped.price > 0:
                            src.source_price_jpy = scraped.price
                            src_price = scraped.price
                            stock_msg = " (在庫切れ)" if not scraped.in_stock else ""
                            print(f"         → JPY {scraped.price:,.0f}{stock_msg} (scraped)")
                        elif not scraped.in_stock:
                            print(f"         → 在庫切れ")
                        else:
                            error_msg = scraped.error_message if scraped.error_message else "価格取得不可"
                            print(f"         → {error_msg}")
                    elif src.stock_status == "unknown":
                        # 全サイト（大手EC含む）の在庫チェック
                        # 価格があっても在庫切れの場合があるため必ず確認
                        print(f"    [{rank}位] {src.source_site} - 在庫チェック中...")
                        scraped = scrape_price_for_url(src.source_url)
                        src.in_stock = scraped.in_stock
                        src.stock_status = scraped.stock_status
                        if not scraped.in_stock:
                            print(f"         → 在庫切れ検出 ({src.stock_status})")
                        else:
                            print(f"         → 在庫OK")

                    # 仕入先タイトルから数量を抽出
                    source_quantity = extract_quantity_from_title(src.title, is_japanese=True)
                    qty_match_score = calculate_quantity_match_score(ebay_quantity, source_quantity)

                    # 数量情報をログ出力
                    if source_quantity.is_set:
                        src_qty_str = f"セット({source_quantity.quantity}個)" if source_quantity.quantity > 0 else "セット"
                    else:
                        src_qty_str = "単品"

                    # 在庫ステータス
                    stock_str = ""
                    if not src.in_stock:
                        stock_str = " [在庫切れ]"
                    elif src.stock_status == "unknown":
                        stock_str = " [在庫不明]"

                    price_str = f"JPY {src_price:,.0f}" if src_price > 0 else "価格不明"
                    print(f"    [{rank}位] {src.source_site} - {price_str}{stock_str}")
                    print(f"         数量: {src_qty_str} (マッチ度: {qty_match_score:.0%})")
                    print(f"         タイトル: {src.title[:45]}...")

                    # === セット商品の価格調整 ===
                    # eBayがセット商品で、仕入先が単品の場合 → 単価×個数
                    adjusted_price = src_price
                    price_note = ""

                    if ebay_quantity.is_set and ebay_quantity.quantity > 0:
                        if not source_quantity.is_set:
                            # 仕入先が単品 → 単価×個数
                            if src_price > 0:
                                adjusted_price = src_price * ebay_quantity.quantity
                                price_note = f"単価{src_price}×{ebay_quantity.quantity}個"
                        elif source_quantity.quantity == 0:
                            # 仕入先もセットだが数量不明 → 単価かセット価格か不明
                            if src_price > 0:
                                price_note = "セット価格か単価か不明"
                                # 価格が極端に安い場合は単価の可能性が高い
                                if src_price < 2000:  # 2000円未満は単価の可能性
                                    adjusted_price = src_price * ebay_quantity.quantity
                                    price_note = f"単価の可能性({src_price}×{ebay_quantity.quantity})"

                    candidates_with_qty.append({
                        "ranked_src": ranked_src,
                        "src_price": src_price,  # 元の価格
                        "adjusted_price": adjusted_price,  # 調整後価格
                        "price_note": price_note,
                        "qty_match": qty_match_score,
                        "source_qty": source_quantity,
                        "in_stock": src.in_stock,  # 在庫あり
                        "stock_status": src.stock_status  # 在庫ステータス
                    })

                # === 候補の選別とランキング ===
                # 1. 数量不一致（0.0）は除外
                # 2. 在庫ありを優先（在庫切れは後回し）
                # 3. 価格ありを優先（0円は後回し）
                # 4. 数量マッチ度×スコアでランキング

                valid_candidates = [c for c in candidates_with_qty if c["qty_match"] > 0.0]

                # 404/アクセス不可なURLは候補から除外（在庫切れとは異なりページ自体が無効）
                dead_statuses = {"not_found", "forbidden", "http_error"}
                dead_candidates = [c for c in valid_candidates if c["stock_status"] in dead_statuses]
                if dead_candidates:
                    print(f"\n  [INFO] 無効URL {len(dead_candidates)}件を除外:")
                    for c in dead_candidates:
                        print(f"         - {c['ranked_src'].source.source_site}: {c['stock_status']} ({c['ranked_src'].source.title[:30]}...)")
                    valid_candidates = [c for c in valid_candidates if c["stock_status"] not in dead_statuses]

                if not valid_candidates:
                    # 全て数量不一致の場合
                    print(f"\n  [WARN] 全候補が数量不一致！eBay: {ebay_quantity.pattern_matched}")
                    for c in candidates_with_qty:
                        print(f"         - {c['ranked_src'].source.source_site}: {c['source_qty'].pattern_matched}")
                    error_reason = "数量不一致"
                    best_source = None
                else:
                    # 在庫ありと在庫切れで分離
                    in_stock_candidates = [c for c in valid_candidates if c["in_stock"]]
                    out_of_stock_candidates = [c for c in valid_candidates if not c["in_stock"]]

                    if out_of_stock_candidates:
                        print(f"\n  [INFO] 在庫切れ{len(out_of_stock_candidates)}件を後回し:")
                        for c in out_of_stock_candidates:
                            print(f"         - {c['ranked_src'].source.source_site}: {c['ranked_src'].source.title[:30]}...")

                    # 在庫ありを優先、全候補在庫切れならスキップ
                    if in_stock_candidates:
                        working_pool = in_stock_candidates
                    else:
                        print(f"\n  [WARN] 全候補が在庫切れ！スキップ")
                        error_reason = "在庫切れ"
                        best_source = None
                        working_pool = []

                    if not working_pool:
                        # 在庫切れで空になった場合はスキップ（error_reason設定済み）
                        pass
                    else:
                        # 価格ありと価格なしで分離
                        priced_candidates = [c for c in working_pool if c["adjusted_price"] > 0]
                        unpriced_candidates = [c for c in working_pool if c["adjusted_price"] <= 0]

                        # 価格ありを優先、なければ価格なしを使用
                        if priced_candidates:
                            working_candidates = priced_candidates
                            if unpriced_candidates:
                                print(f"\n  [INFO] 価格なし{len(unpriced_candidates)}件をスキップ、価格あり{len(priced_candidates)}件を使用")
                        else:
                            working_candidates = unpriced_candidates

                        # 数量マッチ度 × 既存スコアで再ランキング
                        working_candidates.sort(
                            key=lambda c: c["qty_match"] * c["ranked_src"].score,
                            reverse=True
                        )

                        # ベスト候補を選択
                        best_candidate = working_candidates[0]
                        best_source = best_candidate["ranked_src"].source
                        total_source_price = best_candidate["adjusted_price"]  # 調整後価格を使用
                        similarity = best_candidate["ranked_src"].similarity
                        price_note = best_candidate["price_note"]

                        # 選択理由をログ出力
                        if len(candidates_with_qty) != len(valid_candidates):
                            excluded = len(candidates_with_qty) - len(valid_candidates)
                            print(f"\n  [数量チェック結果] {excluded}件を除外、{len(valid_candidates)}件が有効")

                        # 価格確認が必要かどうか
                        if total_source_price <= 0:
                            needs_price_check = True
                            print(f"  [FOUND] via {search_method}: {best_source.source_site} - 要価格確認")
                        else:
                            if price_note:
                                print(f"  [FOUND] via {search_method}: {best_source.source_site} - JPY {total_source_price:.0f} ({price_note})")
                            else:
                                print(f"  [FOUND] via {search_method}: {best_source.source_site} - JPY {total_source_price:.0f}")

                        print(f"  [INFO] Source title: {best_source.title[:50]}..." if len(best_source.title) > 50 else f"  [INFO] Source title: {best_source.title}")
                        print(f"  [INFO] 数量マッチ度: {best_candidate['qty_match']:.0%}")
                        if search_method != "画像検索":
                            print(f"  [INFO] Title similarity: {similarity:.0%}")
                        print(f"  [INFO] URL: {best_source.source_url}")

                        # top_sourcesを有効な候補だけに更新（スプレッドシート出力用）
                        # 価格調整済みの情報も含める
                        top_sources = [c["ranked_src"] for c in working_candidates]
                        # 調整後価格をsourceにも反映（スプレッドシート出力用）
                        for c in working_candidates:
                            c["ranked_src"].source.source_price_jpy = c["adjusted_price"]

            # === 異常低価格チェック ===
            # スクレイピングエラーで極端に安い価格が取れるケースを弾く
            MIN_SOURCE_PRICE_JPY = 500
            if best_source and not error_reason and 0 < total_source_price < MIN_SOURCE_PRICE_JPY:
                print(f"  [SKIP] 仕入値 JPY {total_source_price:.0f} が最低基準 JPY {MIN_SOURCE_PRICE_JPY} 未満 → 価格取得ミスの可能性")
                best_source = None
                error_reason = "価格取得ミス"

            # === Gemini検証: 仕入先が適切かチェック ===
            if best_source and not error_reason:
                gemini_validator = GeminiClient()
                if gemini_validator.is_enabled:
                    print(f"\n  [Gemini検証] 仕入先チェック中...")
                    validation = gemini_validator.validate_source_match(
                        ebay_title=ebay_title,
                        ebay_price_usd=ebay_price,
                        source_title=best_source.title,
                        source_url=best_source.source_url,
                        source_price_jpy=total_source_price,
                        source_site=best_source.source_site,
                        condition=condition
                    )

                    if validation:
                        print(f"    結果: {'OK' if validation.is_valid else 'NG'} ({validation.suggestion})")
                        print(f"    理由: {validation.reason}")
                        if validation.issues:
                            print(f"    問題: {', '.join(validation.issues)}")

                        if validation.suggestion in ("skip", "retry"):
                            # skip/retryの場合、残りの候補を順番にGemini検証
                            reason_label = "別商品" if validation.suggestion == "skip" else "要再試行"
                            print(f"  [Gemini検証] → {reason_label}、次の候補を試行")

                            found_valid = False
                            for retry_idx in range(1, len(working_candidates)):
                                next_candidate = working_candidates[retry_idx]
                                next_src = next_candidate["ranked_src"].source
                                next_price = next_candidate["adjusted_price"]
                                print(f"  [RETRY {retry_idx}] {next_src.source_site} - JPY {next_price:.0f}")

                                # 次の候補もGemini検証
                                retry_validation = gemini_validator.validate_source_match(
                                    ebay_title=ebay_title,
                                    ebay_price_usd=ebay_price,
                                    source_title=next_src.title,
                                    source_url=next_src.source_url,
                                    source_price_jpy=next_price,
                                    source_site=next_src.source_site,
                                    condition=condition
                                )
                                if retry_validation and retry_validation.suggestion == "accept":
                                    print(f"  [RETRY {retry_idx}] Gemini検証OK → 採用")
                                    best_source = next_src
                                    total_source_price = next_price
                                    similarity = next_candidate["ranked_src"].similarity
                                    found_valid = True
                                    break
                                else:
                                    retry_reason = retry_validation.reason[:40] if retry_validation else "検証失敗"
                                    print(f"  [RETRY {retry_idx}] Gemini検証NG: {retry_reason}")

                            if not found_valid:
                                print(f"  [Gemini検証] → 全候補NG、スキップ")
                                best_source = None
                                error_reason = "Gemini検証NG"
                        # accept の場合はそのまま進む
                    else:
                        # Gemini検証失敗（APIエラー等）→ 要確認としてマーク
                        print(f"    [WARN] Gemini検証失敗 → 要確認")
                        error_reason = "要確認: Gemini検証失敗"

            # === 在庫確認: 非大手ECサイトの場合のみ ===
            # Amazon/楽天/Yahooはスクレイピング時に在庫確認済み
            # その他サイトは検索結果の価格を信用しているので、ここで確認
            if best_source and not error_reason:
                source_url_lower = best_source.source_url.lower()
                is_major_ec = any(domain in source_url_lower for domain in [
                    "amazon.co.jp", "rakuten.co.jp", "shopping.yahoo.co.jp"
                ])
                if not is_major_ec:
                    print(f"\n  [在庫確認] {best_source.source_site}...")
                    stock_result = scrape_price_for_url(best_source.source_url)
                    if stock_result:
                        if not stock_result.in_stock:
                            print(f"  [在庫確認] → 在庫切れ検出（{stock_result.stock_status}）")
                            error_reason = "在庫切れ"
                        else:
                            print(f"  [在庫確認] → OK（在庫あり）")
                    else:
                        print(f"  [在庫確認] → 確認失敗（スクレイピングエラー）")

            # Step 5: Calculate profit (with weight estimation)
            profit_no_rebate = 0
            profit_margin_no_rebate = 0
            profit_with_rebate = 0
            profit_margin_with_rebate = 0

            # 仕入先がある場合のみ利益計算（価格確認必要な場合はスキップ）
            if best_source and needs_price_check:
                print(f"\n[5/5] Skipping profit calculation (price confirmation needed)")
                error_reason = "要価格確認"
            elif best_source and best_source.source_url in processed_source_urls:
                print(f"\n[5/5] Skipping (同一仕入先URL既出): {best_source.source_url[:80]}")
                best_source = None
                error_reason = "仕入先URL重複"
            elif best_source:
                print(f"\n[5/5] Calculating profit...")

                # 重量調査: Gemini優先 → フォールバックで固定推定
                adjusted_weight_g = 0
                adjusted_depth = 0.0
                adjusted_width = 0.0
                adjusted_height = 0.0
                weight_source = ""

                # まずGeminiで重量調査を試みる
                gemini_client = GeminiClient()
                if gemini_client.is_enabled:
                    print(f"  [Gemini] 重量調査中: {best_source.title[:40]}...")
                    weight_research = gemini_client.research_product_weight(
                        product_title=best_source.title,
                        product_url=best_source.source_url
                    )
                    if weight_research and weight_research.applied_weight_kg > 0:
                        adjusted_weight_g = weight_research.applied_weight_g
                        adjusted_depth = weight_research.packed_depth_cm
                        adjusted_width = weight_research.packed_width_cm
                        adjusted_height = weight_research.packed_height_cm
                        weight_type = "容積重量" if weight_research.is_volumetric_applied else "実重量"
                        weight_source = f"Gemini調査({weight_type})"
                        print(f"  [Gemini] 商品サイズ: {weight_research.product_depth_cm:.1f}x{weight_research.product_width_cm:.1f}x{weight_research.product_height_cm:.1f}cm, {weight_research.product_weight_kg:.2f}kg")
                        print(f"  [Gemini] 梱包後: {adjusted_depth:.1f}x{adjusted_width:.1f}x{adjusted_height:.1f}cm, {weight_research.packed_weight_kg:.2f}kg")
                        print(f"  [Gemini] 容積重量: {weight_research.volumetric_weight_kg:.2f}kg")
                        print(f"  [Gemini] 適用重量: {weight_research.applied_weight_kg:.2f}kg ({weight_type})")
                    else:
                        print(f"  [Gemini] 重量調査失敗 → フォールバック")

                # Gemini失敗時は固定推定
                if adjusted_weight_g <= 0:
                    product_type = detect_product_type(ebay_title)
                    weight_est = estimate_weight_from_title(ebay_title, ebay_price)

                    # Apply size multiplier from settings
                    adjusted_depth = weight_est.depth_cm * size_multiplier
                    adjusted_width = weight_est.width_cm * size_multiplier
                    adjusted_height = weight_est.height_cm * size_multiplier
                    adjusted_weight_g = weight_est.applied_weight_g
                    weight_source = f"固定推定({weight_est.estimation_basis})"

                    print(f"  [INFO] Product type: {product_type}")

                print(f"  [INFO] Weight: {adjusted_weight_g}g ({weight_source})")
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

                # 仕入先URLを処理済みに登録（赤字でも同じURLの再計算を防止）
                processed_source_urls.add(best_source.source_url)

                # Check if profit meets minimum threshold
                if min_profit_jpy is not None and profit_no_rebate < min_profit_jpy:
                    print(f"  [SKIP] Profit JPY {profit_no_rebate:.0f} is below minimum JPY {min_profit_jpy} → 次の商品へ")
                    skip_reasons["negative_profit"] += 1
                    skipped_this_keyword += 1
                    continue  # スプレッドシートに書き込まず、次の商品を試す
            else:
                print(f"\n[5/5] Skipping profit calculation (no source found)")

            # === eBay最安値アクティブリスティング検索 ===
            # SerpAPIの売済みURLではなく、現在の最安アクティブリスティングを使用
            if best_source and not error_reason and profit_no_rebate > 0:
                print(f"\n  [最安値検索] eBay最安アクティブリスティングを検索中...")
                try:
                    cheapest = ebay_client.find_cheapest_active_listing(
                        ebay_title=ebay_title,
                        sold_price_usd=ebay_price,
                        market=market,
                        item_location=item_location,
                        condition=condition
                    )
                    if cheapest:
                        old_price = ebay_price
                        new_price = cheapest["price"]
                        new_shipping = cheapest["shipping"]
                        new_url = cheapest["url"]
                        sim_pct = cheapest["similarity"]
                        new_item_id_raw = cheapest.get("item_id", "")
                        # Browse APIのitemIdは "v1|123456|0" 形式なので数値部分を抽出
                        new_item_id = new_item_id_raw
                        if "|" in new_item_id_raw:
                            parts = new_item_id_raw.split("|")
                            new_item_id = parts[1] if len(parts) >= 2 else new_item_id_raw

                        # 重複チェック: 同一アクティブリスティングが既に処理済みならスキップ
                        if new_item_id and new_item_id in processed_ebay_ids:
                            print(f"  [最安値検索] Item {new_item_id} は既に処理済み → スキップ")
                            skipped_this_keyword += 1
                            continue

                        if new_price < old_price:
                            print(f"  [最安値検索] より安い出品を発見!")
                            print(f"    旧: ${old_price:.2f} → 新: ${new_price:.2f} (類似度: {sim_pct:.0%})")
                            print(f"    URL: {new_url[:80]}...")
                        else:
                            print(f"  [最安値検索] 最安値: ${new_price:.2f} (類似度: {sim_pct:.0%})")

                        # 常にアクティブリスティングのURL/価格を使用
                        ebay_url = new_url
                        ebay_price = new_price
                        ebay_shipping = new_shipping

                        # 価格が変わった場合は利益を再計算
                        if abs(new_price - old_price) > 0.01:
                            print(f"  [最安値検索] 価格変更のため利益を再計算...")
                            try:
                                search_base_client.write_input_data(
                                    source_price_jpy=total_source_price,
                                    ebay_price_usd=new_price,
                                    ebay_shipping_usd=new_shipping,
                                    ebay_url=new_url,
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
                                    print(f"  [最安値検索] 再計算結果: JPY {profit_no_rebate:.0f} ({profit_margin_no_rebate:.1f}%)")

                                    # 再計算後に利益が最低基準を下回る場合はスキップ
                                    if min_profit_jpy is not None and profit_no_rebate < min_profit_jpy:
                                        print(f"  [SKIP] 再計算後の利益 JPY {profit_no_rebate:.0f} < 最低 JPY {min_profit_jpy} → 次の商品へ")
                                        skipped_this_keyword += 1
                                        continue
                            except Exception as e:
                                print(f"  [WARN] 再計算失敗: {e}")
                    else:
                        print(f"  [最安値検索] アクティブリスティングなし（売済みURLを使用）")
                except Exception as e:
                    print(f"  [WARN] 最安値検索失敗: {e}（売済みURLを使用）")

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

            # 仕入先が取れなかった/在庫切れの場合はスプシに書かずスキップ
            # → 出力件数にカウントせず、次のeBay商品を試す
            skip_errors = ["国内仕入先なし", "類似商品なし", "数量不一致", "Gemini検証NG", "在庫切れ"]
            if error_reason in skip_errors:
                print(f"\n  [SKIP] Not writing to sheet: {error_reason}")
                # スキップ理由別にカウント
                if error_reason == "在庫切れ":
                    skip_reasons["out_of_stock"] += 1
                elif error_reason == "Gemini検証NG":
                    skip_reasons["gemini_reject"] += 1
                else:
                    skip_reasons["no_source"] += 1
                skipped_this_keyword += 1
                continue

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
                "condition": condition,  # 新品中古（New/Used）
                "error": error_reason  # エラー理由（None or 文字列）
            }

            row_num = write_result_to_spreadsheet(sheets_client, result_data)
            total_processed += 1
            items_output_this_keyword += 1  # このキーワードで処理した件数
            if ebay_title:
                output_ebay_titles.append(ebay_title)  # 重複検出用に記録

            # 書き込んだeBay Item IDを処理済みに追加（最安値検索で同一リスティングへの重複書き込みを防止）
            written_item_id = re.search(r'/itm/(\d+)', ebay_url)
            if written_item_id:
                processed_ebay_ids.add(written_item_id.group(1))

            if profit_no_rebate > 0 and not error_reason:
                total_profitable += 1

            if error_reason:
                print(f"  [WRITTEN] Row {row_num} (Error: {error_reason})")
            else:
                print(f"  [SUCCESS] Result written to row {row_num}")

            # Rate limit protection: wait 2 seconds between items
            # Google Sheets API limit is 60 writes/minute
            time.sleep(2)

        # キーワードで有効な出力がなかった場合 or 目標件数未達の場合、通知行を出力
        if items_output_this_keyword < items_per_keyword:
            if items_output_this_keyword == 0:
                print(f"\n  [INFO] No valid items found for keyword: {keyword} (skipped: {skipped_this_keyword})")
                notify_text = f"該当する商品なし"
                msg = f"該当商品なし（{skipped_this_keyword}件スキップ）"
            else:
                print(f"\n  [INFO] All items exhausted for keyword: {keyword}")
                print(f"         Output: {items_output_this_keyword}/{items_per_keyword}, Skipped: {skipped_this_keyword}")
                notify_text = f"これ以上該当する商品なし（{items_output_this_keyword}/{items_per_keyword}件）"
                msg = f"{items_output_this_keyword}/{items_per_keyword}件出力、{skipped_this_keyword}件スキップ"

            # 通知行をテーブル内に追加
            worksheet = sheets_client.spreadsheet.worksheet("入力シート")
            notify_row = [""] * 24  # A〜X列：24列固定
            notify_row[0] = now_jst().strftime("%Y-%m-%d")  # A: 日付
            notify_row[1] = raw_keyword  # B: キーワード
            notify_row[5] = notify_text  # F: 国内最安①商品名（視認性向上）
            notify_row[COL_INDEX["ステータス"]] = "完了"
            notify_row[COL_INDEX["メモ"]] = f"{notify_text} | {msg}"
            row_number = get_next_empty_row(sheets_client)
            worksheet.insert_rows([notify_row], row=row_number, value_input_option="USER_ENTERED")

            # 挿入行にプルダウンを設定
            _apply_row_validation(worksheet, row_number)

            # 黒背景・白文字・折り返しなしのフォーマットを適用
            # 注意: 色はfloat(0.0〜1.0)で指定する必要がある
            try:
                worksheet.format(f"A{row_number}:X{row_number}", {
                    "backgroundColor": {"red": 0.0, "green": 0.0, "blue": 0.0},
                    "textFormat": {
                        "foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
                        "bold": True
                    },
                    "wrapStrategy": "OVERFLOW_CELL"
                })
                print(f"  [NOTIFY] Row {row_number}: {msg} (黒背景適用)")
            except Exception as e:
                print(f"  [NOTIFY] Row {row_number}: {msg} (フォーマット失敗: {e})")

        # キーワード別集計（E列の元キーワード × 商品状態）
        base_kw = _find_base_keyword(raw_keyword, main_keywords)
        if base_kw:
            stats_key = f"{base_kw}|{condition}"
            if stats_key not in keyword_stats:
                keyword_stats[stats_key] = {"processed": 0, "output": 0}
            keyword_stats[stats_key]["processed"] += items_output_this_keyword + skipped_this_keyword
            keyword_stats[stats_key]["output"] += items_output_this_keyword

        # アイテムループでタイムアウトした場合、キーワードループも抜ける
        if timeout_reached:
            break

    # キーワードランキングを設定シートに書き込み（タイムアウト時も必ず実行）
    if keyword_stats:
        excluded_counts = count_excluded_by_keyword(sheets_client, main_keywords)
        update_keyword_ranking(sheets_client, keyword_stats, excluded_counts)

    # Summary
    total_elapsed = time.time() - pipeline_start_time
    print(f"\n{'='*60}")
    if timeout_reached:
        print(f"AUTO RESEARCH COMPLETED (TIMEOUT - {total_elapsed/60:.1f}分経過)")
    else:
        print(f"AUTO RESEARCH COMPLETED ({total_elapsed/60:.1f}分)")
    print(f"{'='*60}")
    print(f"Total processed: {total_processed}")
    print(f"Profitable items: {total_profitable}")

    # スキップ理由の詳細
    total_skipped_count = sum(skip_reasons.values())
    print(f"\n--- Skip Reasons ({total_skipped_count} items) ---")
    if skip_reasons["already_processed"] > 0:
        print(f"  Already processed (in sheet): {skip_reasons['already_processed']}")
    if skip_reasons["out_of_stock"] > 0:
        print(f"  Out of stock: {skip_reasons['out_of_stock']}")
    if skip_reasons["negative_profit"] > 0:
        print(f"  Negative profit: {skip_reasons['negative_profit']}")
    if skip_reasons["no_source"] > 0:
        print(f"  No source found: {skip_reasons['no_source']}")
    if skip_reasons["gemini_reject"] > 0:
        print(f"  Gemini validation NG: {skip_reasons['gemini_reject']}")
    if skip_reasons["limited_product"] > 0:
        print(f"  Limited/Graded/Card: {skip_reasons['limited_product']}")
    if skip_reasons["other"] > 0:
        print(f"  Other: {skip_reasons['other']}")
    if timeout_reached:
        print(f"*** タイムアウトにより一部キーワード未処理 ***")

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

    # Gemini使用履歴サマリー
    gemini_summary = get_gemini_usage_summary()
    if gemini_summary["total_calls"] > 0:
        print(f"\n--- Gemini API Usage Log ({gemini_summary['total_calls']} calls) ---")
        # メソッド別に集計
        for method, count in sorted(gemini_summary["calls_by_method"].items()):
            print(f"  {method}: {count} calls")

        print(f"\n  Token usage (estimated):")
        print(f"    Input:  {gemini_summary['estimated_input_tokens']:,} tokens")
        print(f"    Output: {gemini_summary['estimated_output_tokens']:,} tokens")

        print(f"\n  Cost (estimated):")
        print(f"    USD: ${gemini_summary['estimated_cost_usd']:.4f}")
        print(f"    JPY: {gemini_summary['estimated_cost_jpy']:,}円")

    print(f"{'='*60}")


if __name__ == "__main__":
    main()
