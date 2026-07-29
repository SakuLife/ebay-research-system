"""国内仕入先の判定（_is_domestic_source）の単体テスト.

「国内サイトから最安値を探す」が要件なので、海外ショップの混入は
非現実的な利益額を生む＝納品事故になる。実データで実際に混入した
店名を回帰テストとして固定しておく。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.sourcing import _is_domestic_source  # noqa: E402


class Test国内と判定するもの:
    def test_日本語を含む店名(self):
        assert _is_domestic_source("楽天市場 - ビューティー銀座")
        assert _is_domestic_source("ヤマダデンキ")
        assert _is_domestic_source("Yahoo!ショッピング - ブングショップヤフー店")

    def test_既知の国内ショップ(self):
        assert _is_domestic_source("Amazon公式サイト")
        assert _is_domestic_source("yodobashi.com")
        assert _is_domestic_source("SoundHouse")

    def test_国内ECプラットフォームの個人店(self):
        # 実データで誤って除外していたケース（BASEは国内サービス）
        assert _is_domestic_source("penne19.base.shop")

    def test_jpドメイン(self):
        assert _is_domestic_source("example.co.jp")


class Test海外と判定するもの:
    def test_実際に混入した海外ショップ(self):
        # いずれも「国内最安」として採用されかけた実例
        assert not _is_domestic_source("vvs-automatismes.fr")
        assert not _is_domestic_source("osbrankoradicevicobilic.edu.rs")
        assert not _is_domestic_source("chaxon.pl")

    def test_ascii表記の海外ショップ(self):
        # 楽天の1/3の価格で万年筆を出しており、利益額を約3倍に膨らませていた
        assert not _is_domestic_source("Dorothea Design")
        assert not _is_domestic_source("Weingut Befort")

    def test_客が明示的に除外指定した中国系(self):
        assert not _is_domestic_source("aliexpress.com")
        assert not _is_domestic_source("SHEIN")

    def test_空文字(self):
        assert not _is_domestic_source("")
