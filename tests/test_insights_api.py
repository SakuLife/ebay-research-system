"""Test Marketplace Insights API in production mode.

This test requires Production credentials in .env:
- EBAY_CLIENT_ID (Production, not Sandbox)
- EBAY_CLIENT_SECRET (Production)
- EBAY_USE_SANDBOX=false

Note: Marketplace Insights API is a "Limited Release" API.
You may need to request access from eBay Developer Program.
"""

import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

# Force production mode
os.environ['EBAY_USE_SANDBOX'] = 'false'

from src.ebay_client import EbayClient


def main():
    print("=" * 60)
    print("Marketplace Insights API Test")
    print("=" * 60)

    client = EbayClient()
    print(f"Sandbox mode: {client.use_sandbox}")
    print(f"Client ID: {client.client_id[:30] if client.client_id else 'Not set'}...")

    # Check if using Sandbox credentials
    if client.client_id and 'Sandbox' in client.client_id:
        print()
        print("[ERROR] Sandbox credentials detected!")
        print("  Production APIs require Production credentials.")
        print("  Please update .env with Production EBAY_CLIENT_ID/SECRET")
        return

    print()

    # First test if Browse API works (to verify credentials)
    print("=== Step 1: Verify credentials with Browse API ===")
    try:
        token = client._get_access_token()
        print(f"[OK] Browse API token obtained: {token[:20]}...")
    except Exception as e:
        print(f"[FAIL] Browse API token failed: {e}")
        print("  Credentials are not valid for production.")
        return

    # Test Browse API search
    print("\nTesting search_active_listings('Pokemon', market='US')...")
    active_results = client.search_active_listings('Pokemon', market='US')
    print(f"[OK] Active listings found: {len(active_results)} items")

    print()
    print("=== Step 2: Test Marketplace Insights API ===")
    print("Note: This API is Limited Release. May return 403 if not approved.")
    print()

    # Test Insights API
    print("Testing search_sold_items('Pokemon Japanese', market='US', min_sold=2)...")
    results = client.search_sold_items('Pokemon Japanese', market='US', min_sold=2)

    if results:
        print(f"[OK] Sold items found: {len(results)} items")
        print()
        print("Top results:")
        for r in results[:5]:
            print(f"  - Sold: {r.sold_signal}x, Price: ${r.ebay_price:.2f}")
    else:
        print("[INFO] No sold items returned.")
        print("  Possible reasons:")
        print("  - API access not approved (Limited Release)")
        print("  - No items match the search criteria")


if __name__ == "__main__":
    main()
