"""Config loading helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import yaml


@dataclass
class ConfigBundle:
    hotwords: Dict[str, Any]
    marketplaces: Dict[str, Any]
    categories: Dict[str, Any]
    sourcing_sites: Dict[str, Any]
    fee_rules: Dict[str, Any]


def load_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_all_configs(base_dir: str = "config") -> ConfigBundle:
    base = Path(base_dir)
    return ConfigBundle(
        hotwords=load_yaml(base / "hotwords.yaml"),
        marketplaces=load_yaml(base / "marketplaces.yaml"),
        categories=load_yaml(base / "categories.yaml"),
        sourcing_sites=load_yaml(base / "sourcing_sites.yaml"),
        fee_rules=load_yaml(base / "fee_rules.yaml"),
    )
