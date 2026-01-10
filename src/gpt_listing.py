"""Listing generator (Gemini or mock)."""

from __future__ import annotations

import os
from typing import Optional

import google.generativeai as genai

from .models import GptListing


def _parse_four_lines(text: str) -> GptListing:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 4:
        # Fallback to a safe default format.
        return GptListing(
            title_en="Sample Title",
            description_en="Sample description.",
            size_weight_block="Size: 30 x 20 x 10 cm\nWeight: 0.8 kg",
        )
    return GptListing(
        title_en=lines[0],
        description_en=lines[1],
        size_weight_block="\n".join(lines[2:4]),
    )


class GeminiListingGenerator:
    def __init__(self, api_key: Optional[str], model: Optional[str]) -> None:
        if not api_key:
            raise ValueError("GEMINI_API_KEY is not set.")
        self.model_name = model or "gemini-1.5-flash"
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(self.model_name)

    def generate_listing(self, title: str, description: str) -> GptListing:
        prompt = (
            "You are an expert eBay listing writer.\n"
            "Output exactly 4 lines:\n"
            "1) English title (max 80 chars)\n"
            "2) English description (2-3 sentences)\n"
            "3) Size: L x W x H cm\n"
            "4) Weight: X kg\n\n"
            f"Title source: {title}\n"
            f"Description source: {description}\n"
        )
        response = self.model.generate_content(prompt)
        text = getattr(response, "text", "") or ""
        return _parse_four_lines(text)


class MockGptListingGenerator:
    def generate_listing(self, title: str, description: str) -> GptListing:
        return GptListing(
            title_en=f"{title} - Genuine",
            description_en=f"High quality item. Details: {description}",
            size_weight_block="Size: 30 x 20 x 10 cm\nWeight: 0.8 kg",
        )
