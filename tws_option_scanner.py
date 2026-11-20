#!/usr/bin/env python3
import os
import subprocess
import time
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '.venv', 'lib', 'python3.14', 'site-packages'))

from ib_insync import *

# SSH tunnel setup
def start_ssh_tunnel():
    """Start SSH tunnel to remote TWS"""
    sshpass_command = [
        'sshpass',
        '-p', 'trapapa',
        'ssh',
        '-o', 'StrictHostKeyChecking=no',
        '-o', 'UserKnownHostsFile=/dev/null',
        '-L', '7496:localhost:7496',
        'trapapa@192.168.178.75',
        '-N'  # No remote command, just tunnel
    ]
    
    try:
        process = subprocess.Popen(sshpass_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        time.sleep(3)  # Give tunnel time to establish
        # Check if process started successfully
        if process.poll() is None:
            return process
        else:
            stdout, stderr = process.communicate()
            print(f"SSH tunnel failed to start: {stderr.decode()}")
            return None
    except Exception as e:
        print(f"Failed to start SSH tunnel: {e}")
        return None

def main():
    # Start SSH tunnel
    print("Starting SSH tunnel to TWS...")
    tunnel_proc = start_ssh_tunnel()
    if not tunnel_proc:
        print("Could not start SSH tunnel")
        return 1
    
    try:
        # Connect to TWS via IBKR socket API
        print("Connecting to TWS...")
        ib = IB()
        ib.connect('127.0.0.1', 7496, clientId=1)
        print(f"Connected: {ib.isConnected()}")
        
        if not ib.isConnected():
            print("Failed to connect to TWS")
            return 1
        
        # Example: Get options for AAPL
        print("\nGetting option chain for AAPL...")
        contract = Stock('AAPL', 'SMART', 'USD')
        ib.qualifyContracts(contract)
        
        # Get option chains
        chains = ib.reqSecDefOptParams(contract.symbol, '', contract.secType, contract.conId)
        
        print(f"Found {len(chains)} option chains")
        for chain in chains:
            print(f"Exchange: {chain.exchange}, {len(chain.strikes)} strikes, {len(chain.expirations)} expirations")
        
        # If we have chains, get options for first expiration and nearby strikes
        if chains:
            chain = chains[0]  # Take first chain (usually SMART)
            # Get ATM strike or nearby
            underlying_price = ib.reqMktData(contract, '', False, False)
            ib.sleep(2)  # Wait for data
            price = underlying_price.last if underlying_price.last > 0 else underlying_price.close
            if price == 0:
                price = underlying_price.bid or underlying_price.ask or 100  # fallback
            
            print(f"Underlying price: {price:.2f}")
            
            # Find closest strike
            strikes = sorted(chain.strikes)
            closest_strike = min(strikes, key=lambda x: abs(x - price))
            
            # Get options for nearest expiration
            expirations = sorted(chain.expirations)
            if expirations:
                expiry = expirations[0]  # Nearest expiration
                print(f"Using expiry: {expiry}, strike: {closest_strike}")
                
                # Create call and put contracts
                call_contract = Option(chain.symbol, expiry, closest_strike, 'C', chain.exchange)
                put_contract = Option(chain.symbol, expiry, closest_strike, 'P', chain.exchange)
                
                ib.qualifyContracts(call_contract, put_contract)
                
                # Get market data for greeks and IV
                print("\nFetching option data...")
                ticker_call = ib.reqMktData(call_contract, '', False, False)
                ticker_put = ib.reqMktData(put_contract, '', False, False)
                ib.sleep(3)  # Wait for data
                
                # Display results in requested format
                print("\n" + "="*60)
                print("OPTION SCANNER RESULTS")
                print("="*60)
                
                # CALL
                print("\nCALL OPTION:")
                print(f"  Strike: {closest_strike}")
                print(f"  Expiration: {expiry}")
                print(f"  Last Price: {ticker_call.last:.2f}" if ticker_call.last else "  Last Price: N/A")
                print(f"  Bid/Ask: {ticker_call.bid:.2f}/{ticker_call.ask:.2f}" if ticker_call.bid and ticker_call.ask else "  Bid/Ask: N/A")
                print(f"  IV: {ticker_call.impliedVolatility:.4f}" if ticker_call.impliedVolatility else "  IV: N/A")
                print(f"  Delta: {ticker_call.delta:.4f}" if ticker_call.delta else "  Delta: N/A")
                print(f"  Gamma: {ticker_call.gamma:.6f}" if ticker_call.gamma else "  Gamma: N/A")
                print(f"  Theta: {ticker_call.theta:.4f}" if ticker_call.theta else "  Theta: N/A")
                print(f"  Vega: {ticker_call.vega:.4f}" if ticker_call.vega else "  Vega: N/A")
                print(f"  IVR: N/A (requires historical data)")
                print(f"  IVP: N/A (requires historical data)")
                
                # PUT
                print("\nPUT OPTION:")
                print(f"  Strike: {closest_strike}")
                print(f"  Expiration: {expiry}")
                print(f"  Last Price: {ticker_put.last:.2f}" if ticker_put.last else "  Last Price: N/A")
                print(f"  Bid/Ask: {ticker_put.bid:.2f}/{ticker_put.ask:.2f}" if ticker_put.bid and ticker_put.ask else "  Bid/Ask: N/A")
                print(f"  IV: {ticker_put.impliedVolatility:.4f}" if ticker_put.impliedVolatility else "  IV: N/A")
                print(f"  Delta: {ticker_put.delta:.4f}" if ticker_put.delta else "  Delta: N/A")
                print(f"  Gamma: {ticker_put.gamma:.6f}" if ticker_put.gamma else "  Gamma: N/A")
                print(f"  Theta: {ticker_put.theta:.4f}" if ticker_put.theta else "  Theta: N/A")
                print(f"  Vega: {ticker_put.vega:.4f}" if ticker_put.vega else "  Vega: N/A")
                print(f"  IVR: N/A (requires historical data)")
                print(f"  IVP: N/A (requires historical data)")
                
        ib.disconnect()
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        # Clean up tunnel
        if tunnel_proc:
            tunnel_proc.terminate()
            tunnel_proc.wait()
            print("\nSSH tunnel terminated")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
