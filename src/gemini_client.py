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


@dataclass
class ImageAnalysisResult:
    """Geminiによる画像分析結果."""
    should_skip: bool  # スキップすべきか
    skip_reason: str  # スキップ理由（空文字列ならスキップ不要）
    product_type: str  # 商品タイプ（card, figure, set, lottery, etc.）
    confidence: str  # 確信度（high, medium, low）
    details: str  # 詳細説明
    raw_response: str  # Geminiの生出力


# グローバル使用量トラッカー（モジュールレベル）
_gemini_usage = {
    "calls": [],  # {"method": str, "input_tokens": int, "output_tokens": int}
}


def reset_gemini_usage():
    """使用量をリセット."""
    _gemini_usage["calls"] = []


def get_gemini_usage_summary() -> dict:
    """
    使用量サマリーを取得.

    Returns:
        {
            "total_calls": int,
            "calls_by_method": {"translate": 1, "validate": 2, ...},
            "estimated_input_tokens": int,
            "estimated_output_tokens": int,
            "estimated_cost_usd": float,
            "estimated_cost_jpy": int,
        }
    """
    calls = _gemini_usage["calls"]
    if not calls:
        return {
            "total_calls": 0,
            "calls_by_method": {},
            "estimated_input_tokens": 0,
            "estimated_output_tokens": 0,
            "estimated_cost_usd": 0.0,
            "estimated_cost_jpy": 0,
        }

    total_input = sum(c.get("input_tokens", 0) for c in calls)
    total_output = sum(c.get("output_tokens", 0) for c in calls)

    # Gemini 2.0 Flash pricing (2024): $0.10/1M input, $0.40/1M output
    cost_usd = (total_input / 1_000_000 * 0.10) + (total_output / 1_000_000 * 0.40)

    # USD -> JPY (レート約150円)
    cost_jpy = int(cost_usd * 150)

    calls_by_method = {}
    for c in calls:
        m = c.get("method", "unknown")
        calls_by_method[m] = calls_by_method.get(m, 0) + 1

    return {
        "total_calls": len(calls),
        "calls_by_method": calls_by_method,
        "estimated_input_tokens": total_input,
        "estimated_output_tokens": total_output,
        "estimated_cost_usd": cost_usd,
        "estimated_cost_jpy": cost_jpy,
    }


def _log_gemini_call(method: str, input_tokens: int, output_tokens: int):
    """Gemini呼び出しを記録."""
    _gemini_usage["calls"].append({
        "method": method,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    })


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
            # 使用量を記録（推定トークン数）
            _log_gemini_call("translate", len(prompt) // 4, len(result) // 4)
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
            _log_gemini_call("extract", len(prompt) // 4, len(result) // 4)
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
            _log_gemini_call("weight", len(prompt) // 4, len(result) // 4)
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
            _log_gemini_call("validate", len(prompt) // 4, len(result) // 4)
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

    def analyze_ebay_item_image(
        self,
        image_url: str,
        ebay_title: str,
        condition: str = "New",
        search_keyword: str = ""
    ) -> Optional['ImageAnalysisResult']:
        """
        eBay商品の画像を分析し、仕入れ困難な商品かを判定する.

        Google Lensでは同じ画像を探すだけだが、Geminiは画像を見て
        「これはカード」「セット売り」「一番くじ品」などを判定できる。

        Args:
            image_url: eBay商品画像のURL
            ebay_title: eBay商品タイトル（参考情報）
            condition: 商品コンディション（New/Used）
            search_keyword: 元の検索キーワード（不一致検出用）

        Returns:
            ImageAnalysisResult。失敗時はNone。
        """
        if not self.is_enabled:
            return None

        # 検索キーワード情報を追加
        keyword_info = ""
        if search_keyword:
            keyword_info = f"\n元の検索キーワード: {search_keyword}"

        prompt = f'''あなたはeBay商品の仕入れ可否を判定するアシスタントです。
以下の商品画像とタイトルを見て、日本の大手ECサイト（Amazon、楽天、Yahoo）で
新品として仕入れられるかを判定してください。

【商品情報】
タイトル: {ebay_title}
コンディション: {condition}
画像URL: {image_url}{keyword_info}

【スキップすべき商品の例】
1. トレーディングカード（TCG/CCG）
   - ポケモンカード、遊戯王、ワンピースカード等
   - 特にシングルカード（1枚売り）
   - PSA/BGS鑑定スラブ入りカード

2. 一番くじ・プライズ品
   - 「一番くじ」「Ichiban Kuji」の文字
   - A賞、B賞、ラストワン賞などの表記
   - コンビニ限定、ゲーセン景品

3. セット売り・まとめ売り
   - 複数アイテムがまとめて写っている
   - 「Lot」「Bundle」「Set of」の表記
   - 新品で同じセットは入手困難

4. 限定品・プロモ品
   - イベント限定、店舗限定
   - 予約特典、初回特典
   - シリアルナンバー入り

5. キーワードと商品の不一致
   - 検索キーワードが「ONEPIECE」（アニメ）だが、画像は服のワンピース（ドレス）
   - 検索キーワードが特定のブランド/作品だが、画像は無関係な商品
   - キーワードと画像内容が明らかに異なる場合

6. 初版・ヴィンテージ品（vintage）
   - "first edition", "1st edition", "初版" の表記
   - 年代物、絶版品、コレクターズアイテム
   - 20年以上前の古い商品
   - 新品で購入不可能な希少品

7. 出品者オリジナルセット（custom_set）
   - 出品者が独自に複数商品をまとめたセット
   - 市販のセット商品ではない
   - 別々に販売されている商品を1つにまとめたもの
   - 例：「マンガ1巻〜10巻セット」（市販のボックスセットではない）

8. アニメ制作素材（production）
   - セル画（Animation Cel）
   - 原画、動画、背景画
   - 制作資料、設定資料
   - アニメ制作に使われた一点物
   - "cel", "animation art", "production art" の表記
   - 手描きのアニメ素材

【出力形式】必ずこの形式で出力:
SKIP: [YES/NO]
REASON: [スキップ理由。スキップ不要なら「なし」]
TYPE: [card/lottery/set/promo/mismatch/vintage/custom_set/production/figure/toy/other]
CONFIDENCE: [high/medium/low]
DETAILS: [画像から読み取った詳細（1行）]

それでは画像を分析してください:'''

        try:
            # Gemini 2.0 Flash は画像URLを直接渡せる
            response = self.model.generate_content([prompt, {"url": image_url}])
            result = response.text.strip()
            _log_gemini_call("image_analysis", len(prompt) // 4 + 500, len(result) // 4)  # 画像は約500トークン相当
            return self._parse_image_analysis_result(result)
        except Exception as e:
            # 画像取得失敗の場合はタイトルのみで判定を試みる
            try:
                fallback_prompt = prompt.replace(f"画像URL: {image_url}", "画像URL: (取得失敗、タイトルのみで判定)")
                response = self.model.generate_content(fallback_prompt)
                result = response.text.strip()
                _log_gemini_call("image_analysis", len(fallback_prompt) // 4, len(result) // 4)
                return self._parse_image_analysis_result(result)
            except Exception as e2:
                print(f"  [WARN] Gemini image analysis failed: {e2}")
                return None

    def _parse_image_analysis_result(self, text: str) -> Optional['ImageAnalysisResult']:
        """
        Geminiの画像分析結果をパースしてImageAnalysisResultに変換.
        """
        try:
            should_skip = False
            skip_reason = ""
            product_type = "other"
            confidence = "low"
            details = ""

            lines = text.split('\n')
            for line in lines:
                line = line.strip()
                upper_line = line.upper()

                if upper_line.startswith('SKIP:'):
                    should_skip = 'YES' in upper_line
                elif upper_line.startswith('REASON:'):
                    skip_reason = line.split(':', 1)[1].strip() if ':' in line else ""
                    if skip_reason == "なし":
                        skip_reason = ""
                elif upper_line.startswith('TYPE:'):
                    type_value = line.split(':', 1)[1].strip().lower() if ':' in line else "other"
                    if type_value in ["card", "lottery", "set", "promo", "mismatch", "vintage", "custom_set", "production", "figure", "toy", "other"]:
                        product_type = type_value
                elif upper_line.startswith('CONFIDENCE:'):
                    conf_value = line.split(':', 1)[1].strip().lower() if ':' in line else "low"
                    if conf_value in ["high", "medium", "low"]:
                        confidence = conf_value
                elif upper_line.startswith('DETAILS:'):
                    details = line.split(':', 1)[1].strip() if ':' in line else ""

            return ImageAnalysisResult(
                should_skip=should_skip,
                skip_reason=skip_reason,
                product_type=product_type,
                confidence=confidence,
                details=details,
                raw_response=text
            )
        except Exception as e:
            print(f"  [WARN] Failed to parse image analysis result: {e}")
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
