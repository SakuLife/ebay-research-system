"""同一商品判定に使うタイトル解析ヘルパーの単体テスト.

打率（同一商品を見つけられる割合）に直結する部分なので、
「同一商品を同一と見る」「別バリアントを別と見る」の両方を固定する。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ebay_client import (  # noqa: E402
    is_accessory_listing,
    title_similarity,
    _model_tokens,
    models_match,
)


class TestModelTokens:
    """型番トークンの抽出."""

    def test_型番を抜き出せる(self):
        assert "nstsc10" in _model_tokens("Zojirushi NS-TSC10 Rice Cooker")

    def test_ハイフン有無の表記ゆれを吸収する(self):
        assert models_match(_model_tokens("Casio F-91W"), _model_tokens("CASIO F91W-1 Watch"))

    def test_英字のみ数字のみは型番とみなさない(self):
        tokens = _model_tokens("Sony Wireless Headphones 2024")
        assert "wireless" not in tokens
        assert "2024" not in tokens

    def test_別世代の型番は一致しない(self):
        assert not models_match(_model_tokens("Sony WH-1000XM4"), _model_tokens("Sony WH-1000XM5"))


class TestTitleSimilarity:
    """タイトル類似度. 同一商品は高く、別商品は低く出ること."""

    def test_同一商品は語順が違っても高く出る(self):
        score = title_similarity(
            "Zojirushi NS-TSC10 5-1/2-Cup (Uncooked) Micom Rice Cooker and Warmer",
            "Zojirushi Micom Rice Cooker & Warmer NS-TSC10 5.5 Cup Japan",
        )
        assert score >= 0.70

    def test_同一商品は出品者の前置きがあっても高く出る(self):
        score = title_similarity(
            "Sony WH-1000XM4 Wireless Noise Canceling Headphones Black",
            "Sony Wireless Noise Cancelling Stereo Headset WH-1000XM4 B Black Japan",
        )
        assert score >= 0.70

    def test_別世代の商品は同一商品より低く出る(self):
        base = "Sony WH-1000XM4 Wireless Noise Canceling Headphones Black"
        same = title_similarity(
            base, "Sony Wireless Noise Cancelling Stereo Headset WH-1000XM4 B Black Japan"
        )
        other = title_similarity(base, "SONY WH-1000XM5 Wireless Headphones Silver New")
        # 旧実装(SequenceMatcher)では別世代の方が高く出て、取りこぼしの原因になっていた
        assert same > other

    def test_無関係な商品は低く出る(self):
        score = title_similarity(
            "Zojirushi NS-TSC10 Micom Rice Cooker",
            "Shimano Stella SW 8000HG Spinning Reel",
        )
        assert score < 0.30

    def test_空文字は0(self):
        assert title_similarity("", "Zojirushi Rice Cooker") == 0.0


class TestAccessoryListing:
    """本体でない出品の除外."""

    def test_説明書のみは付属品扱い(self):
        assert is_accessory_listing("Zojirushi NS-TSC10 Rice Cooker Instruction Manual Only")

    def test_箱のみは付属品扱い(self):
        assert is_accessory_listing("Nintendo Switch Box Only No Console")

    def test_ジャンク_部品取りは付属品扱い(self):
        assert is_accessory_listing("Nikon Lens For Parts Not Working")
        assert is_accessory_listing("カメラ ジャンク品")

    def test_本体の出品は除外しない(self):
        assert not is_accessory_listing("Zojirushi NS-TSC10 Micom Rice Cooker 5.5 Cup")
        assert not is_accessory_listing("Sony WH-1000XM4 Headphones with Case")
