#!/usr/bin/env python3
"""
Example: Using the crawl4ai container from Hermes Desktop 2.0
"""
import json
import requests
import sys

API_URL = "http://localhost:11235/crawl"
TOKEN = "testtoken123"

def crawl_page(url):
    payload = {
        "urls": [url],
        "crawler_params": {
            "headless": True,
            "verbose": False,
        },
        "extraction_params": {
            "extraction_strategy": "NoExtractionStrategy",  # just get raw HTML
        },
        "processor_params": {
            "processor": "BeautifulSoupProcessor",
            "params": {
                "extract_tags": ["title", "h1", "p"],
            }
        }
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {TOKEN}"  # Token as Bearer header
    }
    try:
        resp = requests.post(API_URL, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        print(f"Error calling crawl4ai: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    # Example URL: a simple test page
    target = "https://example.com"
    print(f"Crawling {target} via crawl4ai container...")
    result = crawl_page(target)
    print("\n=== Result (truncated) ===")
    # Pretty-print but limit size
    print(json.dumps(result, indent=2)[:2000])
    if len(json.dumps(result)) > 2000:
        print("... (output truncated)")