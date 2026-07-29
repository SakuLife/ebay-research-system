"""Profit calculation utilities."""

from __future__ import annotations

from .models import ProfitResult


def max_affordable_source_jpy(
    ebay_price: float,
    ebay_shipping: float,
    fee_rules: dict,
    min_profit_jpy: float = 0.0,
) -> float:
    """その販売価格で、仕入値がいくらまでなら目標利益に届くかを返す.

    `calculate_profit` と同じ式を逆に解いたもの。**必ず同じ係数を使うこと**
    （別々に概算式を持つと、片方だけ古くなって判断が狂う）。

    用途: 有料APIを叩く前の足切り。返り値が0以下なら、どんなに安く仕入れても
    利益が出ないので、仕入先を探すこと自体が無駄になる。
    """
    fx_rate = float(fee_rules.get("fx", {}).get("default_rate", 150.0))
    percent = float(fee_rules.get("fees", {}).get("default", {}).get("percent", 0.12))
    fixed = float(fee_rules.get("fees", {}).get("default", {}).get("fixed", 0.30))
    shipping_jpy = float(fee_rules.get("shipping", {}).get("default_jpy", 800))

    revenue_jpy = (ebay_price + ebay_shipping) * fx_rate
    fees_jpy = revenue_jpy * percent + fixed * fx_rate
    return revenue_jpy - fees_jpy - shipping_jpy - min_profit_jpy


def calculate_profit(
    ebay_price: float,
    ebay_shipping: float,
    source_price_jpy: float,
    fee_rules: dict,
) -> ProfitResult:
    fx_rate = float(fee_rules.get("fx", {}).get("default_rate", 150.0))
    percent = float(fee_rules.get("fees", {}).get("default", {}).get("percent", 0.12))
    fixed = float(fee_rules.get("fees", {}).get("default", {}).get("fixed", 0.30))
    shipping_jpy = float(fee_rules.get("shipping", {}).get("default_jpy", 800))

    revenue_jpy = (ebay_price + ebay_shipping) * fx_rate
    fees_jpy = revenue_jpy * percent + fixed * fx_rate
    cost_jpy = source_price_jpy + shipping_jpy
    profit = revenue_jpy - fees_jpy - cost_jpy
    margin = profit / revenue_jpy if revenue_jpy else 0.0

    return ProfitResult(
        fx_rate=fx_rate,
        estimated_weight_kg=0.8,
        estimated_pkg_cm="30/20/10",
        profit_jpy_no_rebate=round(profit, 2),
        profit_margin_no_rebate=round(margin, 4),
        profit_jpy_with_rebate=round(profit, 2),
        profit_margin_with_rebate=round(margin, 4),
        is_profitable=profit >= 1,
    )
