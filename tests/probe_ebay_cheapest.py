"""指摘行のeBay最安値検索を再現して、現ロジックと拡張ロジック（類似度緩和・Gemini優先クエリ）を比較する検証スクリプト."""

from __future__ import annotations

import os
import re
import sys
from difflib import SequenceMatcher
from typing import Any

import requests
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

load_dotenv()

# プロジェクトのsrcをimport
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.ebay_client import EbayClient
from src.sheets_client import GoogleSheetsClient

try:
    from src.gemini_client import GeminiClient
    gemini = GeminiClient() if os.getenv("GEMINI_API_KEY") else None
except Exception as e:
    print(f"Gemini init failed: {e}")
    gemini = None

ebay = EbayClient()
sc = GoogleSheetsClient("ebaysystem-837d6cedefa5.json", os.getenv("SHEETS_SPREADSHEET_ID"))
ws = sc.spreadsheet.worksheet("入力シート")


def extract_item_id(url: str) -> str:
    m = re.search(r"/itm/(\d+)", url)
    return m.group(1) if m else ""


def fetch_ebay_title(item_id: str, market: str = "UK") -> tuple[str, float, str]:
    """eBayタイトル/価格/画像を取得."""
    token = ebay._get_access_token()
    marketplace_map = {"UK": "EBAY_GB", "US": "EBAY_US"}
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": marketplace_map.get(market, "EBAY_GB"),
    }
    url = f"{ebay.browse_url}/item/v1|{item_id}|0"
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code != 200:
            return "", 0.0, ""
        data = r.json()
        title = data.get("title", "")
        price_info = data.get("price", {})
        price = float(price_info.get("value", 0))
        currency = price_info.get("currency", "USD")
        rates = {"GBP": 1.27, "EUR": 1.09, "USD": 1.0}
        price_usd = price * rates.get(currency, 1.0)
        image = data.get("image", {}).get("imageUrl", "")
        return title, price_usd, image
    except Exception:
        return "", 0.0, ""


def gemini_short_query(title: str) -> str:
    """Gemini短縮クエリを生成（5〜8語のブランド+型番）."""
    if not gemini or not hasattr(gemini, "model"):
        return ""
    prompt = f"""eBayの商品タイトルから、同じ商品の別出品を検索するための最短キーワードを生成してください。

ルール:
- ブランド名 + 商品名/型番 のみ（5〜8語以内）
- 数量・サイズ・状態説明・送料情報は除外
- 英語のまま出力

商品タイトル: {title}

検索キーワード:"""
    try:
        resp = gemini.model.generate_content(prompt)
        q = resp.text.strip().split("\n")[0].strip()
        return q if 5 <= len(q) <= 100 else ""
    except Exception as e:
        print(f"  [Gemini] error: {e}")
        return ""


def search_listings(query: str, market: str, condition: str, item_location: str = "japan", limit: int = 200) -> list[dict]:
    """Browse APIで検索."""
    token = ebay._get_access_token()
    marketplace_map = {"UK": "EBAY_GB", "US": "EBAY_US"}
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": marketplace_map.get(market, "EBAY_GB"),
    }
    filter_parts = ["buyingOptions:{FIXED_PRICE}"]
    if condition == "New":
        filter_parts.append("conditionIds:{1000}")
    if item_location == "japan":
        filter_parts.append("itemLocationCountry:JP")
    params = {
        "q": query,
        "sort": "price",
        "limit": limit,
        "filter": ",".join(filter_parts),
    }
    url = f"{ebay.browse_url}/item_summary/search"
    try:
        r = requests.get(url, headers=headers, params=params, timeout=20)
        if r.status_code != 200:
            print(f"    [HTTP {r.status_code}] {r.text[:120]}")
            return []
        return r.json().get("itemSummaries", [])
    except Exception as e:
        print(f"    [ERROR] {e}")
        return []


def to_usd(price_local: float, currency: str) -> float:
    rates = {"GBP": 1.27, "EUR": 1.09, "USD": 1.0}
    return price_local * rates.get(currency, 1.0)


def current_query(title: str) -> str:
    """現行のノイズ除去クエリを再現."""
    noise = {
        "new", "used", "brand", "free", "shipping", "ship", "from", "japan",
        "japanese", "authentic", "genuine", "original", "official", "sealed",
        "rare", "vintage", "limited", "edition", "oem", "nib", "nwt", "nwb",
        "f/s", "fs", "mint", "box", "with", "and", "the", "for",
    }
    words = title.split()
    filtered = [w for w in words if w.lower().strip("!.,()[]【】") not in noise]
    q = " ".join(filtered) if filtered else title
    if len(q) > 80:
        q = " ".join(filtered[:10])
    return q


def find_cheapest(
    items: list[dict],
    ebay_title: str,
    sim_min: float,
    sim_auto: float,
    ebay_image: str,
    use_gemini: bool,
    exclude_item_id: str = "",
) -> dict | None:
    """同一商品候補の最安値を返す."""
    ebay_lower = ebay_title.lower().strip()
    best = None
    matched = 0
    for it in items:
        item_id = it.get("itemId", "")
        if exclude_item_id and exclude_item_id in item_id:
            continue
        title = it.get("title", "")
        sim = SequenceMatcher(None, ebay_lower, title.lower().strip()).ratio()
        if sim < sim_min:
            continue
        # auto未満はGemini画像比較
        if sim < sim_auto:
            if not use_gemini or not gemini:
                continue
            cand_img = it.get("image", {}).get("imageUrl", "")
            if not cand_img:
                continue
            try:
                ok = gemini.compare_product_images(
                    ebay_image_url=ebay_image,
                    source_image_url=cand_img,
                    ebay_title=ebay_title,
                    source_title=title,
                )
            except Exception:
                ok = None
            if ok is not True:
                continue
        matched += 1
        pi = it.get("price", {})
        price_usd = to_usd(float(pi.get("value", 0)), pi.get("currency", "USD"))
        ship_info = (it.get("shippingOptions", [{}])[0] if it.get("shippingOptions") else {}).get("shippingCost", {})
        ship_usd = to_usd(float(ship_info.get("value", 0)), ship_info.get("currency", "USD"))
        total = price_usd + ship_usd
        if total <= 0:
            continue
        if best is None or total < best["total"]:
            best = {
                "item_id": item_id,
                "title": title,
                "price": price_usd,
                "ship": ship_usd,
                "total": total,
                "sim": sim,
                "url": it.get("itemWebUrl", ""),
            }
    return best, matched


def probe(row_num: int) -> None:
    print(f"\n========== Row {row_num} ==========")
    row = ws.row_values(row_num)
    cur_url = row[14] if len(row) > 14 else ""
    cur_price = row[16] if len(row) > 16 else ""
    condition = row[4] if len(row) > 4 else "New"
    item_id = extract_item_id(cur_url)
    if not item_id:
        print("  [SKIP] cannot extract item id")
        return
    title, price_usd, image = fetch_ebay_title(item_id)
    if not title:
        print(f"  [SKIP] cannot fetch title for {item_id}")
        return
    print(f"  Title    : {title[:100]}")
    print(f"  Current  : ${cur_price} (sheet) / ${price_usd:.2f} (live)  item={item_id}")
    print(f"  Cond/Img : {condition} / {bool(image)}")

    # Strategy A: 現行クエリ(noise除去)
    q_curr = current_query(title)
    items_curr = search_listings(q_curr, "UK", condition)
    # Strategy B: Gemini短縮クエリ
    q_gem = gemini_short_query(title)
    items_gem = search_listings(q_gem, "UK", condition) if q_gem else []

    print(f"\n  [現行クエリ ] q={q_curr[:80]!r}  → {len(items_curr)}件")
    print(f"  [Gemini   ] q={q_gem[:80]!r}  → {len(items_gem)}件")

    # 候補プールを統合（重複除去）
    seen = set()
    pool = []
    for src_items in (items_curr, items_gem):
        for it in src_items:
            iid = it.get("itemId", "")
            if iid in seen:
                continue
            seen.add(iid)
            pool.append(it)

    print(f"  [プール  ] 重複除去後 = {len(pool)}件")

    # 各設定で最安値を探す
    settings = [
        ("現行(sim>=50%, 30-50%はGemini, 現行Q)", 0.30, 0.50, True, items_curr),
        ("拡張1(sim>=30%, 全てGemini判定, プール)", 0.20, 0.30, True, pool),
        ("拡張2(sim>=15%, 全てGemini, プール)", 0.10, 0.20, True, pool),
    ]
    for name, sim_min, sim_auto, use_g, items in settings:
        best, matched = find_cheapest(items, title, sim_min, sim_auto, image, use_g, exclude_item_id=item_id)
        if best:
            saved = price_usd - best["total"]
            mark = "★安い！" if saved > 0.5 else "(同等)"
            print(f"  {name}")
            print(f"    matched={matched} 最安=${best['total']:.2f} (sim={best['sim']:.0%}) {mark}")
            print(f"    → {best['title'][:80]}")
            print(f"    → item={best['item_id']}")
        else:
            print(f"  {name}: マッチなし (matched=0)")


if __name__ == "__main__":
    # 代表5行: 異なるジャンル
    target_rows = [647, 568, 656, 273, 577]
    for r in target_rows:
        try:
            probe(r)
        except Exception as e:
            print(f"\n[Row {r}] ERROR: {e}")
