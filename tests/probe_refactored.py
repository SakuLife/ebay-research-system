"""改修後のfind_cheapest_active_listingを実データで検証."""

from __future__ import annotations

import os
import re
import sys

import requests
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.ebay_client import EbayClient
from src.gemini_client import GeminiClient
from src.sheets_client import GoogleSheetsClient

ebay = EbayClient()
gemini = GeminiClient()
sc = GoogleSheetsClient("ebaysystem-837d6cedefa5.json", os.getenv("SHEETS_SPREADSHEET_ID"))
ws = sc.spreadsheet.worksheet("入力シート")


def extract_item_id(url: str) -> str:
    m = re.search(r"/itm/(\d+)", url)
    return m.group(1) if m else ""


def fetch_ebay_detail(item_id: str) -> tuple[str, float, str]:
    token = ebay._get_access_token()
    headers = {"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB"}
    r = requests.get(f"{ebay.browse_url}/item/v1|{item_id}|0", headers=headers, timeout=15)
    if r.status_code != 200:
        return "", 0.0, ""
    d = r.json()
    title = d.get("title", "")
    pi = d.get("price", {})
    rates = {"GBP": 1.27, "EUR": 1.09, "USD": 1.0}
    price_usd = float(pi.get("value", 0)) * rates.get(pi.get("currency", "USD"), 1.0)
    img = d.get("image", {}).get("imageUrl", "")
    return title, price_usd, img


target_rows = [647, 656, 273, 577, 540, 552]

for r in target_rows:
    row = ws.row_values(r)
    cur_url = row[14] if len(row) > 14 else ""
    cond = row[4] if len(row) > 4 else "New"
    iid = extract_item_id(cur_url)
    title, price_usd, image = fetch_ebay_detail(iid)
    print(f"\n========== Row {r} ==========")
    print(f"  Title: {title[:100]}")
    print(f"  Cur  : ${price_usd:.2f}  item={iid}  cond={cond}")
    if not title:
        print("  [SKIP] cannot fetch")
        continue
    result = ebay.find_cheapest_active_listing(
        ebay_title=title,
        sold_price_usd=price_usd,
        market="UK",
        item_location="japan",
        condition=cond,
        gemini_client=gemini,
        ebay_image_url=image,
    )
    if result:
        saved = price_usd - result["total_price_usd"]
        mark = "★安い！" if saved > 0.5 else "(同等以上)"
        print(f"  RESULT: ${result['total_price_usd']:.2f} (類似度{result['similarity']:.0%}) {mark}")
        print(f"          → {result['title'][:80]}")
    else:
        print("  RESULT: なし")
