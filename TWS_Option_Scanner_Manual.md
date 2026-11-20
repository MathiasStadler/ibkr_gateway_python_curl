# TWS Option Scanner Manual

## Overview
This manual describes how to use the TWS Option Scanner script to retrieve option chain data with Greeks (delta, gamma, theta, vega) and implied volatility (IV) via Interactive Brokers' TWS Socket API (port 7496) over an SSH tunnel.

The script connects to a remote TWS instance, establishes an SSH tunnel, queries option data for a given symbol, and outputs a formatted table with the requested columns:

- Strike
- Expiration
- Last price
- Bid/Ask
- IV (Implied Volatility)
- Delta
- Gamma
- Theta
- Vega
- IVR (Implied Volatility Rank) – *Not available in real‑time data*
- IVP (Implied Volatility Percentile) – *Not available in real‑time data*

IVR and IVP require historical IV calculations and are therefore omitted; placeholders show "N/A".

## Prerequisites

1. **Remote machine** (IP `192.168.178.75`) must have:
   - TWS (Trader Workstation) installed and running.
   - SSH server accessible with username `trapapa` and password `trapapa` (as stored in your environment; for production use key‑based auth).
   - TWS configured to accept socket API connections:
     - In TWS: `Edit → Configuration → API`
     - Check **Enable ActiveX and Socket Clients**
     - Set **Socket port** to `7496` (default) – adjust the script if you use a different port.

2. **Local machine** (where you run the script):
   - Python 3.11+ (the provided virtual environment uses Python 3.14.9).
   - The `ib_insync` package installed (already present in `~/ibkr_gateway_python_curl/.venv`).
   - `sshpass` installed for SSH tunneling (available on most Linux distributions; install via `apt-get install sshpass` if missing).

3. **Network**:
   - Ensure you can SSH to the remote host:
     ```bash
     sshpass -p trapapa ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null trapapa@192.168.178.75 echo "SSH OK"
     ```
   - Verify that TWS is listening on port 7496 on the remote host:
     ```bash
     sshpass -p trapapa ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null trapapa@192.168.178.75 "nc -z localhost 7496 && echo 'Port 7496 open' || echo 'Port 7496 closed'"
     ```

## Script Location
```
~/ibkr_gateway_python_curl/tws_option_scanner_final.py
```

## Installation & Setup

1. **Clone / ensure the project exists** (you already have it):
   ```bash
   cd ~
   git clone <your-repo-url> ibkr_gateway_python_curl   # if not already present
   ```

2. **Activate the virtual environment**:
   ```bash
   cd ~/ibkr_gateway_python_curl
   source .venv/bin/activate
   ```

3. **Make the script executable**:
   ```bash
   chmod +x tws_option_scanner_final.py
   ```

## Usage

Run the script from the project directory:

```bash
.venv/bin/python tws_option_scanner_final.py [SYMBOL]
```

- `[SYMBOL]`: Optional stock symbol (e.g., `AAPL`, `MSFT`). If omitted, defaults to `AAPL`.

### Example
```bash
.venv/bin/python tws_option_scanner_final.py AAPL
```

### Expected Output
```
================================================================================
OPTION SCANNER RESULTS FOR AAPL
================================================================================
Type   Strike   Expiration  Last    Bid/Ask   IV      Delta   Gamma     Theta   Vega    IVR     IVP
--------------------------------------------------------------------------------
CALL   150.00   20261218    12.34   12.30/12.38 0.2145  0.4521  0.012345 -0.0845 0.1234  N/A     N/A
PUT    150.00   20261218    10.21   10.18/10.24 0.2210 -0.5489  0.012345 -0.0798 0.1190  N/A     N/A
================================================================================
Note: IVR (Implied Volatility Rank) and IVP (Implied Volatility Percentile)
require historical IV data calculations and are not included in this scan.
================================================================================
```

### Column Descriptions

| Column   | Description |
|----------|-------------|
| **Type** | Option type: `CALL` or `PUT` |
| **Strike** | Strike price of the option |
| **Expiration** | Expiration date in `YYYYMMDD` format |
| **Last** | Last traded price (or `N/A` if unavailable) |
| **Bid/Ask** | Bid price / Ask price (format `bid/ask`) |
| **IV** | Implied volatility (as a decimal, e.g., `0.2145` = 21.45%) |
| **Delta** | Delta Greek (rate of change of option price vs. underlying) |
| **Gamma** | Gamma Greek (rate of change of delta) |
| **Theta** | Theta Greek (time decay) |
| **Vega** | Vega Greek (sensitivity to volatility) |
| **IVR** | Implied Volatility Rank – requires historical IV; shown as `N/A` |
| **IVP** | Implied Volatility Percentile – requires historical IV; shown as `N/A` |

## How It Works

1. **SSH Tunnel**: The script launches an SSH tunnel (`ssh -L 7496:localhost:7496`) to forward the remote TWS socket API port to localhost.
2. **Connection**: Uses `ib_insync.IB` to connect to `127.0.0.1:7496` (the tunneled port).
3. **Contract Qualification**: Qualifies a stock contract for the given symbol to obtain its `conId`.
4. **Underlying Price**: Requests a market data snapshot to derive the current underlying price (mid of bid/ask or last).
5. **Option Chains**: Calls `reqSecDefOptParams` to retrieve available strikes and expirations.
6. **ATM Selection**: Selects the strike closest to the underlying price and the nearest (front‑month) expiration.
7. **Market Data**: Subscribes to market data for the ATM call and put options to retrieve Greeks and IV.
8. **Formatting**: Prints a readable table; waits briefly for data to populate, then disconnects and tears down the SSH tunnel.

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| `API connection failed: TimeoutError()` | TWS not running, socket API not enabled, or wrong port | Ensure TWS is running, API enabled, port 7496 open. Verify with `nc -z localhost 7496` over the tunnel. |
| `SSH tunnel failed to start` | SSH credentials incorrect, `sshpass` missing, or network issue | Check username/password, test SSH manually, install `sshpass`. |
| No data displayed (all `N/A`) | Market data subscription not receiving data (e.g., TWS in paper trading without subscription, or market closed) | Ensure you have market data subscriptions for the symbol, or delay longer (`ib.sleep(5)`). |
| `Qualified contract: None` | Symbol invalid or not found in TWS | Verify symbol spelling and that TWS has access to the exchange. |
| Script hangs | Waiting for data longer than expected | Increase `ib.sleep()` calls or check TWS logs for errors. |

## Adapting the Script

- **Different port**: Change `REMOTE_PORT` and `LOCAL_PORT` in `start_ssh_tunnel()` if your TWS socket API uses a non‑default port.
- **Different SSH user/host**: Edit `REMOTE_HOST`, `REMOTE_USER`, `REMOTE_PASS` variables (or better, use SSH keys and remove password).
- **Multiple strikes/expirations**: Modify the selection logic to loop over a list of strikes or export to CSV.
- **Historical IVR/IVP**: Implement a separate script that fetches historical option data (e.g., via `reqHistoricalData` or downloading CSV from IBKR) and computes rank/percentile.

## Safety & Best Practices

- **Do not hard‑code passwords** in production. Use SSH key authentication and replace the `sshpass` line with a plain `ssh` call.
- **Limit concurrent connections**: Avoid running many instances simultaneously; each opens a socket API connection.
- **Monitor TWS logs** for API warnings or errors.
- **Disconnect cleanly**: The script always calls `ib.disconnect()` and terminates the SSH tunnel in a `finally` block.

## References

- Interactive Brokers API Documentation: https://www.interactivebrokers.com/campus/ibkr-api-page/tws-api-doc/
- ib_insync GitHub: https://github.com/erdewit/ib_insync
- SSH Tunneling: https://www.ssh.com/academy/ssh/tunneling/example

---  
*Manual generated from the script header and project context. For updates, refer to the script source or re‑run with `--help` (if added).*