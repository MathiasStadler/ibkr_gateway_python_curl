def get_option_snapshot_bulk(conids, fields="84,85,86,87,88,89", max_attempts=2, delay=3, batch_size=20):
    """
    Fetch delayed market data snapshot for a list of conids with batching and retry logic.
    """
    if not conids:
        return {}

    # Authenticate first
    authenticate_market_data()

    field_map = {
        "84": "bid",
        "85": "ask",
        "86": "delta",
        "87": "gamma",
        "88": "theta",
        "89": "vega"
    }

    all_data = {}
    total_batches = (len(conids) + batch_size - 1) // batch_size

    for i in range(0, len(conids), batch_size):
        batch = conids[i:i+batch_size]
        batch_num = i // batch_size + 1
        logging.info(f"Processing batch {batch_num}/{total_batches} ({len(batch)} contracts)")

        conid_str = ",".join(str(c) for c in batch)
        url = f'https://localhost:4002/v1/api/iserver/marketdata/snapshot?conids={conid_str}&fields={fields}&delay=1&snapshot=1'

        batch_data = {}
        for attempt in range(max_attempts):
            try:
                resp = requests.get(url=url, verify=False)
                resp.raise_for_status()
                data = resp.json()

                # Merge data for this batch
                for item in data:
                    conid = item.get("conid")
                    if not conid:
                        continue
                    if conid not in batch_data:
                        batch_data[conid] = {}
                    for f_id, f_name in field_map.items():
                        value = item.get(f_id)
                        if value is not None:
                            batch_data[conid][f_name] = value

                # Check completeness for this batch
                complete = sum(1 for conid in batch_data
                               if all(f in batch_data[conid] for f in field_map.values()))
                logging.info(f"Batch {batch_num}, attempt {attempt+1}: {complete}/{len(batch)} complete")
                if complete == len(batch):
                    break

                if attempt < max_attempts - 1:
                    time.sleep(delay * (attempt + 1))
            except Exception as e:
                logging.error(f"Batch {batch_num}, attempt {attempt+1} failed: {e}")
                if attempt < max_attempts - 1:
                    time.sleep(delay)
                else:
                    logging.warning(f"Batch {batch_num} failed after {max_attempts} attempts")

        # Convert batch_data to final format and merge into all_data
        for conid, quote in batch_data.items():
            formatted = {}
            for f_name in field_map.values():
                value = quote.get(f_name)
                if f_name in ["bid", "ask"]:
                    formatted[f_name] = str(value) if value is not None else ""
                else:
                    formatted[f_name] = value if value is not None else ""
            all_data[conid] = formatted

        # Pause between batches to avoid rate limiting
        if i + batch_size < len(conids):
            time.sleep(2)

    return all_data