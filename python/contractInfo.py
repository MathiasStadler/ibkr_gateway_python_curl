# FROM FOUND HERE
# https://www.interactivebrokers.com/campus/trading-lessons/contract-search/

import requests
import json

# Disable SSL Warnings
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# reauthenticate
def contractInfo():
    # base_url = "https://localhost:5000/v1/api/"
    base_url = "https://localhost:4002/v1/api/"
    endpoint = "iserver/secdef/info"

    #org from example conid="conid=11004968"
    conid="conid=6608603"
    
    secType = "secType=STK"
    # change month/year
    month = "month=JUL26"
    exchange = "exchange=SMART"
    strike = "strike=45"
    right = "right=C"

    params = "&".join([conid, secType, month, exchange, strike, right])
    request_url = "".join([base_url, endpoint, "?", params])

    contract_req = requests.get(url=request_url, verify=False)
    contract_json = json.dumps(contract_req.json(), indent=2)

    print(contract_req)
    print(contract_json)

if __name__ == "__main__":
    contractInfo()