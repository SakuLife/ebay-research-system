"""検索ベースシート連携クライアント."""

from typing import Optional, Dict, Any
import time


class SearchBaseClient:
    """検索ベースシートへの書き込み・読み取りを行うクライアント."""

    def __init__(self, sheets_client):
        """
        Args:
            sheets_client: GoogleSheetsClient instance
        """
        self.sheets_client = sheets_client
        self.worksheet_name = "検索ベース"

    def write_input_data(
        self,
        source_price_jpy: float,
        ebay_price_usd: float,
        ebay_shipping_usd: float,
        ebay_url: str,
        weight_g: Optional[float] = None,
        depth_cm: Optional[float] = None,
        width_cm: Optional[float] = None,
        height_cm: Optional[float] = None,
        category_id: Optional[str] = None
    ) -> bool:
        """
        検索ベースシート10行目（B10:M10）に入力データを書き込む.

        Args:
            source_price_jpy: 仕入値（円）
            ebay_price_usd: eBay売値（ドル）
            ebay_shipping_usd: eBay送料（ドル）
            ebay_url: eBay URL
            weight_g: 適用重量（グラム）
            depth_cm: 奥行き（cm）
            width_cm: 幅（cm）
            height_cm: 高さ（cm）

        Returns:
            書き込み成功ならTrue
        """
        try:
            worksheet = self.sheets_client.spreadsheet.worksheet(self.worksheet_name)

            print(f"  [検索ベース] === 書き込み開始 ===")
            # 個別セルに書き込み（E10とJ10は数式なので上書きしない）
            # value_input_option='USER_ENTERED'で書式を保持

            # B10: 仕入値（¥）
            worksheet.update(range_name="B10", values=[[int(source_price_jpy)]], value_input_option='USER_ENTERED')
            print(f"  [検索ベース] B10 ← {int(source_price_jpy)} 円（仕入値）")

            # C10: 売値（$）- 小数第1位まで
            ebay_price_rounded = round(ebay_price_usd, 1)
            worksheet.update(range_name="C10", values=[[ebay_price_rounded]], value_input_option='USER_ENTERED')
            print(f"  [検索ベース] C10 ← ${ebay_price_rounded:.1f} （売値）")

            # D10: 送料（$）- 小数第1位まで
            ebay_shipping_rounded = round(ebay_shipping_usd, 1)
            worksheet.update(range_name="D10", values=[[ebay_shipping_rounded]], value_input_option='USER_ENTERED')
            print(f"  [検索ベース] D10 ← ${ebay_shipping_rounded:.1f} （送料）")

            # E10: 適用重量（g） - 数式なのでスキップ
            print(f"  [検索ベース] E10 ← スキップ（数式セル）")

            # F10: 実重量（g）
            if weight_g:
                worksheet.update(range_name="F10", values=[[int(weight_g)]], value_input_option='USER_ENTERED')
                print(f"  [検索ベース] F10 ← {int(weight_g)}g （実重量）")
            else:
                print(f"  [検索ベース] F10 ← 未入力（weight_g=None）")

            # G10: 奥行き（cm）
            if depth_cm:
                worksheet.update(range_name="G10", values=[[depth_cm]], value_input_option='USER_ENTERED')
                print(f"  [検索ベース] G10 ← {depth_cm}cm （奥行き）")
            else:
                print(f"  [検索ベース] G10 ← 未入力")

            # H10: 幅（cm）
            if width_cm:
                worksheet.update(range_name="H10", values=[[width_cm]], value_input_option='USER_ENTERED')
                print(f"  [検索ベース] H10 ← {width_cm}cm （幅）")
            else:
                print(f"  [検索ベース] H10 ← 未入力")

            # I10: 高さ（cm）
            if height_cm:
                worksheet.update(range_name="I10", values=[[height_cm]], value_input_option='USER_ENTERED')
                print(f"  [検索ベース] I10 ← {height_cm}cm （高さ）")
            else:
                print(f"  [検索ベース] I10 ← 未入力")

            # J10: 合計（g） - 数式なのでスキップ
            print(f"  [検索ベース] J10 ← スキップ（数式セル）")

            # K9: eBay URL（9行目に変更）
            worksheet.update(range_name="K9", values=[[ebay_url]], value_input_option='USER_ENTERED')
            print(f"  [検索ベース] K9 ← {ebay_url}")

            # K11: カテゴリNo
            if category_id:
                worksheet.update(range_name="K11", values=[[category_id]], value_input_option='USER_ENTERED')
                print(f"  [検索ベース] K11 ← {category_id} （カテゴリNo）")

            print(f"  [検索ベース] === 書き込み完了 ===")
            return True

        except Exception as e:
            print(f"  [ERROR] 検索ベースシートへの書き込みエラー: {e}")
            return False

    def read_calculation_results(self, max_wait_seconds: int = 5) -> Optional[Dict[str, Any]]:
        """
        検索ベースシートから計算結果を読み取る.

        スプレッドシートの計算完了を待つため、少し待機してから読み取る。

        Args:
            max_wait_seconds: 最大待機時間（秒）

        Returns:
            計算結果の辞書。読み取り失敗時はNone。
            {
                "carrier": str,             # N10: 業者
                "shipping_method": str,     # O10: 発送方法
                "profit_no_rebate": float,  # P10: 還付抜き利益
                "margin_no_rebate": float,  # Q10: 還付抜き利益率
                "profit_with_rebate": float,  # P13: 還付あり利益
                "margin_with_rebate": float   # Q13: 還付あり利益率
            }
        """
        try:
            # 計算完了を待つ
            print(f"  [検索ベース] 計算完了を待機中（{max_wait_seconds}秒）...")
            time.sleep(max_wait_seconds)

            worksheet = self.sheets_client.spreadsheet.worksheet(self.worksheet_name)

            # N10:Q10 から還付抜き結果を取得
            result_row_10 = worksheet.get("N10:Q10")
            # P13:Q13 から還付あり結果を取得
            result_row_13 = worksheet.get("P13:Q13")

            if not result_row_10 or not result_row_10[0]:
                print(f"  [WARN] 検索ベースシートの計算結果が見つかりません（N10:Q10）")
                return None

            row_10 = result_row_10[0]

            # N10: 業者
            carrier = row_10[0] if len(row_10) > 0 else ""

            # O10: 発送方法
            shipping_method = row_10[1] if len(row_10) > 1 else ""

            # P10: 還付抜き利益
            profit_no_rebate_str = row_10[2] if len(row_10) > 2 else "0"
            profit_no_rebate_str = str(profit_no_rebate_str).replace(",", "").replace("¥", "").replace("円", "").strip()
            try:
                profit_no_rebate = float(profit_no_rebate_str) if profit_no_rebate_str and profit_no_rebate_str != "" else 0
            except ValueError:
                # "該当なし"などの文字列の場合は0
                print(f"  [WARN] P10の値が数値ではありません: {profit_no_rebate_str}")
                profit_no_rebate = 0

            # Q10: 還付抜き利益率
            margin_no_rebate_str = row_10[3] if len(row_10) > 3 else "0"
            margin_no_rebate_str = str(margin_no_rebate_str).replace(",", "").replace("%", "").strip()
            try:
                margin_no_rebate = float(margin_no_rebate_str) if margin_no_rebate_str and margin_no_rebate_str != "" else 0
            except ValueError:
                # "該当なし"などの文字列の場合は0
                print(f"  [WARN] Q10の値が数値ではありません: {margin_no_rebate_str}")
                margin_no_rebate = 0

            # P13:Q13 から還付あり結果を取得
            profit_with_rebate = profit_no_rebate
            margin_with_rebate = margin_no_rebate

            if result_row_13 and result_row_13[0]:
                row_13 = result_row_13[0]

                # P13: 還付あり利益
                profit_with_rebate_str = row_13[0] if len(row_13) > 0 else "0"
                profit_with_rebate_str = str(profit_with_rebate_str).replace(",", "").replace("¥", "").replace("円", "").strip()
                if profit_with_rebate_str and profit_with_rebate_str != "":
                    try:
                        profit_with_rebate = float(profit_with_rebate_str)
                    except ValueError:
                        pass  # デフォルト値を使用

                # Q13: 還付あり利益率
                margin_with_rebate_str = row_13[1] if len(row_13) > 1 else "0"
                margin_with_rebate_str = str(margin_with_rebate_str).replace(",", "").replace("%", "").strip()
                if margin_with_rebate_str and margin_with_rebate_str != "":
                    try:
                        margin_with_rebate = float(margin_with_rebate_str)
                    except ValueError:
                        pass  # デフォルト値を使用

            result = {
                "carrier": carrier,
                "shipping_method": shipping_method,
                "profit_no_rebate": profit_no_rebate,
                "margin_no_rebate": margin_no_rebate,
                "profit_with_rebate": profit_with_rebate,
                "margin_with_rebate": margin_with_rebate
            }

            print(f"  [検索ベース] 計算結果取得:")
            print(f"    業者: {carrier}")
            print(f"    発送方法: {shipping_method}")
            print(f"    還付抜き利益: JPY {profit_no_rebate:,.0f}")
            print(f"    還付抜き利益率: {margin_no_rebate:.1f}%")
            print(f"    還付あり利益: JPY {profit_with_rebate:,.0f}")
            print(f"    還付あり利益率: {margin_with_rebate:.1f}%")

            return result

        except Exception as e:
            print(f"  [ERROR] 検索ベースシートからの読み取りエラー: {e}")
            import traceback
            traceback.print_exc()
            return None
