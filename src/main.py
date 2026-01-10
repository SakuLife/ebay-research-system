"""Entry point for the sourcing and listing pipeline."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import List

from dotenv import load_dotenv

from .config_loader import load_all_configs
from .ebay_client import EbayClient, MockEbayClient
from .sheets_client import LocalSheetsClient
from .sourcing import MockSourcingClient, SourcingClient
from .profit import calculate_profit
from .gpt_listing import GeminiListingGenerator, MockGptListingGenerator
from .validators import is_blocked_listing
from .models import CandidateRow, ListingRequest, ListedRow


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_clients(use_mocks: bool):
    if use_mocks:
        return MockEbayClient(), MockSourcingClient(), MockGptListingGenerator()
    gemini_key = os.getenv("GEMINI_API_KEY")
    gemini_model = os.getenv("GEMINI_MODEL")
    return EbayClient(), SourcingClient(), GeminiListingGenerator(gemini_key, gemini_model)


def main() -> None:
    load_dotenv()
    use_mocks = os.getenv("USE_MOCKS", "1") == "1"
    run_market = os.getenv("RUN_MARKET")

    configs = load_all_configs()
    market_key = run_market or configs.marketplaces.get("default_market")
    market = configs.marketplaces["markets"].get(market_key)
    if not market:
        raise ValueError(f"Unknown market: {market_key}")

    ebay_client, sourcing_client, gpt_generator = build_clients(use_mocks)
    sheets = LocalSheetsClient(base_dir="data")

    candidates: List[CandidateRow] = []
    for keyword in configs.hotwords.get("keywords", []):
        listings = ebay_client.search_completed(keyword=keyword, market=market_key)
        for listing in listings:
            if is_blocked_listing(listing, configs.categories):
                continue
            offer = sourcing_client.search_best_offer(listing)
            if not offer:
                continue

            profit = calculate_profit(
                ebay_price=listing.ebay_price,
                ebay_shipping=listing.ebay_shipping,
                source_price_jpy=offer.source_price_jpy,
                fee_rules=configs.fee_rules,
            )
            if not profit.is_profitable:
                continue

            candidates.append(
                CandidateRow(
                    candidate_id=listing.candidate_id,
                    created_at=utc_now_iso(),
                    market=market_key,
                    status="NEW",
                    keyword=keyword,
                    ebay_search_query=listing.search_query,
                    ebay_item_url=listing.ebay_item_url,
                    ebay_price=listing.ebay_price,
                    ebay_shipping=listing.ebay_shipping,
                    ebay_currency=market["currency"],
                    ebay_category_id="",
                    ebay_sold_signal=listing.sold_signal,
                    source_site=offer.source_site,
                    source_url=offer.source_url,
                    source_price_jpy=offer.source_price_jpy,
                    source_shipping_jpy=offer.source_shipping_jpy,
                    stock_hint=offer.stock_hint,
                    fx_rate=profit.fx_rate,
                    estimated_weight_kg=profit.estimated_weight_kg,
                    estimated_pkg_cm=profit.estimated_pkg_cm,
                    profit_jpy_no_rebate=profit.profit_jpy_no_rebate,
                    profit_margin_no_rebate=profit.profit_margin_no_rebate,
                    profit_jpy_with_rebate=profit.profit_jpy_with_rebate,
                    profit_margin_with_rebate=profit.profit_margin_with_rebate,
                    is_profitable=profit.is_profitable,
                    title_en="",
                    description_en="",
                    size_weight_block="",
                    gpt_model="",
                    gpt_prompt_version="v1",
                    listing_id="",
                    listed_url="",
                    listed_at="",
                    error_message="",
                )
            )

    if candidates:
        sheets.append_candidates(candidates)

    approved = sheets.load_approved_pending()
    for row in approved:
        listing_text = gpt_generator.generate_listing(
            title=row.get("keyword", ""),
            description=row.get("ebay_search_query", ""),
        )
        request = ListingRequest(
            candidate_id=row.get("candidate_id", ""),
            title_en=listing_text.title_en,
            description_en=listing_text.description_en,
            size_weight_block=listing_text.size_weight_block,
            price=row.get("ebay_price", ""),
            currency=row.get("ebay_currency", ""),
        )
        result = ebay_client.create_and_publish_listing(request)

        listed = ListedRow(
            candidate_id=row.get("candidate_id", ""),
            listed_at=utc_now_iso(),
            listing_id=result.listing_id,
            listed_url=result.listed_url,
            error_message=result.error_message,
        )
        sheets.append_listed(listed)


if __name__ == "__main__":
    main()
