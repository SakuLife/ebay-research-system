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

        Returns:
            Dict with settings: market, period, min_profit
        """
        try:
            worksheet = self.spreadsheet.worksheet("設定＆キーワード")

            # Read settings from B column (rows 4-6)
            market = worksheet.acell('B4').value or "UK"
            period = worksheet.acell('B5').value or "90日"
            min_profit = worksheet.acell('B6').value or "1円"

            return {
                "market": market,
                "period": period,
                "min_profit": min_profit
            }
        except gspread.WorksheetNotFound:
            print(f"  [WARN] '設定＆キーワード' sheet not found. Using defaults.")
            return {
                "market": "UK",
                "period": "90日",
                "min_profit": "1円"
            }

    def read_keywords_from_settings(self) -> List[str]:
        """
        Read keywords from the '設定＆キーワード' (Settings & Keywords) sheet.

        Returns:
            List of keywords (non-empty strings from column A, starting from row 10)
        """
        try:
            worksheet = self.spreadsheet.worksheet("設定＆キーワード")
            # Get all values from column A starting from row 10 (keyword section)
            col_a_values = worksheet.col_values(1)

            # Row 10 onwards are keywords (row 1-9 are settings)
            # Filter out empty strings and section headers (starting with 【)
            keywords = [
                kw.strip()
                for kw in col_a_values[9:]  # Skip rows 1-9 (settings section)
                if kw and kw.strip() and not kw.strip().startswith('【')
            ]

            print(f"  [INFO] Found {len(keywords)} keywords from '設定＆キーワード' sheet")
            return keywords

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
