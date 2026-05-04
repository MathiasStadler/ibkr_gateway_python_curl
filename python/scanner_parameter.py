# FROM HERE
# https://www.interactivebrokers.com/campus/ibkr-api-page/cpapi-v1/#scanner

import requests

# Disable SSL Warnings
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# reauthenticate
def scanner_params():
    # base_url = "https://localhost:5000/v1/api/"
    # change port for IB Gateway
    base_url = "https://localhost:4002/v1/api/"
    endpoint = "iserver/auth/status"
    
    auth_req = requests.get(url=base_url+endpoint,verify=False)
    print(auth_req)
    print(auth_req.text)

if __name__ == "__main__":
    scanner_params()