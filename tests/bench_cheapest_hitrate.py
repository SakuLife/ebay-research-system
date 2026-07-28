"""eBay最安値検索（find_cheapest_active_listing）の打率を実測するベンチマーク.

「同一商品の最安アクティブ出品を見つけられた割合」を測る。
改修の前後で同じ母集団に対して回し、打率の変化を数値で比較するために使う。

使い方:
    # 母集団を作る（eBayから実データを取得してJSONに固定。以後は同じ母集団で比較できる）
    python -m tests.bench_cheapest_hitrate --build --size 30

    # 打率を測る
    python -m tests.bench_cheapest_hitrate --run
    python -m tests.bench_cheapest_hitrate --run --no-gemini   # Gemini無しの素の打率

母集団を固定するのは、実行のたびに対象が変わると改修の効果と
母集団の運不運を切り分けられなくなるため。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

sys.stdout.reconfigure(encoding="utf-8")
# スクリプトとして直接実行できるようにリポジトリルートをパスへ
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from src.ebay_client import EbayClient
from src.gemini_client import GeminiClient

# 母集団の保存先（リポジトリに残して改修前後で同じ対象を使う）
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "cheapest_bench_items.json"

# 母集団を作るときの検索キーワード。
# 実運用の対象（日本から輸出される中古/新品の日本製品）に寄せて広めに散らす
SEED_KEYWORDS = [
    "Zojirushi rice cooker",
    "Pokemon card booster box japanese",
    "Nintendo Switch game japan",
    "Casio G-Shock watch",
    "Tamiya plastic model kit",
    "Bandai Gundam model kit",
    "Sony headphones japan",
    "Shimano fishing reel",
    "Nikon camera lens",
    "Japanese chef knife",
    "Tomica die-cast car",
    "Studio Ghibli figure",
]


def build_fixture(size: int, market: str = "US") -> None:
    """eBayから実データを取得して母集団JSONを作る."""
    ebay = EbayClient()
    items: List[Dict[str, Any]] = []
    per_keyword = max(1, size // len(SEED_KEYWORDS) + 1)

    for kw in SEED_KEYWORDS:
        if len(items) >= size:
            break
        try:
            cands = ebay.search_active_listings(kw, market=market)
        except Exception as e:
            print(f"  [WARN] {kw}: {e}")
            continue

        for c in cands[:per_keyword]:
            if len(items) >= size:
                break
            # itemId は "v1|123|0" 形式。自己マッチ除外に数値部分が要る
            legacy_id = ""
            if "/itm/" in c.ebay_item_url:
                legacy_id = c.ebay_item_url.split("/itm/")[1].split("?")[0]

            items.append({
                "keyword": kw,
                "title": c.ebay_title,
                "price": c.ebay_price,
                "shipping": c.ebay_shipping,
                "url": c.ebay_item_url.split("?")[0],
                "image_url": c.image_url,
                "legacy_id": legacy_id,
            })
        time.sleep(0.3)

    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[OK] 母集団 {len(items)}件を保存: {FIXTURE_PATH}")


def run_bench(use_gemini: bool = True, market: str = "US", limit: int = 0) -> Dict[str, Any]:
    """母集団に対して最安値検索を回し、打率を出す."""
    if not FIXTURE_PATH.exists():
        print(f"[ERROR] 母集団がありません。先に --build を実行してください: {FIXTURE_PATH}")
        sys.exit(1)

    items = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    if limit:
        items = items[:limit]

    ebay = EbayClient()
    gemini = None
    if use_gemini:
        gc = GeminiClient()
        gemini = gc if gc.is_enabled else None
        if gemini is None:
            print("[WARN] Geminiが無効です。--no-gemini と同じ条件で測定されます")

    hits = 0
    cheaper = 0
    results: List[Dict[str, Any]] = []
    started = time.time()

    for idx, it in enumerate(items, 1):
        print(f"\n--- [{idx}/{len(items)}] {it['title'][:60]}")
        try:
            found = ebay.find_cheapest_active_listing(
                ebay_title=it["title"],
                sold_price_usd=it["price"],
                market=market,
                item_location="japan",
                condition="New",
                gemini_client=gemini,
                ebay_image_url=it.get("image_url", ""),
                exclude_item_id=it.get("legacy_id", ""),
            )
        except Exception as e:
            print(f"  [ERROR] {e}")
            found = None

        original_total = it["price"] + it.get("shipping", 0)
        is_hit = found is not None
        is_cheaper = bool(found) and found["total_price_usd"] < original_total - 0.01

        hits += int(is_hit)
        cheaper += int(is_cheaper)
        results.append({
            "title": it["title"],
            "original_total": original_total,
            "hit": is_hit,
            "cheaper": is_cheaper,
            "found_total": found["total_price_usd"] if found else None,
            "similarity": found["similarity"] if found else None,
        })

    elapsed = time.time() - started
    n = len(items)
    summary = {
        "件数": n,
        "同一商品を発見(打率)": f"{hits}/{n} = {hits / n:.0%}" if n else "0",
        "うち元より安い": f"{cheaper}/{n} = {cheaper / n:.0%}" if n else "0",
        "Gemini": "ON" if gemini else "OFF",
        "所要秒": round(elapsed, 1),
    }

    print("\n" + "=" * 60)
    print("打率サマリ")
    print("=" * 60)
    for k, v in summary.items():
        print(f"  {k}: {v}")

    print("\n--- 取りこぼし（同一商品を見つけられなかったもの）---")
    for r in results:
        if not r["hit"]:
            print(f"  MISS: ${r['original_total']:.2f} {r['title'][:70]}")

    return {"summary": summary, "results": results}


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="eBay最安値検索の打率ベンチマーク")
    parser.add_argument("--build", action="store_true", help="母集団を作り直す")
    parser.add_argument("--run", action="store_true", help="打率を測る")
    parser.add_argument("--size", type=int, default=30, help="母集団の件数（--build時）")
    parser.add_argument("--limit", type=int, default=0, help="測定件数を絞る（0=全件）")
    parser.add_argument("--no-gemini", action="store_true", help="Gemini無しで測る")
    parser.add_argument("--out", default="", help="結果JSONの保存先")
    args = parser.parse_args()

    if args.build:
        build_fixture(args.size)
    if args.run:
        data = run_bench(use_gemini=not args.no_gemini, limit=args.limit)
        if args.out:
            Path(args.out).write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"\n[OK] 結果を保存: {args.out}")
    if not args.build and not args.run:
        parser.print_help()


if __name__ == "__main__":
    main()
