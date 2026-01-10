"""Local test without GitHub Actions - direct execution."""

import os
from datetime import datetime
from dotenv import load_dotenv


def test_local_ebay_to_spreadsheet():
    """
    Test the full pipeline locally without GitHub Actions.
    This simulates what GitHub Actions would do.
    """
    load_dotenv()

    print("\n" + "="*60)
    print("LOCAL PIPELINE TEST (No GitHub Token needed)")
    print("="*60)

    # Check required credentials
    required_vars = [
        "GOOGLE_SERVICE_ACCOUNT_JSON",
        "SHEETS_SPREADSHEET_ID",
        "RAKUTEN_APPLICATION_ID",
    ]

    optional_vars = [
        "EBAY_CLIENT_ID",
        "AMAZON_ACCESS_KEY_ID",
        "GEMINI_API_KEY",
    ]

    print("\n[CREDENTIALS CHECK]")
    for var in required_vars:
        value = os.getenv(var)
        if value:
            print(f"  {var}: OK")
        else:
            print(f"  {var}: MISSING (required)")
            return

    for var in optional_vars:
        value = os.getenv(var)
        if value:
            print(f"  {var}: OK")
        else:
            print(f"  {var}: Not set (optional)")

    # Import modules
    from src.ebay_client import EbayClient
    from src.sourcing import SourcingClient
    from src.profit import calculate_profit
    from src.sheets_client import GoogleSheetsClient
    from src.models import ListingCandidate

    # Initialize clients
    print("\n[INITIALIZATION]")

    # eBay client
    ebay_client_id = os.getenv("EBAY_CLIENT_ID")
    if ebay_client_id:
        print("  eBay: Real API")
        ebay_client = EbayClient()
    else:
        print("  eBay: MOCK (set EBAY_CLIENT_ID for real)")
        from src.ebay_client import MockEbayClient
        ebay_client = MockEbayClient()

    # Sourcing client
    print("  Sourcing: Real API (Rakuten/Amazon)")
    sourcing_client = SourcingClient()

    # Sheets client
    service_account = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    spreadsheet_url = os.getenv("SHEETS_SPREADSHEET_ID")
    sheets_client = GoogleSheetsClient(
        service_account_file=service_account,
        spreadsheet_url=spreadsheet_url
    )
    print(f"  Spreadsheet: {sheets_client.spreadsheet.title}")

    # Test data - eBay URL
    test_ebay_url = input("\n[INPUT] Enter eBay URL (or press Enter for mock): ").strip()

    if test_ebay_url:
        print(f"\n[EBAY] Fetching item info from: {test_ebay_url}")
        # TODO: Implement real eBay API call
        ebay_title = "Nintendo Switch Console"
        ebay_price = 299.99
        ebay_shipping = 15.00
        print("  (Using mock data - eBay API not yet implemented)")
    else:
        print("\n[EBAY] Using mock data")
        ebay_title = "Gaming Mouse RGB LED"
        ebay_price = 29.99
        ebay_shipping = 5.00

    print(f"  Title: {ebay_title}")
    print(f"  Price: ${ebay_price}")
    print(f"  Shipping: ${ebay_shipping}")

    # Create listing candidate
    listing = ListingCandidate(
        candidate_id=f"LOCAL-TEST-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        search_query=ebay_title,
        ebay_item_url=test_ebay_url or "https://ebay.com/itm/mock",
        ebay_price=ebay_price,
        ebay_shipping=ebay_shipping,
        sold_signal=100
    )

    # Search domestic sources
    print(f"\n[SOURCING] Searching: {ebay_title}")
    offer = sourcing_client.search_best_offer(listing)

    if offer:
        print(f"  Found: {offer.source_site}")
        print(f"  Price: JPY {offer.source_price_jpy:,.0f}")
        print(f"  URL: {offer.source_url[:60]}...")
    else:
        print("  No results found")
        return

    # Calculate profit
    print(f"\n[PROFIT] Calculating...")
    from src.config_loader import load_all_configs
    configs = load_all_configs()

    profit = calculate_profit(
        ebay_price=ebay_price,
        ebay_shipping=ebay_shipping,
        source_price_jpy=offer.source_price_jpy,
        fee_rules=configs.fee_rules
    )

    print(f"  Revenue: JPY {(ebay_price + ebay_shipping) * profit.fx_rate:,.0f}")
    print(f"  Cost: JPY {offer.source_price_jpy + 800:,.0f}")
    print(f"  Profit: JPY {profit.profit_jpy_no_rebate:,.0f}")
    print(f"  Margin: {profit.profit_margin_no_rebate * 100:.1f}%")
    print(f"  Profitable: {'YES' if profit.is_profitable else 'NO'}")

    # Ask before writing
    confirm = input(f"\n[CONFIRM] Write to 入力シート? (y/N): ").strip().lower()
    if confirm != 'y':
        print("\n[SKIP] Not writing to spreadsheet")
        return

    # Prepare row data
    from src.spreadsheet_mapping import INPUT_SHEET_COLUMNS

    row_data = [""] * len(INPUT_SHEET_COLUMNS)
    row_data[0] = datetime.now().strftime("%Y-%m-%d")  # 日付
    row_data[1] = listing.ebay_item_url  # eBay URL
    row_data[2] = "LOCAL-TEST"  # リサーチ作業者
    row_data[3] = "LOCAL"  # リサーチ方法
    row_data[9] = ebay_title[:50]  # キーワード
    row_data[13] = ebay_title  # 検索クエリ
    row_data[14] = offer.source_url  # ソーシング1 URL
    row_data[15] = str(offer.source_price_jpy)  # ソーシング1 価格
    row_data[21] = str(ebay_price)  # eBay売値
    row_data[22] = str(ebay_shipping)  # eBay送料
    row_data[27] = str(profit.profit_jpy_no_rebate)  # 利益額
    row_data[28] = str(profit.profit_margin_no_rebate * 100)  # 利益率%
    row_data[31] = "LOCAL-TEST"  # ステータス
    row_data[33] = f"Local test - {datetime.now().isoformat()}"  # ログ

    # Write to spreadsheet
    print(f"\n[WRITE] Writing to 入力シート...")
    worksheet = sheets_client.spreadsheet.worksheet("入力シート")
    worksheet.append_row(row_data)

    print(f"\n[SUCCESS] Written to spreadsheet!")
    print(f"  Check: {spreadsheet_url}")

    print(f"\n" + "="*60)
    print("LOCAL TEST COMPLETED")
    print("="*60)
