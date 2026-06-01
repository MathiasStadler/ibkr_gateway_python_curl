# insiden .env
# source .venv/bin/activate
# pip install ib_insync
import csv
from ib_insync import *
import time

# --- Konfiguration ---
HOST = '127.0.0.1'
# PORT = 7497          # 7497 für TWS, 4001 für Gateway
PORT = 4002
CLIENT_ID = 1

# Verwendete Marktdatenart (3 = verzögert, 1 = live)
MARKET_DATA_TYPE = 3   # 3 = "Delayed"

# Basiswert: Apple (AAPL)
UNDERLYING_SYMBOL = 'AAPL'
EXCHANGE = 'NASDAQ'
CURRENCY = 'USD'

# Optionen: Puts, Frontmonat, Strikes innerhalb ±10 $ des aktuellen Preises
OPT_RIGHT = 'P'
STRIKE_RANGE = 10.0   # ±10 $


def get_current_price(ib):
    """Holt den aktuellen (verzögerten) Preis des Basiswerts per reqMktData (Snapshot)."""
    contract = Stock(UNDERLYING_SYMBOL, EXCHANGE, CURRENCY)
    ib.reqMarketDataType(MARKET_DATA_TYPE)
    ticker = ib.reqMktData(contract, '', snapshot=True)
    ib.sleep(2)   # Kurze Wartezeit, bis Tick eintrifft
    return ticker.last


def get_option_chain(ib, under_conid, month, strikes):
    """
    Erstellt Contract‑Objekte für Optionen.
    under_conid: benötigt man in der TWS API nicht direkt, man nutzt stattdessen die Option Definition.
    Hier: Wir bauen einfach den Options‑Contract aus Symbol, Monat, Strike, Right.
    """
    options = []
    for strike in strikes:
        # Optionscontract für Put, angegebenes Monat (Format: yyyymm)
        contract = Option(UNDERLYING_SYMBOL, month, strike, OPT_RIGHT, EXCHANGE, CURRENCY)
        options.append(contract)
    return options


def fetch_market_data(ib, contracts, wait_seconds=3):
    """
    Ruft für eine Liste von Contracts die Marktdaten per reqMktData (Streaming, snapshot=False) ab.
    Da wir verzögerte Daten wollen, reicht ein kurzes Warten, bis die ersten Ticks kommen.
    Gibt ein Dict zurück: {contract: { 'bid':..., 'ask':..., 'delta':..., etc. }}
    """
    ib.reqMarketDataType(MARKET_DATA_TYPE)
    tickers = {}
    for contract in contracts:
        # genericTickList = '106' aktiviert Option Greeks (Delta, Gamma, Theta, Vega)
        ticker = ib.reqMktData(contract, genericTickList='106', snapshot=False)
        tickers[contract] = ticker

    # Warten, bis die ersten Daten eintrudeln
    ib.sleep(wait_seconds)

    result = {}
    for contract, ticker in tickers.items():
        # Greeks sind in ticker.modelGreeks (sofern unterstützt) oder einzeln als attributes
        # Bei snapshot=False kommen sie nach einigen Sekunden; wir nehmen, was da ist.
        bid = ticker.bid if ticker.bid else None
        ask = ticker.ask if ticker.ask else None
        delta = ticker.delta if hasattr(ticker, 'delta') else None
        gamma = ticker.gamma if hasattr(ticker, 'gamma') else None
        theta = ticker.theta if hasattr(ticker, 'theta') else None
        vega = ticker.vega if hasattr(ticker, 'vega') else None

        result[contract] = {
            'conid': contract.conId,
            'symbol': contract.symbol,
            'strike': contract.strike,
            'maturityDate': contract.lastTradeDateOrContractMonth,
            'bid': bid,
            'ask': ask,
            'delta': delta,
            'gamma': gamma,
            'theta': theta,
            'vega': vega
        }
    return result


def main():
    ib = IB()
    ib.connect(HOST, PORT, clientId=CLIENT_ID)

    # 1. Verzögerten Preis des Basiswerts holen, um Strikes zu bestimmen
    underlying_price = get_current_price(ib)
    print(f"Aktueller {UNDERLYING_SYMBOL} Preis (verzögert): {underlying_price}")

    # 2. Strikes im Bereich ±10 $ ermitteln
    # Dazu benötigen wir die verfügbaren Strikes für den Frontmonat.
    # Methode: Wir rufen die Option Chain von IB API ab (z.B. ib.reqSecDefOptParams).
    # Einfachheitshalber holen wir die Strike Liste aus der REST API? Aber wir wollen ja rein TWS API.
    # Wir nutzen ib.reqSecDefOptParams, um alle Strikes für den Frontmonat zu bekommen.
    # Zuerst den Frontmonat ermitteln (wir nehmen den nächsten verfügbaren Optionsmonat).
    # Für dieses Beispiel: Der Frontmonat ist der dritte Donnerstag des nächsten Monats.
    # Wir nehmen einen festen Monat, z.B. den aktuellen + 1 Monat (Format yyyymm).
    from datetime import datetime, timedelta
    now = datetime.now()
    next_month = now.replace(day=1) + timedelta(days=32)
    front_month_str = next_month.strftime('%Y%m')  # z.B. '202506'

    # Option Parameter abfragen
    contract = Stock(UNDERLYING_SYMBOL, EXCHANGE, CURRENCY)
    opt_params = ib.reqSecDefOptParams(contract.symbol, '', contract.secType, contract.conId)
    # Finde Put Strikes für den Frontmonat
    strikes = []
    for param in opt_params:
        if param.secType == 'OPT' and param.right == OPT_RIGHT and param.exchange == EXCHANGE:
            if param.lastTradeDateOrContractMonth == front_month_str:
                # alle Strikes filtern
                all_strikes = param.strikes
                # innerhalb ±10 vom aktuellen Preis
                strikes = [s for s in all_strikes if abs(s - underlying_price) <= STRIKE_RANGE]
                break

    if not strikes:
        print(f"Keine Strikes im Bereich ±{STRIKE_RANGE} um {underlying_price} gefunden.")
        ib.disconnect()
        return

    print(f"Gefundene Strikes: {strikes}")

    # 3. Option Contracts erzeugen
    option_contracts = []
    for strike in strikes:
        opt = Option(UNDERLYING_SYMBOL, front_month_str, strike, OPT_RIGHT, EXCHANGE, CURRENCY)
        option_contracts.append(opt)

    # 4. Marktdaten mit reqMktData abholen (verzögert, Greeks mit genericTickList='106')
    market_data = fetch_market_data(ib, option_contracts, wait_seconds=5)

    # 5. CSV schreiben
    headers = ["conid", "symbol", "strike", "maturityDate", "bid", "ask", "delta", "gamma", "theta", "vega"]
    file_path = "./OptionContracts_Delayed.csv"
    with open(file_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for contract, data in market_data.items():
            writer.writerow(data)

    print(f"Fertige CSV mit verzögerten Bid/Ask und Greeks: {file_path}")

    ib.disconnect()


if __name__ == "__main__":
    main()