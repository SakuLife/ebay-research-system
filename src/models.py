"""Data models used across modules."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ListingCandidate:
    candidate_id: str
    search_query: str
    ebay_item_url: str
    ebay_price: float
    ebay_shipping: float
    sold_signal: int
    category_id: str = ""
    category_name: str = ""
    ebay_title: str = ""
    currency: str = "USD"
    image_url: str = ""  # 商品画像URL（Google Lens検索用）
    ebay_condition: str = ""  # eBayアイテムのコンディション（New/Used等）


@dataclass
class SourceOffer:
    source_site: str
    source_url: str
    source_price_jpy: float
    source_shipping_jpy: float
    stock_hint: str
    title: str = ""
    in_stock: bool = True  # 在庫あり（デフォルト）
    stock_status: str = "unknown"  # "in_stock", "out_of_stock", "unknown"
    source_image_url: str = ""  # 仕入先商品画像URL（Gemini画像比較用）


@dataclass
class ProfitResult:
    fx_rate: float
    estimated_weight_kg: float
    estimated_pkg_cm: str
    profit_jpy_no_rebate: float
    profit_margin_no_rebate: float
    profit_jpy_with_rebate: float
    profit_margin_with_rebate: float
    is_profitable: bool


@dataclass
class GptListing:
    title_en: str
    description_en: str
    size_weight_block: str


@dataclass
class ListingRequest:
    candidate_id: str
    title_en: str
    description_en: str
    size_weight_block: str
    price: str
    currency: str


@dataclass
class ListingResult:
    listing_id: str
    listed_url: str
    error_message: str


@dataclass
class CandidateRow:
    candidate_id: str
    created_at: str
    market: str
    status: str
    keyword: str
    ebay_search_query: str
    ebay_item_url: str
    ebay_price: float
    ebay_shipping: float
    ebay_currency: str
    ebay_category_id: str
    ebay_sold_signal: int
    source_site: str
    source_url: str
    source_price_jpy: float
    source_shipping_jpy: float
    stock_hint: str
    fx_rate: float
    estimated_weight_kg: float
    estimated_pkg_cm: str
    profit_jpy_no_rebate: float
    profit_margin_no_rebate: float
    profit_jpy_with_rebate: float
    profit_margin_with_rebate: float
    is_profitable: bool
    title_en: str
    description_en: str
    size_weight_block: str
    gpt_model: str
    gpt_prompt_version: str
    listing_id: str
    listed_url: str
    listed_at: str
    error_message: str


@dataclass
class ListedRow:
    candidate_id: str
    listed_at: str
    listing_id: str
    listed_url: str
    error_message: str
