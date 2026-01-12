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
# Format: (base_weight_g, packaging_weight_g, depth_cm, width_cm, height_cm)
CATEGORY_WEIGHTS = {
    # Trading Cards / Collectibles
    "trading_cards": (100, 200, 20, 15, 5),
    "card_game": (100, 200, 20, 15, 5),
    "pokemon": (100, 200, 20, 15, 5),
    "yu-gi-oh": (100, 200, 20, 15, 5),

    # Figures / Models
    "gundam": (500, 800, 35, 25, 15),
    "figure": (400, 600, 30, 20, 20),
    "model_kit": (600, 800, 40, 30, 15),

    # Cosmetics
    "shiseido": (200, 400, 15, 10, 10),
    "senka": (150, 300, 12, 8, 8),
    "cosmetic": (200, 400, 15, 10, 10),

    # Knives / Tools
    "knife": (300, 500, 35, 10, 5),
    "japanese_knife": (400, 600, 40, 12, 5),

    # Character goods
    "hello_kitty": (200, 400, 25, 20, 15),
    "sanrio": (200, 400, 25, 20, 15),

    # Default for unknown categories
    "default": (500, 700, 30, 20, 15),
}


def estimate_weight_from_keyword(keyword: str) -> WeightEstimate:
    """
    Estimate weight and dimensions based on search keyword.

    Args:
        keyword: Search keyword (e.g., "Pokemon Japanese", "Gundam Vintage")

    Returns:
        WeightEstimate with estimated values
    """
    keyword_lower = keyword.lower()

    # Find matching category
    matched_category = "default"
    for category_key in CATEGORY_WEIGHTS.keys():
        if category_key in keyword_lower:
            matched_category = category_key
            break

    base_weight, packaging, depth, width, height = CATEGORY_WEIGHTS[matched_category]

    # Calculate actual weight (product + packaging)
    actual_weight_g = base_weight + packaging

    # Calculate volumetric weight: L x W x H / 5000 (result in kg, convert to g)
    volumetric_weight_kg = (depth * width * height) / 5000
    volumetric_weight_g = int(volumetric_weight_kg * 1000)

    # Apply the larger weight
    if volumetric_weight_g > actual_weight_g:
        applied_weight_g = volumetric_weight_g
        estimation_basis = "volumetric"
    else:
        applied_weight_g = actual_weight_g
        estimation_basis = "actual"

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

    Args:
        price_usd: Item price in USD
        category: Product category hint

    Returns:
        WeightEstimate with estimated values
    """
    # Get base values from category
    base_weight, packaging, depth, width, height = CATEGORY_WEIGHTS.get(
        category.lower(), CATEGORY_WEIGHTS["default"]
    )

    # Adjust based on price (higher price = likely bigger/heavier)
    if price_usd > 200:
        # Large/premium item - add 50% to dimensions
        multiplier = 1.5
        extra_packaging = 1000  # +1kg for large items
    elif price_usd > 100:
        # Medium item - add 25%
        multiplier = 1.25
        extra_packaging = 500  # +0.5kg
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

    # Apply the larger weight
    if volumetric_weight_g > actual_weight_g:
        applied_weight_g = volumetric_weight_g
        estimation_basis = "volumetric"
    else:
        applied_weight_g = actual_weight_g
        estimation_basis = "actual"

    return WeightEstimate(
        actual_weight_g=actual_weight_g,
        depth_cm=round(adjusted_depth, 1),
        width_cm=round(adjusted_width, 1),
        height_cm=round(adjusted_height, 1),
        volumetric_weight_g=volumetric_weight_g,
        applied_weight_g=applied_weight_g,
        estimation_basis=estimation_basis
    )


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
