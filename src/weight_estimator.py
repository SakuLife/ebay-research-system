"""Weight and dimension estimation utilities.

This module estimates shipping weight based on product category and price.
The estimation follows these rules:
1. Actual weight = product weight + packaging weight (0.5-2kg)
2. Volumetric weight = L x W x H / 5000
3. Applied weight = max(actual weight, volumetric weight)
"""

from typing import Tuple, Optional
from dataclasses import dataclass


@dataclass
class WeightEstimate:
    """Weight estimation result."""
    actual_weight_g: int  # 梱包後実重量（グラム）
    depth_cm: float  # 奥行き
    width_cm: float  # 幅
    height_cm: float  # 高さ
    volumetric_weight_g: int  # 容積重量（グラム）
    applied_weight_g: int  # 適用重量（大きい方）
    estimation_basis: str  # "volumetric" or "actual"


# Category-based weight estimates (rough defaults)
# Format: (base_weight_g, packaging_weight_g, depth_cm, width_cm, height_cm, max_weight_g)
# max_weight_g: カテゴリごとの上限（容積重量で過大評価されるのを防ぐ）
CATEGORY_WEIGHTS = {
    # Trading Cards - Single / Promo / Graded (軽い、上限あり)
    "psa": (50, 150, 15, 10, 3, 400),  # PSAスラブ
    "cgc": (50, 150, 15, 10, 3, 400),  # CGCスラブ
    "bgs": (50, 150, 15, 10, 3, 400),  # BGSスラブ
    "promo": (30, 100, 15, 10, 2, 300),  # プロモカード
    "single": (20, 80, 12, 8, 1, 200),  # シングルカード

    # Trading Cards - Box / Collection (やや重い)
    "booster_box": (400, 400, 25, 20, 10, 1500),
    "collection": (300, 400, 30, 25, 10, 2000),
    "display": (500, 500, 35, 25, 15, 2500),

    # Trading Cards - General (上限600g)
    "trading_cards": (100, 200, 20, 15, 5, 600),
    "card_game": (100, 200, 20, 15, 5, 600),
    "pokemon": (100, 200, 20, 15, 5, 600),
    "yu-gi-oh": (100, 200, 20, 15, 5, 600),
    "one_piece": (100, 200, 20, 15, 5, 600),
    "tcg": (100, 200, 20, 15, 5, 600),

    # Figures / Models
    "gundam": (500, 800, 35, 25, 15, 5000),
    "figure": (400, 600, 30, 20, 20, 4000),
    "model_kit": (600, 800, 40, 30, 15, 5000),

    # Cosmetics
    "shiseido": (200, 400, 15, 10, 10, 1500),
    "senka": (150, 300, 12, 8, 8, 1000),
    "cosmetic": (200, 400, 15, 10, 10, 1500),

    # Knives / Tools
    "knife": (300, 500, 35, 10, 5, 2000),
    "japanese_knife": (400, 600, 40, 12, 5, 2500),

    # Character goods
    "hello_kitty": (200, 400, 25, 20, 15, 2000),
    "sanrio": (200, 400, 25, 20, 15, 2000),

    # Default for unknown categories
    "default": (500, 700, 30, 20, 15, 5000),
}


# カード系カテゴリを判定するためのキーワード
CARD_CATEGORY_KEYWORDS = [
    "psa", "cgc", "bgs", "sgc",  # グレーディング
    "promo", "プロモ",
    "pokemon", "ポケモン", "ポケカ",
    "yu-gi-oh", "yugioh", "遊戯王",
    "one piece", "ワンピース",
    "trading card", "tcg", "ccg",
    "card game",
]

# 商品タイプ判定キーワード
SINGLE_CARD_KEYWORDS = ["psa", "cgc", "bgs", "sgc", "promo", "single", "/"]  # "/"はカード番号
BOX_KEYWORDS = ["box", "booster", "display", "case", "collection", "bundle", "lot"]


def detect_product_type(title: str) -> str:
    """
    商品タイトルから商品タイプを判定する.

    Returns:
        "single_card", "graded_card", "box", "collection", or "general"
    """
    title_lower = title.lower()

    # グレーディングカード（PSA, CGC等）
    if any(kw in title_lower for kw in ["psa", "cgc", "bgs", "sgc"]):
        return "graded_card"

    # シングルカード（カード番号あり、プロモ等）
    if "/" in title and any(kw in title_lower for kw in CARD_CATEGORY_KEYWORDS):
        return "single_card"
    if "promo" in title_lower:
        return "single_card"

    # ボックス/コレクション
    if any(kw in title_lower for kw in BOX_KEYWORDS):
        return "box"

    # カード系だがタイプ不明
    if any(kw in title_lower for kw in CARD_CATEGORY_KEYWORDS):
        return "general_card"

    return "general"


def estimate_weight_from_keyword(keyword: str) -> WeightEstimate:
    """
    Estimate weight and dimensions based on search keyword.

    Args:
        keyword: Search keyword (e.g., "Pokemon Japanese", "Gundam Vintage")

    Returns:
        WeightEstimate with estimated values
    """
    keyword_lower = keyword.lower()

    # Find matching category (優先順位: 具体的 > 一般的)
    matched_category = "default"

    # まず具体的なカテゴリをチェック
    priority_categories = ["psa", "cgc", "bgs", "promo", "booster_box", "display", "collection"]
    for cat in priority_categories:
        if cat in keyword_lower:
            matched_category = cat
            break

    # 見つからなければ一般カテゴリ
    if matched_category == "default":
        for category_key in CATEGORY_WEIGHTS.keys():
            if category_key in keyword_lower:
                matched_category = category_key
                break

    cat_data = CATEGORY_WEIGHTS[matched_category]
    base_weight, packaging, depth, width, height = cat_data[:5]
    max_weight = cat_data[5] if len(cat_data) > 5 else 5000

    # Calculate actual weight (product + packaging)
    actual_weight_g = base_weight + packaging

    # Calculate volumetric weight: L x W x H / 5000 (result in kg, convert to g)
    volumetric_weight_kg = (depth * width * height) / 5000
    volumetric_weight_g = int(volumetric_weight_kg * 1000)

    # Apply the larger weight, but cap at max_weight
    raw_applied = max(actual_weight_g, volumetric_weight_g)
    applied_weight_g = min(raw_applied, max_weight)

    if volumetric_weight_g > actual_weight_g:
        estimation_basis = "volumetric"
    else:
        estimation_basis = "actual"

    # 上限が適用された場合は記録
    if raw_applied > max_weight:
        estimation_basis += f"_capped({max_weight}g)"

    return WeightEstimate(
        actual_weight_g=actual_weight_g,
        depth_cm=depth,
        width_cm=width,
        height_cm=height,
        volumetric_weight_g=volumetric_weight_g,
        applied_weight_g=applied_weight_g,
        estimation_basis=estimation_basis
    )


def estimate_weight_from_price(price_usd: float, category: str = "default") -> WeightEstimate:
    """
    Estimate weight based on price range and category.

    Higher priced items tend to be larger/heavier (sets, special editions).
    But for card categories, weight is capped to realistic values.

    Args:
        price_usd: Item price in USD
        category: Product category hint

    Returns:
        WeightEstimate with estimated values
    """
    # Get base values from category
    cat_data = CATEGORY_WEIGHTS.get(category.lower(), CATEGORY_WEIGHTS["default"])
    base_weight, packaging, depth, width, height = cat_data[:5]
    max_weight = cat_data[5] if len(cat_data) > 5 else 5000

    # カード系かどうか判定
    is_card_category = category.lower() in ["psa", "cgc", "bgs", "promo", "single",
                                             "trading_cards", "card_game", "pokemon",
                                             "yu-gi-oh", "one_piece", "tcg"]

    # Adjust based on price (higher price = likely bigger/heavier)
    # ただしカード系は価格が高くても軽いまま（レアカードは高額だが軽い）
    if is_card_category:
        # カード系は価格で重量を増やさない
        multiplier = 1.0
        extra_packaging = 0
    elif price_usd > 200:
        # Large/premium item - add 50% to dimensions
        multiplier = 1.5
        extra_packaging = 500  # +0.5kg for large items（1kgから減少）
    elif price_usd > 100:
        # Medium item - add 25%
        multiplier = 1.25
        extra_packaging = 300  # +0.3kg
    elif price_usd > 50:
        # Standard item
        multiplier = 1.0
        extra_packaging = 0
    else:
        # Small item - reduce by 20%
        multiplier = 0.8
        extra_packaging = 0

    adjusted_depth = depth * multiplier
    adjusted_width = width * multiplier
    adjusted_height = height * multiplier
    actual_weight_g = int((base_weight + packaging + extra_packaging) * multiplier)

    # Calculate volumetric weight
    volumetric_weight_kg = (adjusted_depth * adjusted_width * adjusted_height) / 5000
    volumetric_weight_g = int(volumetric_weight_kg * 1000)

    # Apply the larger weight, but cap at max_weight
    raw_applied = max(actual_weight_g, volumetric_weight_g)
    applied_weight_g = min(raw_applied, max_weight)

    if volumetric_weight_g > actual_weight_g:
        estimation_basis = "volumetric"
    else:
        estimation_basis = "actual"

    # 上限が適用された場合は記録
    if raw_applied > max_weight:
        estimation_basis += f"_capped({max_weight}g)"

    return WeightEstimate(
        actual_weight_g=min(actual_weight_g, max_weight),
        depth_cm=round(adjusted_depth, 1),
        width_cm=round(adjusted_width, 1),
        height_cm=round(adjusted_height, 1),
        volumetric_weight_g=volumetric_weight_g,
        applied_weight_g=applied_weight_g,
        estimation_basis=estimation_basis
    )


def estimate_weight_from_title(title: str, price_usd: float = 0) -> WeightEstimate:
    """
    商品タイトルと価格から重量を推定する.
    タイトルから商品タイプを判定し、適切なカテゴリで推定.

    Args:
        title: 商品タイトル
        price_usd: 価格（USD）

    Returns:
        WeightEstimate
    """
    product_type = detect_product_type(title)

    # 商品タイプに応じたカテゴリを選択
    type_to_category = {
        "graded_card": "psa",
        "single_card": "promo",
        "box": "booster_box",
        "general_card": "trading_cards",
        "general": "default",
    }

    category = type_to_category.get(product_type, "default")

    # キーワードからより具体的なカテゴリを探す
    title_lower = title.lower()
    for specific_cat in ["pokemon", "yu-gi-oh", "one_piece", "gundam", "figure"]:
        if specific_cat in title_lower:
            # カード系の具体的なカテゴリが見つかっても、タイプに応じて上書きしない
            if product_type in ["graded_card", "single_card"]:
                break
            category = specific_cat
            break

    return estimate_weight_from_price(price_usd, category)


def calculate_volumetric_weight(depth_cm: float, width_cm: float, height_cm: float) -> float:
    """
    Calculate volumetric weight in kg.

    Formula: L x W x H / 5000

    Args:
        depth_cm: Depth in cm
        width_cm: Width in cm
        height_cm: Height in cm

    Returns:
        Volumetric weight in kg (rounded to 2 decimal places)
    """
    return round((depth_cm * width_cm * height_cm) / 5000, 2)


def get_applied_weight(
    actual_weight_kg: float,
    depth_cm: float,
    width_cm: float,
    height_cm: float
) -> Tuple[float, str]:
    """
    Determine which weight to apply for shipping calculation.

    Args:
        actual_weight_kg: Actual packed weight in kg
        depth_cm, width_cm, height_cm: Dimensions in cm

    Returns:
        Tuple of (applied_weight_kg, basis_str)
        basis_str is either "actual" or "volumetric"
    """
    volumetric = calculate_volumetric_weight(depth_cm, width_cm, height_cm)

    if volumetric > actual_weight_kg:
        return (volumetric, "volumetric")
    else:
        return (actual_weight_kg, "actual")
