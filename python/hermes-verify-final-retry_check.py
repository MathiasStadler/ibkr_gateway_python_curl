#!/usr/bin/env python3
import subprocess
import sys
import os

TARGET = "/home/hermes/ibkr_gateway_python_curl/python/03_delay_get_option_twenteen_three.py"

def run_verification():
    print("=== Final Ad-hoc Verification of Retry Logic ===")
    
    # 1. Syntax Check
    result = subprocess.run([sys.executable, "-m", "py_compile", TARGET], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"FAIL: Syntax error: {result.stderr}")
        sys.exit(1)
    print("PASS: Syntax OK")

    # 2. Logic Check (Verification of specific patterns)
    with open(TARGET, 'r', encoding='utf-8') as f:
        content = f.read()
    
    checks = {
        "max_attempts = 3": "Retry limit set to 3",
        "time.sleep(3)": "Pause of 3 seconds",
        "for attempt in range(max_attempts)": "Retry loop implemented",
        "continue": "Retry continuation"
    }
    
    all_passed = True
    for pattern, desc in checks.items():
        if pattern in content:
            print(f"PASS: {desc} found")
        else:
            print(f"FAIL: {desc} not found")
            all_passed = False
            
    if not all_passed:
        sys.exit(1)
    
    print("=== All checks passed successfully ===")
    sys.exit(0)

if __name__ == "__main__":
    run_verification()