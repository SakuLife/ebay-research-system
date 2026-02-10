"""無料Web検索による事前スクリーニング（SerpAPIコスト削減用）.

楽天APIで見つからなかった商品について、DuckDuckGoで無料検索し、
Geminiでスニペットを分析して仕入れ困難性を判定する。
予約制・抽選式・期間限定・廃盤品を早期検出してGoogle Lens呼び出しを回避。
"""

import time
from typing import Optional, List
from dataclasses import dataclass

try:
    from duckduckgo_search import DDGS
    DDGS_AVAILABLE = True
except ImportError:
    DDGS_AVAILABLE = False


@dataclass
class WebPrescreenResult:
    """Web事前スクリーニング結果."""

    should_skip: bool
    skip_reason: str
    confidence: str
    snippets_found: int
    search_queries_used: list
    raw_snippets: str
    gemini_response: str


# グローバル統計トラッカー
_prescreen_stats: dict[str, int] = {
    "total_checks": 0,
    "skipped_by_prescreen": 0,
    "serpapi_calls_saved": 0,
    "errors": 0,
}


def reset_prescreen_stats() -> None:
    """統計をリセット."""
    _prescreen_stats["total_checks"] = 0
    _prescreen_stats["skipped_by_prescreen"] = 0
    _prescreen_stats["serpapi_calls_saved"] = 0
    _prescreen_stats["errors"] = 0


def get_prescreen_stats() -> dict[str, int]:
    """統計を取得."""
    return dict(_prescreen_stats)


class FreeWebSearcher:
    """DuckDuckGoを使った無料Web検索クライアント."""

    SEARCH_TIMEOUT = 10

    def __init__(self) -> None:
        self.is_enabled = DDGS_AVAILABLE
        if not DDGS_AVAILABLE:
            print("  [WARN] duckduckgo-search not installed. pip install duckduckgo-search")

    def search(
        self,
        query: str,
        region: str = "jp-jp",
        max_results: int = 8,
    ) -> list[dict]:
        """
        DuckDuckGoで検索してスニペットを返す.

        Args:
            query: 検索クエリ（日本語OK）
            region: 検索リージョン
            max_results: 最大結果数

        Returns:
            [{"title": str, "body": str, "href": str}, ...]
        """
        if not self.is_enabled:
            return []

        try:
            ddgs = DDGS(timeout=self.SEARCH_TIMEOUT)
            results = ddgs.text(
                keywords=query,
                region=region,
                max_results=max_results,
            )
            return results or []
        except Exception as e:
            print(f"    [WebPrescreen] DuckDuckGo error: {e}")
            _prescreen_stats["errors"] += 1
            return []

    def search_multiple_queries(
        self,
        queries: list[str],
        region: str = "jp-jp",
        max_results_per_query: int = 5,
        delay_between: float = 1.0,
    ) -> list[dict]:
        """
        複数クエリで検索して結果をマージ（URL重複排除）.

        Args:
            queries: 検索クエリリスト
            region: 検索リージョン
            max_results_per_query: クエリあたり最大結果数
            delay_between: クエリ間ウェイト（秒）

        Returns:
            マージされた検索結果リスト
        """
        all_results: list[dict] = []
        seen_urls: set[str] = set()

        for i, query in enumerate(queries):
            if i > 0:
                time.sleep(delay_between)

            results = self.search(query, region=region, max_results=max_results_per_query)
            for r in results:
                url = r.get("href", "")
                if url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append(r)

        return all_results


def build_prescreen_queries(
    japanese_query: str,
    ebay_title: str,
) -> list[str]:
    """
    事前スクリーニング用の検索クエリを構築する.

    Args:
        japanese_query: Gemini翻訳済み日本語クエリ
        ebay_title: eBayタイトル（英語、現在未使用だが将来拡張用）

    Returns:
        検索クエリリスト（2つ）
    """
    queries = [
        f"{japanese_query} 通販",
        f"{japanese_query} 予約 在庫",
    ]
    return queries


def format_snippets_for_gemini(
    search_results: list[dict],
    max_snippets: int = 10,
    max_chars: int = 2000,
) -> str:
    """
    検索結果をGemini分析用テキストに整形する.

    Args:
        search_results: DuckDuckGo検索結果
        max_snippets: 最大スニペット数
        max_chars: 最大文字数

    Returns:
        整形されたスニペットテキスト
    """
    lines: list[str] = []
    total_chars = 0

    for i, result in enumerate(search_results[:max_snippets]):
        title = result.get("title", "")
        body = result.get("body", "")
        href = result.get("href", "")

        line = f"[{i + 1}] {title}\n    {body}\n    URL: {href}"
        if total_chars + len(line) > max_chars:
            break
        lines.append(line)
        total_chars += len(line)

    return "\n".join(lines)
