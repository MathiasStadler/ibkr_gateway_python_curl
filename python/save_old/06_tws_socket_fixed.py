"""
TWS Socket API - Option data fetcher for CROX (Oct 2026)
Uses ib_insync to connect to TWS socket (port 7496).
Robust extraction with safe getattr for all attributes.
"""
import csv
import logging
import time
import sys
import random
from ib_insync import IB, Option

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def extract_ticker_data(ticker_data):
    # Bid/Ask mit safety
    bid = getattr(ticker_data, 'bid', None)
    ask = getattr(ticker_data, 'ask', None)
    bid = bid if bid is not None and bid != 'NaN' else 'n/a'
    ask = ask if ask is not None and ask != 'NaN' else 'n/a'
    
    # Greeks: mehrere mögliche Quellen
    greeks = None
    for attr in ['bidGreeks', 'lastGreeks', 'modelGreeks', 'greeks']:
        greeks = getattr(ticker_data, attr, None)
        if greeks:
            break
    
    delta = gamma = theta = vega = 'n/a'
    if greeks:
        delta = getattr(greeks, 'delta', None) if hasattr(greeks, 'delta') else None
        gamma = getattr(greeks, 'gamma', None) if hasattr(greeks, 'gamma') else None
        theta = getattr(greeks, 'theta', None) if hasattr(greeks, 'theta') else None
        vega = getattr(greeks, 'vega', None) if hasattr(greeks, 'vega') else None
        
        delta = delta if delta is not None else 'n/a'
        gamma = gamma if gamma is not None else 'n/a'
        theta = theta if theta is not None else 'n/a'
        vega = vega if vega is not None else 'n/a'
    
    # Volumen und Open Interest
    volume = getattr(ticker_data, 'volume', None)
    open_interest = getattr(ticker_data, 'openInterest', None)
    volume = volume if volume else 'n/a'
    open_interest = open_interest if open_interest else 'n/a'
    
    return {
        'bid': bid, 'ask': ask, 'delta': delta,
        'gamma': gamma, 'theta': theta, 'vega': vega,
        'volume': volume, 'open_interest': open_interest
    }

ib = IB()
# Random client ID to avoid connection conflicts
client_id = random.randint(100, 999)
ib.connect('127.0.0.1', 7496, clientId=client_id)
logger.info(f'Connected to TWS (clientId={client_id})')

# Marktdaten typ festlegen (DELAYED = 4)
ib.reqMarketDataType(4)

def get_option_contracts(symbol, expiry_months, max_strikes=5):
    """Findet Puts für das gegebene Symbol und die Expiry-Monate"""
    logger.info(f"Searching for {symbol} options...")
    final_contracts = []
    for month in expiry_months:
        # CROX: Abfrage relevanter Strikes 75..180 in 5er-Schritten
        strikes = list(range(75, 181, 5))
        for strike in strikes:
            try:
                opt = Option(symbol, month, strike, 'P', 'SMART', tradingClass=symbol)
                details = ib.reqContractDetails(opt)
                if details:
                    final_contracts.append((month, details[0].contract))
            except Exception as e:
                logger.warning(f"Could not fetch {symbol} {month} {strike} P: {e}")
    logger.info(f"Selected months: {[m for m, _ in final_contracts]}")
    logger.info(f"Total contracts: {len(final_contracts)}")
    return final_contracts

def main(symbol, expiry_months):
    final_contracts = get_option_contracts(symbol, expiry_months)
    all_data = []
    for month, c in final_contracts:
        try:
            # Request with genericTickList '103' for Option Greeks
            ticker_data = ib.reqMktData(c, '103', False, False)
            time.sleep(3)
            
            # Extrahieren der Daten
            data = extract_ticker_data(ticker_data)
            data.update({
                'conid': str(c.conId),
                'symbol': c.symbol,
                'right': c.right,
                'month': month,
                'strike': c.strike,
                'maturityDate': c.lastTradeDateOrContractMonth
            })
            all_data.append(data)
            logger.info(f"✅ {symbol} {month} ${c.strike} P: bid={data['bid']}, ask={data['ask']}, delta={data['delta']}")
        except Exception as e:
            logger.error(f"❌ {symbol} {month} ${c.strike} P: {e}")
            # Füllen mit n/a
            all_data.append({
                'conId': str(c.conId),
                'symbol': c.symbol,
                'right': c.right,
                'month': month,
                'strike': c.strike,
                'maturityDate': c.lastTradeDateOrContractMonth,
                'bid': 'n/a', 'ask': 'n/a', 'delta': 'n/a',
                'gamma': 'n/a', 'theta': 'n/a', 'vega': 'n/a',
                'volume': 'n/a', 'open_interest': 'n/a'
            })
    
    csv_path = './DelayOptionContracts.csv'
    with open(csv_path, 'w', newline='') as f:
        fieldnames = ['conid', 'symbol', 'right', 'month', 'strike', 'maturityDate',
                      'bid', 'ask', 'delta', 'gamma', 'theta', 'vega',
                      'volume', 'open_interest', 'historical_volatility', 'implied_volatility']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in all_data:
            writer.writerow(row)
    logger.info(f"✅ CSV saved to {csv_path}")
    
    return all_data

if __name__ == '__main__':
    if len(sys.argv) >= 2:
        symbol = sys.argv[1]
        expiry_months = [sys.argv[2]] if len(sys.argv) > 2 else ['20261016']
    else:
        symbol = 'CROX'
        expiry_months = ['20261016']
    main(symbol, expiry_months)
    ib.disconnect()
