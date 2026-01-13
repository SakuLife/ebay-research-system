"""Gemini API client for product name translation."""

import os
import re
from typing import Optional, List

try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False


def extract_model_numbers(title: str) -> List[str]:
    """
    タイトルから型番・識別子を正規表現で抽出する.
    Gemini翻訳で失われがちな情報を保持するため.

    Returns:
        抽出された型番のリスト
    """
    identifiers = []

    # PSA/CGC/BGSグレーディング (PSA 10, CGC 9.5等)
    grading = re.findall(r'\b(PSA|CGC|BGS|SGC)\s*\d+\.?\d*\b', title, re.IGNORECASE)
    identifiers.extend(grading)

    # カード番号 (217/187, 025/078等)
    card_numbers = re.findall(r'\b(\d{2,3}/\d{2,3})\b', title)
    identifiers.extend(card_numbers)

    # レアリティ (SAR, SR, RR, AR, UR, HR等)
    rarities = re.findall(r'\b(SAR|SR|RR|AR|UR|HR|SSR|CHR|CSR|S|A|R)\b', title)
    identifiers.extend(rarities)

    # 型番パターン (MG-100, RX-78-2, V-1234等)
    model_nums = re.findall(r'\b([A-Z]{1,4}[-]?\d{2,5}[-]?[A-Z0-9]*)\b', title, re.IGNORECASE)
    # 短すぎるものや年号っぽいものを除外
    model_nums = [m for m in model_nums if len(m) >= 4 and not re.match(r'^(19|20)\d{2}$', m)]
    identifiers.extend(model_nums)

    # 重複除去（順序維持）
    seen = set()
    unique = []
    for item in identifiers:
        upper = item.upper()
        if upper not in seen:
            seen.add(upper)
            unique.append(item)

    return unique


class GeminiClient:
    """Gemini APIを使って商品名を翻訳するクライアント."""

    def __init__(self, api_key: Optional[str] = None, model_name: str = "gemini-2.0-flash"):
        """
        Args:
            api_key: Gemini API key. If not provided, reads from GEMINI_API_KEY env var.
            model_name: Model name to use.
        """
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.model_name = model_name or os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
        self.is_enabled = bool(self.api_key) and GEMINI_AVAILABLE

        if not GEMINI_AVAILABLE:
            print("  [WARN] Gemini library not installed. Run: pip install google-generativeai")
        elif not self.api_key:
            print("  [WARN] GEMINI_API_KEY not set. Gemini disabled.")
        else:
            genai.configure(api_key=self.api_key)
            self.model = genai.GenerativeModel(self.model_name)

    def translate_product_name(self, english_title: str) -> Optional[str]:
        """
        eBayの英語商品名を日本語の検索キーワードに翻訳する.

        Args:
            english_title: eBayの商品タイトル（英語）

        Returns:
            日本語の検索キーワード。失敗時はNone。
        """
        if not self.is_enabled:
            return None

        prompt = f'''eBayの商品タイトルを日本の楽天/Amazonで検索するための日本語キーワードに変換してください。
ルール:
- 商品名のみを出力（説明不要）
- 型番や番号（例: 217/187, RR, SAR）はそのまま維持
- 「Japanese」「Excellent Condition」などの状態説明は除外
- ブランド名やキャラクター名は日本語に翻訳

商品タイトル: {english_title}

日本語キーワード:'''

        try:
            response = self.model.generate_content(prompt)
            result = response.text.strip()
            # 余計な改行や空白を除去
            result = ' '.join(result.split())
            return result
        except Exception as e:
            print(f"  [WARN] Gemini translation failed: {e}")
            return None

    def extract_product_identifier(self, title: str) -> Optional[str]:
        """
        商品タイトルから型番や識別子を抽出する.

        Args:
            title: 商品タイトル

        Returns:
            型番や識別子。見つからない場合はNone。
        """
        if not self.is_enabled:
            return None

        prompt = f'''この商品タイトルから、商品を特定できる型番や番号を抽出してください。
例: カード番号（217/187）、型番（MG-100）、JANコードなど
複数ある場合はスペース区切りで出力。
見つからない場合は「なし」と出力。

タイトル: {title}

型番/番号:'''

        try:
            response = self.model.generate_content(prompt)
            result = response.text.strip()
            if result == "なし" or not result:
                return None
            return result
        except Exception as e:
            print(f"  [WARN] Gemini extraction failed: {e}")
            return None
