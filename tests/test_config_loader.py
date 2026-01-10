"""Tests for config_loader module."""

from pathlib import Path

from src.config_loader import ConfigBundle, load_all_configs, load_yaml


def test_load_yaml_returns_dict():
    """Test that load_yaml returns a dictionary."""
    result = load_yaml(Path("config/hotwords.yaml"))
    assert isinstance(result, dict)


def test_load_yaml_hotwords():
    """Test loading hotwords configuration."""
    result = load_yaml(Path("config/hotwords.yaml"))
    assert "keywords" in result


def test_load_yaml_categories():
    """Test loading categories configuration."""
    result = load_yaml(Path("config/categories.yaml"))
    assert "blocked_keywords" in result


def test_load_yaml_fee_rules():
    """Test loading fee rules configuration."""
    result = load_yaml(Path("config/fee_rules.yaml"))
    assert "fx" in result
    assert "fees" in result


def test_load_yaml_marketplaces():
    """Test loading marketplaces configuration."""
    result = load_yaml(Path("config/marketplaces.yaml"))
    assert "markets" in result
    assert "US" in result["markets"]


def test_load_yaml_sourcing_sites():
    """Test loading sourcing sites configuration."""
    result = load_yaml(Path("config/sourcing_sites.yaml"))
    assert "sites" in result


def test_load_all_configs():
    """Test loading all configurations."""
    config = load_all_configs()
    assert isinstance(config, ConfigBundle)
    assert isinstance(config.hotwords, dict)
    assert isinstance(config.marketplaces, dict)
    assert isinstance(config.categories, dict)
    assert isinstance(config.sourcing_sites, dict)
    assert isinstance(config.fee_rules, dict)


def test_config_bundle_has_all_fields():
    """Test that ConfigBundle has all expected fields."""
    config = load_all_configs()
    assert hasattr(config, "hotwords")
    assert hasattr(config, "marketplaces")
    assert hasattr(config, "categories")
    assert hasattr(config, "sourcing_sites")
    assert hasattr(config, "fee_rules")
