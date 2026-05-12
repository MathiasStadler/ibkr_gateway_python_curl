# FROM HERE
# https://www.interactivebrokers.com/campus/ibkr-api-page/cpapi-v1/#scanner

# code example from here
# https://www.interactivebrokers.com/campus/trading-lessons/market-scanners/

import requests
import json

# Disable SSL Warnings
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def scanParams():
    # org
    # base_url = "https://localhost:5000/v1/api/"
    #for gateway
    base_url = "https://localhost:4002/v1/api/"
    endpoint = "iserver/scanner/params"

    params_req = requests.get(url=base_url+endpoint, verify=False)
    params_json = json.dumps(params_req.json(), indent=2)

    paramFiles = open("./scannerParams.xml", "w")
    
    for i in params_json:
        paramFiles.write(i)

    paramFiles.close()
    # params see generate file
    print(params_req.status_code)

if __name__ == "__main__":
    scanParams()