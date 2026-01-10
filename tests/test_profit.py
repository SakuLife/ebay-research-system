from src.profit import calculate_profit


def test_profit_positive():
    """Test profit calculation with positive profit."""
    result = calculate_profit(
        ebay_price=60.0,
        ebay_shipping=10.0,
        source_price_jpy=2000.0,
        fee_rules={
            "fx": {"default_rate": 150.0},
            "fees": {"default": {"percent": 0.12, "fixed": 0.30}},
            "shipping": {"default_jpy": 800},
        },
    )
    assert result.profit_jpy_no_rebate > 0
    assert result.is_profitable is True
    assert result.fx_rate == 150.0


def test_profit_negative():
    """Test profit calculation with negative profit."""
    result = calculate_profit(
        ebay_price=10.0,
        ebay_shipping=2.0,
        source_price_jpy=5000.0,
        fee_rules={
            "fx": {"default_rate": 150.0},
            "fees": {"default": {"percent": 0.12, "fixed": 0.30}},
            "shipping": {"default_jpy": 800},
        },
    )
    assert result.profit_jpy_no_rebate < 0
    assert result.is_profitable is False


def test_profit_zero_revenue():
    """Test profit calculation with zero revenue (edge case)."""
    result = calculate_profit(
        ebay_price=0.0,
        ebay_shipping=0.0,
        source_price_jpy=1000.0,
        fee_rules={
            "fx": {"default_rate": 150.0},
            "fees": {"default": {"percent": 0.12, "fixed": 0.30}},
            "shipping": {"default_jpy": 800},
        },
    )
    assert result.profit_jpy_no_rebate < 0
    assert result.profit_margin_no_rebate == 0.0
    assert result.is_profitable is False


def test_profit_default_values():
    """Test profit calculation with default fee rules."""
    result = calculate_profit(
        ebay_price=50.0,
        ebay_shipping=5.0,
        source_price_jpy=3000.0,
        fee_rules={},  # Empty rules should use defaults
    )
    assert result.fx_rate == 150.0  # default
    assert result.profit_jpy_no_rebate is not None
    assert result.is_profitable is not None


def test_profit_high_fees():
    """Test profit calculation with high fees."""
    result = calculate_profit(
        ebay_price=100.0,
        ebay_shipping=10.0,
        source_price_jpy=5000.0,
        fee_rules={
            "fx": {"default_rate": 150.0},
            "fees": {"default": {"percent": 0.25, "fixed": 5.00}},
            "shipping": {"default_jpy": 2000},
        },
    )
    assert result.profit_jpy_no_rebate is not None
    assert isinstance(result.is_profitable, bool)


def test_profit_low_fx_rate():
    """Test profit calculation with low FX rate."""
    result = calculate_profit(
        ebay_price=100.0,
        ebay_shipping=10.0,
        source_price_jpy=5000.0,
        fee_rules={
            "fx": {"default_rate": 100.0},
            "fees": {"default": {"percent": 0.12, "fixed": 0.30}},
            "shipping": {"default_jpy": 800},
        },
    )
    assert result.fx_rate == 100.0
    assert result.profit_jpy_no_rebate is not None


def test_profit_margin_calculation():
    """Test that profit margin is calculated correctly."""
    result = calculate_profit(
        ebay_price=100.0,
        ebay_shipping=0.0,
        source_price_jpy=3000.0,
        fee_rules={
            "fx": {"default_rate": 150.0},
            "fees": {"default": {"percent": 0.12, "fixed": 0.30}},
            "shipping": {"default_jpy": 800},
        },
    )
    assert 0.0 <= result.profit_margin_no_rebate <= 1.0
    assert result.profit_margin_no_rebate == result.profit_margin_with_rebate
