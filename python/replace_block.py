#!/usr/bin/env python3
import sys

script_path = '/home/hermes/ibkr_gateway_python_curl/python/04_delay_get_option_twenteen_four.py'
with open(script_path, 'r') as f:
    content = f.read()

# Define the correct block for 1️⃣ Pull market snapshot
old_block = '''    # --------------------------------------------------------------
    # 1️⃣  Pull market snapshot - each field separately
    # --------------------------------------------------------------
    conids = [c.conid for c in top_contracts]

    FIELD_TO_ATTR = {
        "84": "bid", "85": "ask", "86": "delta", "87": "gamma", "88": "theta", "89": "vega", "100": "volume", "101": "open_interest", "104": "historical_volatility", "106": "implied_volatility"
    }

    merged: Dict[int, Dict[str, Any]] = {}
    for cid in conids:
        merged[cid] = {
            "conid": cid,
            "bid": None, "ask": None, "delta": None, "gamma": None, "theta": None, "vega": None, "volume": None, "open_interest": None, "historical_volatility": None, "implied_volatility": None
        }

    for field_id, attr_name in FIELD_TO_ATTR.items():
        logger.info(f"Fetching field {field_id} ({attr_name})...")
        for attempt in range(3):
            result = client._snapshot_raw(conids, field_id)
            if not result.ok:
                logger.warning(f"Attempt {attempt+1}/3: Field {field_id} failed: {result.error}")
                time.sleep(2)
                continue
            data = result.data
            if not isinstance(data, list): data = [data]
            for item in data:
                if isinstance(item, dict) and ('conid' in item and item['conid'] in merged):
                    value = item.get(field_id)
                    if value is not None and value != "":
                        merged[item['conid']][attr_name] = value
            filled = sum(1 for cid in conids if merged[cid][attr_name] is not None)
            if filled >= len(conids) * 0.8: break
            else: time.sleep(2)

    # Apply data to OptionContract objects
    for c in top_contracts:
        attrs = merged.get(c.conid)
        if attrs:
            for attr, value in attrs.items():
                if attr != "conid" and hasattr(c, attr): setattr(c, attr, value)

    # Re-correct Greeks
    for c in top_contracts: correct_put_greeks(c)

    # Write debug log and CSV
    write_debug_log(top_contracts, config.debug_log, logger)
    csv_result = write_csv(top_contracts, config.csv_output, logger)
    if not csv_result.ok: return 1

    logger.info("Script completed successfully.")
    return 0'''

new_block = '''
    # --------------------------------------------------------------
    # 1️⃣  Pull market snapshot – each field separately
    # --------------------------------------------------------------
    conids = [c.conid for c in top_contracts]

    # Define API field ID → OptionContract attribute name mapping
    FIELD_TO_ATTR = {
        "84": "bid", "85": "ask", "86": "delta", "87": "gamma", "88": "theta", "89": "vega", "100": "volume", "101": "open_interest", "104": "historical_volatility", "106": "implied_volatility"
    }

    # Initialize merged dict with all fields for each conid
    merged = {cid: {"conid": cid, "bid": None, "ask": None, "delta": None, "gamma": None, "theta": None, "vega": None, "volume": None, "open_interest": None, "historical_volatility": None, "implied_volatility": None} for cid in conids}

    # Fetch each field with retry logic
    for field_id, attr_name in FIELD_TO_ATTR.items():
        logger.info(f"Fetching field {field_id} ({attr_name})...")
        for attempt in range(3):
            result = client._snapshot_raw(conids, field_id)
            if not result.ok: logger.warning(f"Attempt {attempt+1}/3 failed: {result.error}"); time.sleep(2); continue
            data = result.data if isinstance(result.data, list) else [result.data]
            for item in data:
                if isinstance(item, dict) and ('conid' in item and item['conid'] in merged):
                    value = item.get(field_id)
                    if value is not None and value != "": merged[item['conid']][attr_name] = value
            filled = sum(1 for cid in conids if merged[cid][attr_name] is not None)
            if filled >= len(conids) * 0.8: break
            else: time.sleep(2)

    # Apply data to OptionContract objects
    for c in top_contracts:
        attrs = merged.get(c.conid)
        if attrs: for attr, value in attrs.items(): setattr(c, attr, value) if attr != "conid"

    # Re-correct Greeks
    for c in top_contracts: correct_put_greeks(c)

    # Write debug log and CSV
    write_debug_log(top_contracts, config.debug_log, logger)
    csv_result = write_csv(top_contracts, config.csv_output, logger)
    if not csv_result.ok: return 1

    logger.info("Script completed successfully.")
    return 0
'''

if old_block in content:
    new_content = content.replace(old_block, new_block)
    with open(script_path, 'w') as f: f.write(new_content)
    print("Block replacement successful.")
else:
    print("ERROR: Old block not found")
    sys.exit(1)