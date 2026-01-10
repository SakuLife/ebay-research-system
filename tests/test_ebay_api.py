"""Test eBay API connection."""

import os
from dotenv import load_dotenv


def test_ebay_credentials():
    """Check if eBay credentials are set."""
    load_dotenv()

    print("\n" + "="*60)
    print("EBAY API CREDENTIALS CHECK")
    print("="*60)

    ebay_client_id = os.getenv("EBAY_CLIENT_ID")
    ebay_client_secret = os.getenv("EBAY_CLIENT_SECRET")
    ebay_refresh_token = os.getenv("EBAY_REFRESH_TOKEN")

    print(f"\nEBAY_CLIENT_ID: {'[OK] Set' if ebay_client_id else '[X] Not set'}")
    print(f"EBAY_CLIENT_SECRET: {'[OK] Set' if ebay_client_secret else '[X] Not set'}")
    print(f"EBAY_REFRESH_TOKEN: {'[OK] Set' if ebay_refresh_token else '[X] Not set'}")

    if ebay_client_id:
        print(f"\nClient ID (first 10 chars): {ebay_client_id[:10]}...")

    if not all([ebay_client_id, ebay_client_secret, ebay_refresh_token]):
        print("\n[WARN] eBay credentials incomplete")
        print("  Add to .env:")
        print("  EBAY_CLIENT_ID=your_client_id")
        print("  EBAY_CLIENT_SECRET=your_client_secret")
        print("  EBAY_REFRESH_TOKEN=your_refresh_token")
        return False

    print("\n[OK] All eBay credentials are set!")
    return True


def test_ebay_api_call():
    """Test actual eBay API call (if credentials are set)."""
    load_dotenv()

    if not test_ebay_credentials():
        return

    print("\n" + "="*60)
    print("EBAY API TEST CALL")
    print("="*60)

    # TODO: Implement actual eBay API call
    print("\n[INFO] eBay API implementation pending...")
    print("  Will implement:")
    print("  1. OAuth token refresh")
    print("  2. Get item details by URL/ID")
    print("  3. Search completed listings")

    # For now, test with mock client
    from src.ebay_client import MockEbayClient

    client = MockEbayClient()
    print(f"\n[TEST] Using MockEbayClient for now")

    # Test search
    results = client.search_completed(keyword="Nintendo Switch", market="US")
    print(f"\n[RESULTS] Found {len(results)} mock results")

    if results:
        first = results[0]
        print(f"  First result:")
        print(f"    ID: {first.candidate_id}")
        print(f"    Query: {first.search_query}")
        print(f"    Price: ${first.ebay_price}")
        print(f"    Shipping: ${first.ebay_shipping}")
        print(f"    Sold: {first.sold_signal}")


if __name__ == "__main__":
    test_ebay_api_call()
