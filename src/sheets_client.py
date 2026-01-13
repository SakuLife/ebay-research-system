"""Sheet adapters for Google Sheets and local CSV."""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import gspread
from google.oauth2.service_account import Credentials

from .models import CandidateRow, ListedRow


CANDIDATE_HEADERS = [
    "candidate_id",
    "created_at",
    "market",
    "status",
    "keyword",
    "ebay_search_query",
    "ebay_item_url",
    "ebay_price",
    "ebay_shipping",
    "ebay_currency",
    "ebay_category_id",
    "ebay_sold_signal",
    "source_site",
    "source_url",
    "source_price_jpy",
    "source_shipping_jpy",
    "stock_hint",
    "fx_rate",
    "estimated_weight_kg",
    "estimated_pkg_cm",
    "profit_jpy_no_rebate",
    "profit_margin_no_rebate",
    "profit_jpy_with_rebate",
    "profit_margin_with_rebate",
    "is_profitable",
    "title_en",
    "description_en",
    "size_weight_block",
    "gpt_model",
    "gpt_prompt_version",
    "listing_id",
    "listed_url",
    "listed_at",
    "error_message",
]

LISTED_HEADERS = [
    "candidate_id",
    "listed_at",
    "listing_id",
    "listed_url",
    "error_message",
]


class GoogleSheetsClient:
    """Real Google Sheets client using gspread."""

    def __init__(self, service_account_file: str, spreadsheet_url: str) -> None:
        # Extract spreadsheet ID from URL
        if "/d/" in spreadsheet_url:
            self.spreadsheet_id = spreadsheet_url.split("/d/")[1].split("/")[0]
        else:
            self.spreadsheet_id = spreadsheet_url

        # Authenticate
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(service_account_file, scopes=scopes)
        self.client = gspread.authorize(creds)
        self.spreadsheet = self.client.open_by_key(self.spreadsheet_id)

        self.candidates_name = os.getenv("SHEETS_NAME_FIND", "検索ベース")
        self.approved_name = os.getenv("SHEETS_NAME_INPUT", "入力シート")
        self.listed_name = os.getenv("SHEETS_NAME_LISTED", "Listed")

    def _get_or_create_worksheet(self, name: str) -> gspread.Worksheet:
        """Get worksheet by name, create if it doesn't exist."""
        try:
            return self.spreadsheet.worksheet(name)
        except gspread.WorksheetNotFound:
            return self.spreadsheet.add_worksheet(title=name, rows=1000, cols=40)

    def append_candidates(self, rows: Iterable[CandidateRow]) -> None:
        """Append candidate rows to the sheet."""
        worksheet = self._get_or_create_worksheet(self.candidates_name)

        # Check if headers exist
        if worksheet.row_count == 0 or not worksheet.row_values(1):
            worksheet.append_row(CANDIDATE_HEADERS)

        # Append rows
        for row in rows:
            values = [getattr(row, field, "") for field in CANDIDATE_HEADERS]
            worksheet.append_row(values)

    def load_approved_pending(self) -> List[Dict[str, str]]:
        """Load approved but not yet listed candidates."""
        worksheet = self._get_or_create_worksheet(self.approved_name)
        records = worksheet.get_all_records()

        return [
            r
            for r in records
            if r.get("status") == "APPROVED" and not r.get("listing_id")
        ]

    def append_listed(self, row: ListedRow) -> None:
        """Append listed row to the sheet."""
        worksheet = self._get_or_create_worksheet(self.listed_name)

        # Check if headers exist
        if worksheet.row_count == 0 or not worksheet.row_values(1):
            worksheet.append_row(LISTED_HEADERS)

        # Append row
        values = [getattr(row, field, "") for field in LISTED_HEADERS]
        worksheet.append_row(values)

    def read_settings(self) -> Dict[str, str]:
        """
        Read settings from the '設定＆キーワード' sheet.

        Layout:
          A-C列: 基本設定（Row 4-8）、重量設定（Row 10-12）
          E-G列: キーワード（Row 4+）

        Returns:
            Dict with settings: market, period, min_profit, weight settings, items_per_keyword
        """
        try:
            worksheet = self.spreadsheet.worksheet("設定＆キーワード")

            # Basic settings (B4-B10)
            market = worksheet.acell('B4').value or "UK"
            period = worksheet.acell('B5').value or "90日"
            min_price = worksheet.acell('B6').value or "100"
            min_profit = worksheet.acell('B7').value or "フィルターなし"
            items_per_keyword = worksheet.acell('B8').value or "5"
            min_sold = worksheet.acell('B9').value or "0"
            condition = worksheet.acell('B10').value or "New"  # New or Used

            # Weight settings (B13-B15) - after condition row and empty row
            default_weight = worksheet.acell('B13').value or "自動推定"
            packaging_weight = worksheet.acell('B14').value or "500"
            size_multiplier = worksheet.acell('B15').value or "1.0"

            return {
                "market": market,
                "period": period,
                "min_price": min_price,
                "min_profit": min_profit,
                "items_per_keyword": items_per_keyword,
                "min_sold": min_sold,
                "condition": condition,
                "default_weight": default_weight,
                "packaging_weight": packaging_weight,
                "size_multiplier": size_multiplier
            }
        except gspread.WorksheetNotFound:
            print(f"  [WARN] '設定＆キーワード' sheet not found. Using defaults.")
            return {
                "market": "UK",
                "period": "90日",
                "min_price": "100",
                "min_profit": "フィルターなし",
                "items_per_keyword": "5",
                "min_sold": "0",
                "condition": "New",
                "default_weight": "自動推定",
                "packaging_weight": "500",
                "size_multiplier": "1.0"
            }

    def read_keywords_from_settings(self) -> List[str]:
        """
        Read keywords from the '設定＆キーワード' (Settings & Keywords) sheet.
        Generates ALL combinations of keywords × modifiers.

        Layout: E-F columns (starting from row 4)
          E列: キーワード (main keywords)
          F列: 修飾語 (modifiers)

        Returns:
            List of all keyword × modifier combinations
        """
        try:
            worksheet = self.spreadsheet.worksheet("設定＆キーワード")

            # Get all values from columns E-F (starting from row 4)
            all_values = worksheet.get("E4:F100")

            if not all_values:
                print(f"  [WARN] No keywords found in '設定＆キーワード' sheet (E4:F)")
                return []

            # Collect unique keywords and modifiers separately
            main_keywords = []
            modifiers = []

            for row in all_values:
                # E column: main keyword
                main_kw = row[0].strip() if len(row) > 0 and row[0] else ""

                # Skip empty rows and section headers
                if main_kw and not main_kw.startswith('【'):
                    if main_kw not in main_keywords:
                        main_keywords.append(main_kw)

                # F column: modifier
                modifier = row[1].strip() if len(row) > 1 and row[1] else ""
                if modifier and modifier not in modifiers:
                    modifiers.append(modifier)

            # Generate ALL combinations: keyword × modifier
            combined_keywords = []

            if modifiers:
                # Generate all keyword × modifier combinations
                for kw in main_keywords:
                    for mod in modifiers:
                        combined = f"{kw} {mod}"
                        combined_keywords.append(combined)

                print(f"  [INFO] Generated {len(combined_keywords)} combinations from {len(main_keywords)} keywords × {len(modifiers)} modifiers")
            else:
                # No modifiers, just use keywords as-is
                combined_keywords = main_keywords
                print(f"  [INFO] Found {len(combined_keywords)} keywords (no modifiers)")

            return combined_keywords

        except gspread.WorksheetNotFound:
            print(f"  [ERROR] '設定＆キーワード' sheet not found!")
            print(f"  [ERROR] Please create the settings sheet first.")
            return []



class LocalSheetsClient:
    """Local CSV-based client for testing."""

    def __init__(self, base_dir: str) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(exist_ok=True)
        self.candidates_name = os.getenv("SHEETS_NAME_FIND", "Candidates")
        self.approved_name = os.getenv("SHEETS_NAME_INPUT", "Approved")
        self.listed_name = os.getenv("SHEETS_NAME_LISTED", "Listed")

    def _ensure_file(self, filename: str, headers: List[str]) -> Path:
        path = self.base_dir / filename
        if not path.exists():
            with path.open("w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(headers)
        return path

    def append_candidates(self, rows: Iterable[CandidateRow]) -> None:
        path = self._ensure_file(f"{self.candidates_name}.csv", CANDIDATE_HEADERS)
        with path.open("a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CANDIDATE_HEADERS)
            for row in rows:
                writer.writerow(row.__dict__)

    def load_approved_pending(self) -> List[Dict[str, str]]:
        path = self._ensure_file(f"{self.approved_name}.csv", CANDIDATE_HEADERS)
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            return [
                r
                for r in reader
                if r.get("status") == "APPROVED" and not r.get("listing_id")
            ]

    def append_listed(self, row: ListedRow) -> None:
        path = self._ensure_file(f"{self.listed_name}.csv", LISTED_HEADERS)
        with path.open("a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=LISTED_HEADERS)
            writer.writerow(row.__dict__)
