"""Gemini API client for product name translation and weight research."""

import os
import re
from typing import Optional, List
from dataclasses import dataclass

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

    def research_product_weight(self, product_title: str, product_url: str = "") -> Optional['WeightResearchResult']:
        """
        商品のサイズ・重量を調査する.

        Geminiに商品名でネット検索させ、メーカー公式や大手ショップから
        実際のサイズ・重量を取得。梱包後サイズ・容積重量も計算。

        Args:
            product_title: 商品タイトル（日本語推奨）
            product_url: 商品URL（参考情報として渡す）

        Returns:
            WeightResearchResult。失敗時はNone。
        """
        if not self.is_enabled:
            return None

        prompt = f'''あなたは商品サイズ・重量の調査アシスタントです。
以下の商品について、ネット検索してサイズ・重量情報を取得してください。

【商品情報】
商品名: {product_title}
{f"参考URL: {product_url}" if product_url else ""}

【出力ルール】
必ず以下の形式で4項目を出力してください。数値のみ、単位はcmとkgで統一。

1. 商品サイズ・重量: [縦]x[横]x[高さ]cm, [重量]kg
2. 梱包後サイズ・重量: [縦]x[横]x[高さ]cm, [重量]kg
3. 容積重量: [数値]kg
4. 適用重量: [数値]kg ([実重量/容積重量]を適用)

【サイズ・重量の取得ルール】

1. まず必ず、商品名・型番でネット検索してサイズ・重量を調べること。
   - 優先する情報源：メーカー公式サイト、正規販売店、Amazon、楽天
   - そこから「商品サイズ（外箱サイズがあれば箱サイズ）」と「商品重量」を取る。

2. 梱包後サイズの決め方
   - 割れ物・陶器・桐箱など → 周囲に2〜3cm程度のクッション
   - 小物・サプリ・文具など → 商品サイズ＋数cm程度
   - ぬいぐるみ・フィギュア → 商品サイズ＋5〜10cm程度

3. 梱包後重量の決め方
   - 小物（〜500g）: 商品重量＋0.2〜0.3kg
   - 中型（500g〜2kg）: 商品重量＋0.3〜0.5kg
   - 大型（2kg〜）: 商品重量＋0.5〜1.0kg

4. 容積重量の計算
   - 式：縦(cm) × 横(cm) × 高さ(cm) ÷ 5000
   - 小数2桁程度に丸める

5. 適用重量
   - 梱包後重量と容積重量を比較し、大きい方を採用

6. サイズ情報が見つからない場合
   - 商品カテゴリから現実的な目安を設定（極端な数値は避ける）
   - 「推定」と明記

【出力例】
1. 商品サイズ・重量: 26.0x10.5x16.0cm, 1.49kg
2. 梱包後サイズ・重量: 32x18x22cm, 1.9kg
3. 容積重量: 2.53kg
4. 適用重量: 2.53kg (容積重量を適用)

それでは調査結果を出力してください:'''

        try:
            response = self.model.generate_content(prompt)
            result = response.text.strip()
            return self._parse_weight_research_result(result)
        except Exception as e:
            print(f"  [WARN] Gemini weight research failed: {e}")
            return None

    def _parse_weight_research_result(self, text: str) -> Optional['WeightResearchResult']:
        """
        Geminiの出力をパースしてWeightResearchResultに変換.
        """
        try:
            # 初期値
            product_size = (0.0, 0.0, 0.0)
            product_weight_kg = 0.0
            packed_size = (0.0, 0.0, 0.0)
            packed_weight_kg = 0.0
            volumetric_weight_kg = 0.0
            applied_weight_kg = 0.0
            is_volumetric = False

            lines = text.split('\n')
            for line in lines:
                line = line.strip()

                # 1. 商品サイズ・重量
                if '商品サイズ' in line and '梱包' not in line:
                    size_match = re.search(r'(\d+\.?\d*)\s*[x×]\s*(\d+\.?\d*)\s*[x×]\s*(\d+\.?\d*)', line)
                    weight_match = re.search(r'(\d+\.?\d*)\s*kg', line)
                    if size_match:
                        product_size = (float(size_match.group(1)), float(size_match.group(2)), float(size_match.group(3)))
                    if weight_match:
                        product_weight_kg = float(weight_match.group(1))

                # 2. 梱包後サイズ・重量
                elif '梱包後' in line:
                    size_match = re.search(r'(\d+\.?\d*)\s*[x×]\s*(\d+\.?\d*)\s*[x×]\s*(\d+\.?\d*)', line)
                    weight_match = re.search(r'(\d+\.?\d*)\s*kg', line)
                    if size_match:
                        packed_size = (float(size_match.group(1)), float(size_match.group(2)), float(size_match.group(3)))
                    if weight_match:
                        packed_weight_kg = float(weight_match.group(1))

                # 3. 容積重量
                elif '容積重量' in line:
                    weight_match = re.search(r'(\d+\.?\d*)\s*kg', line)
                    if weight_match:
                        volumetric_weight_kg = float(weight_match.group(1))

                # 4. 適用重量
                elif '適用重量' in line or '適用すべき重量' in line:
                    weight_match = re.search(r'(\d+\.?\d*)\s*kg', line)
                    if weight_match:
                        applied_weight_kg = float(weight_match.group(1))
                    is_volumetric = '容積' in line

            # 最低限の検証
            if applied_weight_kg <= 0:
                # フォールバック: 梱包後重量か容積重量の大きい方
                applied_weight_kg = max(packed_weight_kg, volumetric_weight_kg)
                is_volumetric = volumetric_weight_kg > packed_weight_kg

            if applied_weight_kg <= 0:
                return None

            return WeightResearchResult(
                product_depth_cm=product_size[0],
                product_width_cm=product_size[1],
                product_height_cm=product_size[2],
                product_weight_kg=product_weight_kg,
                packed_depth_cm=packed_size[0],
                packed_width_cm=packed_size[1],
                packed_height_cm=packed_size[2],
                packed_weight_kg=packed_weight_kg,
                volumetric_weight_kg=volumetric_weight_kg,
                applied_weight_kg=applied_weight_kg,
                is_volumetric_applied=is_volumetric,
                raw_response=text
            )
        except Exception as e:
            print(f"  [WARN] Failed to parse weight research result: {e}")
            return None

    def validate_source_match(
        self,
        ebay_title: str,
        ebay_price_usd: float,
        source_title: str,
        source_url: str,
        source_price_jpy: float,
        source_site: str,
        condition: str = "New"
    ) -> Optional['SourceValidationResult']:
        """
        eBay商品と国内仕入先の組み合わせが適切かをGeminiでチェックする.

        Args:
            ebay_title: eBay商品タイトル
            ebay_price_usd: eBay販売価格（USD）
            source_title: 仕入先商品タイトル
            source_url: 仕入先URL
            source_price_jpy: 仕入先価格（円）
            source_site: 仕入先サイト名
            condition: 商品コンディション（New/Used）

        Returns:
            SourceValidationResult。失敗時はNone。
        """
        if not self.is_enabled:
            return None

        prompt = f'''あなたはeBay転売の仕入れ判断アシスタントです。
以下のeBay商品と国内仕入先の組み合わせが「仕入れとして適切か」を判断してください。

【eBay商品（販売側）】
タイトル: {ebay_title}
価格: ${ebay_price_usd:.2f}
コンディション: {condition}

【国内仕入先（仕入れ側）】
サイト: {source_site}
タイトル: {source_title}
URL: {source_url}
価格: ¥{source_price_jpy:,.0f}

【チェック観点】
1. 同一商品か？
   - タイトルから判断して、同じ商品を指しているか
   - 型番、キャラクター名、商品シリーズが一致するか
   - 全く違う商品を仕入れようとしていないか

2. 購入可能なサイトか？
   - 一般ECサイト（Amazon、楽天、Yahoo等）→ OK
   - 小規模カードショップ、専門店 → OK
   - フリマ・オークション（メルカリ、ヤフオク等）→ NG
   - 転売サイト、相場サイト → NG
   - 在庫がなさそう、販売終了っぽい → NG

3. 仕入れ困難な商品か？
   - 一番くじ、プライズ品 → 一般ECでは入手困難
   - PSA/BGS鑑定済みカード → 中古品（Newでは仕入れ不可）
   - 限定品、プロモ品 → 一般ECでは入手困難
   - 予約商品、受注生産 → 納期リスク

4. 価格の妥当性
   - 仕入値が異常に高い（利益が出ない）
   - 仕入値が異常に安い（偽物・詐欺の可能性）

【出力形式】必ずこの形式で出力してください:
VALID: [YES/NO]
SUGGESTION: [accept/skip/retry]
REASON: [1行で判断理由]
ISSUES: [問題点をカンマ区切りで。なければ「なし」]

【SUGGESTIONの意味】
- accept: この仕入先でOK
- skip: この商品自体をスキップ（仕入れ困難、または全く違う商品）
- retry: この仕入先はNG、別の仕入先を試すべき

それでは判断してください:'''

        try:
            response = self.model.generate_content(prompt)
            result = response.text.strip()
            return self._parse_validation_result(result)
        except Exception as e:
            print(f"  [WARN] Gemini validation failed: {e}")
            return None

    def _parse_validation_result(self, text: str) -> Optional['SourceValidationResult']:
        """
        Geminiの検証結果をパースしてSourceValidationResultに変換.
        """
        try:
            is_valid = False
            suggestion = "skip"
            reason = ""
            issues = []

            lines = text.split('\n')
            for line in lines:
                line = line.strip()
                upper_line = line.upper()

                if upper_line.startswith('VALID:'):
                    is_valid = 'YES' in upper_line
                elif upper_line.startswith('SUGGESTION:'):
                    if 'ACCEPT' in upper_line:
                        suggestion = "accept"
                    elif 'RETRY' in upper_line:
                        suggestion = "retry"
                    else:
                        suggestion = "skip"
                elif upper_line.startswith('REASON:'):
                    reason = line.split(':', 1)[1].strip() if ':' in line else ""
                elif upper_line.startswith('ISSUES:'):
                    issues_str = line.split(':', 1)[1].strip() if ':' in line else ""
                    if issues_str and issues_str != "なし":
                        issues = [i.strip() for i in issues_str.split(',')]

            return SourceValidationResult(
                is_valid=is_valid,
                suggestion=suggestion,
                reason=reason,
                issues=issues,
                raw_response=text
            )
        except Exception as e:
            print(f"  [WARN] Failed to parse validation result: {e}")
            return None


@dataclass
class SourceValidationResult:
    """Geminiによる仕入先検証結果."""
    is_valid: bool  # 仕入先として適切か
    suggestion: str  # "accept", "skip", "retry"
    reason: str  # 判断理由
    issues: List[str]  # 検出された問題点
    raw_response: str  # Geminiの生出力


@dataclass
class WeightResearchResult:
    """Geminiによる重量調査結果."""
    product_depth_cm: float  # 商品サイズ（縦）
    product_width_cm: float  # 商品サイズ（横）
    product_height_cm: float  # 商品サイズ（高さ）
    product_weight_kg: float  # 商品重量

    packed_depth_cm: float  # 梱包後サイズ（縦）
    packed_width_cm: float  # 梱包後サイズ（横）
    packed_height_cm: float  # 梱包後サイズ（高さ）
    packed_weight_kg: float  # 梱包後重量

    volumetric_weight_kg: float  # 容積重量
    applied_weight_kg: float  # 適用すべき重量
    is_volumetric_applied: bool  # 容積重量が適用されたか

    raw_response: str  # Geminiの生出力（デバッグ用）

    @property
    def applied_weight_g(self) -> int:
        """適用重量（グラム）."""
        return int(self.applied_weight_kg * 1000)
