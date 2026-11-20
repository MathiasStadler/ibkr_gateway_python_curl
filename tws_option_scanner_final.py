#!/usr/bin/env python3
"""
Option Scanner using TWS Socket API via SSH Tunnel
Connects to TWS on remote machine via SSH tunnel and scans option chain.
Outputs: strike, expiration, greeks (delta, gamma, theta, vega), IV
Note: IVR and IVP require historical IV data and are not included.
"""
import subprocess
import time
import sys
import os
from ib_insync import IB, Stock, Option
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s : %(message)s')
logger = logging.getLogger(__name__)

def start_ssh_tunnel(remote_host, remote_user, remote_pass, local_port=7496, remote_port=7496):
    """Start SSH tunnel to remote TWS"""
    sshpass_command = [
        'sshpass',
        '-p', remote_pass,
        'ssh',
        '-o', 'StrictHostKeyChecking=no',
        '-o', 'UserKnownHostsFile=/dev/null',
        '-L', f'{local_port}:localhost:{remote_port}',
        f'{remote_user}@{remote_host}',
        '-N'  # No remote command, just tunnel
    ]
    
    try:
        logger.info(f"Starting SSH tunnel to {remote_host}:{remote_port}...")
        process = subprocess.Popen(sshpass_command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(3)  # Give tunnel time to establish
        
        # Check if process is still running
        if process.poll() is None:
            logger.info("SSH tunnel started successfully")
            return process
        else:
            logger.error("SSH tunnel failed to start")
            return None
    except Exception as e:
        logger.error(f"Failed to start SSH tunnel: {e}")
        return None

def scan_options(symbol="AAPL"):
    """Scan options for given symbol and return formatted results"""
    # Connection parameters for remote TWS via SSH tunnel
    REMOTE_HOST = "192.168.178.75"
    REMOTE_USER = "trapapa"
    REMOTE_PASS = "trapapa"  # From memory - note: in production use .venv/bin/activatekey-based auth
    
    # Start SSH tunnel
    tunnel_proc = start_ssh_tunnel(REMOTE_HOST, REMOTE_USER, REMOTE_PASS)
    if not tunnel_proc:
        logger.error("Could not establish SSH tunnel to remote TWS")
        return None
    
    ib = IB()
    try:
        logger.info("Connecting to TWS via SSH tunnel (localhost:7496)...")
        ib.connect('127.0.0.1', 7496, clientId=1, timeout=10)
        logger.info(f"Connected to TWS: {ib.isConnected()}")
        
        if not ib.isConnected():
            logger.error("Failed to connect to TWS")
            return None
        
        # Create stock contract and qualify
        contract = Stock(symbol, 'SMART', 'USD')
        ib.qualifyContracts(contract)
        logger.info(f"Qualified contract: {contract}")
        
        # Get market data for stock to determine price
        ticker = ib.reqMktData(contract, '', False, False)
        ib.sleep(2)  # Wait for data
        
        if ticker.bid and ticker.ask:
            underlying_price = (ticker.bid + ticker.ask) / 2
        elif ticker.last:
            underlying_price = ticker.last
        else:
            underlying_price = ticker.close or 0
            
        if underlying_price == 0:
            logger.warning("Could not get underlying price, using default")
            underlying_price = 150  # fallback for AAPL
        
        logger.info(f"Underlying price for {symbol}: {underlying_price:.2f}")
        
        # Get option chains
        chains = ib.reqSecDefOptParams(contract.symbol, '', contract.secType, contract.conId)
        if not chains:
            logger.error("No option chains found")
            return None
            
        logger.info(f"Found {len(chains)} option chains")
        
        # Use first chain (usually SMART)
        chain = chains[0]
        logger.info(f"Using exchange: {chain.exchange}")
        
        # Get strikes and expirations
        strikes = sorted(chain.strikes)
        expirations = sorted(chain.expirations)
        
        logger.info(f"Available strikes: {len(strikes)} (from {min(strikes)} to {max(strikes)})")
        logger.info(f"Available expirations: {len(expirations)} (from {min(expirations)} to {max(expirations)})")
        
        # Select ATM strike and nearest expiration
        atm_strike = min(strikes, key=lambda x: abs(x - underlying_price))
        nearest_expiry = expirations[0]  # Front month
        
        logger.info(f"Selected strike: {atm_strike}, expiration: {nearest_expiry}")
        
        # Create call and put option contracts
        call_contract = Option(symbol, nearest_expiry, atm_strike, 'C', chain.exchange)
        put_contract = Option(symbol, nearest_expiry, atm_strike, 'P', chain.exchange)
        
        ib.qualifyContracts(call_contract, put_contract)
        logger.info(f"Qualified call: {call_contract}")
        logger.info(f"Qualified put: {put_contract}")
        
        # Request market data for Greeks and IV
        logger.info("Requesting market data for options...")
        call_ticker = ib.reqMktData(call_contract, '', False, False)
        put_ticker = ib.reqMktData(put_contract, '', False, False)
        
        # Wait for data to arrive
        ib.sleep(3)
        
        # Format results
        results = []
        
        # CALL
        call_data = {
            'type': 'CALL',
            'strike': atm_strike,
            'expiration': nearest_expiry,
            'last': call_ticker.last if call_ticker.last else None,
            'bid': call_ticker.bid if call_ticker.bid else None,
            'ask': call_ticker.ask if call_ticker.ask else None,
            'iv': call_ticker.impliedVolatility if call_ticker.impliedVolatility else None,
            'delta': call_ticker.delta if call_ticker.delta else None,
            'gamma': call_ticker.gamma if call_ticker.gamma else None,
            'theta': call_ticker.theta if call_ticker.theta else None,
            'vega': call_ticker.vega if call_ticker.vega else None,
            'ivr': None,  # Requires historical data
            'ivp': None   # Requires historical data
        }
        results.append(call_data)
        
        # PUT
        put_data = {
            'type': 'PUT',
            'strike': atm_strike,
            'expiration': nearest_expiry,
            'last': put_ticker.last if put_ticker.last else None,
            'bid': put_ticker.bid if put_ticker.bid else None,
            'ask': put_ticker.ask if put_ticker.ask else None,
            'iv': put_ticker.impliedVolatility if put_ticker.impliedVolatility else None,
            'delta': put_ticker.delta if put_ticker.delta else None,
            'gamma': put_ticker.gamma if put_ticker.gamma else None,
            'theta': put_ticker.theta if put_ticker.theta else None,
            'vega': put_ticker.vega if put_ticker.vega else None,
            'ivr': None,  # Requires historical data
            'ivp': None   # Requires historical data
        }
        results.append(put_data)
        
        return results
        
    except Exception as e:
        logger.error(f"Error during option scan: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None
    finally:
        if ib.isConnected():
            ib.disconnect()
            logger.info("Disconnected from TWS")
        if tunnel_proc:
            tunnel_proc.terminate()
            tunnel_proc.wait()
            logger.info("SSH tunnel terminated")

def main():
    if len(sys.argv) > 1:
        symbol = sys.argv[1]
    else:
        symbol = "AAPL"  # Default
    
    logger.info(f"Starting option scan for {symbol}")
    results = scan_options(symbol)
    
    if results is None:
        logger.error("Option scan failed. Please ensure:")
        logger.error("1. TWS is running on remote machine (192.168.178.75)")
        logger.error("2. TWS is configured to accept socket API connections (Edit -> Configuration -> API)")
        logger.error("3. SSH access to remote machine is working")
        sys.exit(1)
    
    # Print header
    print("\n" + "="*80)
    print(f"OPTION SCANNER RESULTS FOR {symbol}")
    print("="*80)
    print(f"{'Type':<6} {'Strike':<8} {'Expiration':<12} {'Last':<8} {'Bid/Ask':<12} {'IV':<8} {'Delta':<8} {'Gamma':<10} {'Theta':<8} {'Vega':<8} {'IVR':<6} {'IVP':<6}")
    print("-"*80)
    
    for r in results:
        # Format values for display
        last_str = f"{r['last']:.2f}" if r['last'] is not None else "N/A"
        bid_str = f"{r['bid']:.2f}" if r['bid'] is not None else "0.00"
        ask_str = f"{r['ask']:.2f}" if r['ask'] is not None else "0.00"
        bidask_str = f"{bid_str}/{ask_str}"
        iv_str = f"{r['iv']:.4f}" if r['iv'] is not None else "N/A"
        delta_str = f"{r['delta']:.4f}" if r['delta'] is not None else "N/A"
        gamma_str = f"{r['gamma']:.6f}" if r['gamma'] is not None else "N/A"
        theta_str = f"{r['theta']:.4f}" if r['theta'] is not None else "N/A"
        vega_str = f"{r['vega']:.4f}" if r['vega'] is not None else "N/A"
        ivr_str = "N/A"  # Placeholder
        ivp_str = "N/A"  # Placeholder
        
        print(f"{r['type']:<6} {r['strike']:<8.2f} {r['expiration']:<12} {last_str:<8} {bidask_str:<12} {iv_str:<8} {delta_str:<8} {gamma_str:<10} {theta_str:<8} {vega_str:<8} {ivr_str:<6} {ivp_str:<6}")
    
    print("="*80)
    print("Note: IVR (Implied Volatility Rank) and IVP (Implied Volatility Percentile)")
    print("require historical IV data calculations and are not included in this scan.")
    print("="*80)

if __name__ == "__main__":
    main()
