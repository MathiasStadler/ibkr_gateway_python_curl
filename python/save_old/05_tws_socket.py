"""
TWS Socket API - Option data fetcher for CROX (Oct 2026)
Uses ib_insync to connect to TWS socket (port 7496).
"""
import csv
import logging
import time
import sys
from ib_insync import IB, Option

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def main():
    ticker = sys.argv[1].upper() if len(sys.argv) > 1 else 'CROX'
    num_months = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    logger.info(f"Processing ticker: {ticker}, next {num_months} months")

    # Connect to TWS socket
    ib = IB()
    import random
    client_id = random.randint(100, 999)
    ib.connect('127.0.0.1', 7496, clientId=client_id)
    logger.info(f'Connected to TWS (clientId={client_id})')

    # Search for option contracts
    try:
        contracts = ib.reqContractDetails(Option(ticker, '', '', 'P', 'SMART'))
        logger.info(f"Found {len(contracts)} option contracts")
    except Exception as e:
        logger.error(f"Failed to search contracts: {e}")
        ib.disconnect()
        sys.exit(1)

    # Filter by month and put right
    selected_contracts = []
    for cd in contracts:
        c = cd.contract
        if c.right != 'P':
            continue
        if c.lastTradeDateOrContractMonth:
            month = c.lastTradeDateOrContractMonth[:6]  # YYYYMM
            selected_contracts.append((month, c))

    if not selected_contracts:
        logger.error("No put option contracts found")
        ib.disconnect()
        sys.exit(1)

    # Get unique months and select first N
    months = sorted(set(m for m, _ in selected_contracts))
    selected_months = months[:num_months]
    logger.info(f"Selected months: {selected_months}")

    # Filter contracts to selected months
    final_contracts = [(m, c) for m, c in selected_contracts if m in selected_months]
    logger.info(f"Total contracts: {len(final_contracts)}")

    # Request market data (DELAYED = 4)
    ib.reqMarketDataType(4)
    logger.info("Requested market data...")

    # Fetch market data for each contract
    all_data = []
    for month, c in final_contracts:
        try:
            # Request with genericTickList '103' for Option Greeks
            ticker_data = ib.reqMktData(c, '103', False, False)
            time.sleep(3)  # Wait for data

            # Extract data with fallback to n/a
            bid = ticker_data.bid if ticker_data.bid and ticker_data.bid != 'NaN' else 'n/a'
            ask = ticker_data.ask if ticker_data.ask and ticker_data.ask != 'NaN' else 'n/a'
            
            # Greeks are in ticker.bidGreeks (if requested)
            greeks = getattr(ticker_data, 'bidGreeks', None)
            delta = greeks.delta if greeks and hasattr(greeks, 'delta') else 'n/a'
            gamma = greeks.gamma if greeks and hasattr(greeks, 'gamma') else 'n/a'
            theta = greeks.theta if greeks and hasattr(greeks, 'theta') else 'n/a'
            vega = greeks.vega if greeks and hasattr(greeks, 'vega') else 'n/a'
            volume = ticker_data.volume if ticker_data.volume else 'n/a'
            open_interest = ticker_data.openInterest if ticker_data.openInterest else 'n/a'

            row = {
                'conid': str(c.conid),
                'symbol': c.symbol,
                'right': c.right,
                'month': month,
                'strike': str(c.strike),
                'maturityDate': c.lastTradeDateOrContractMonth,
                'bid': str(bid),
                'ask': str(ask),
                'delta': str(delta) if delta else 'n/a',
                'gamma': str(gamma) if gamma else 'n/a',
                'theta': str(theta) if theta else 'n/a',
                'vega': str(vega) if vega else 'n/a',
                'volume': str(volume) if volume else 'n/a',
                'open_interest': str(open_interest) if open_interest else 'n/a',
                'historical_volatility': 'n/a',
                'implied_volatility': 'n/a'
            }
            all_data.append(row)
            logger.info(f"  {c.symbol} {c.strike} {c.right}: bid={bid}, ask={ask}, delta={delta}")
        except Exception as e:
            logger.error(f"  Error for {c.symbol} {c.strike}: {e}")
        finally:
            ib.cancelMktData(c)

    # Write CSV
    headers = ['conid', 'symbol', 'right', 'month', 'strike', 'maturityDate',
               'bid', 'ask', 'delta', 'gamma', 'theta', 'vega',
               'volume', 'open_interest', 'historical_volatility', 'implied_volatility']
    csv_path = './DelayOptionContracts.csv'
    with open(csv_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(all_data)
    logger.info(f"✅ CSV saved to {csv_path}")

    ib.disconnect()
    logger.info("Disconnected from TWS")

if __name__ == '__main__':
    main()
