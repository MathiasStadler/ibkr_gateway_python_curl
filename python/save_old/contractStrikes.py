import requests
import json

# Disable SSL Warnings
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# reauthenticate
def contractStrikes():
    base_url = "https://localhost:4002/v1/api/"
    endpoint = "iserver/secdef/strikes"

    #org from example
    #conid = "conid=11004968"
    conid="conid=6608603"
    secType = "secType=OPT"
    # change month
    month = "month=JUL26"
    exchange = "exchange=NASDAQ"

    params = "&".join([conid, secType, month, exchange])
    request_url = "".join([base_url, endpoint, "?", params])

    strikes_req = requests.get(url=request_url, verify=False)
    strikes_json = json.dumps(strikes_req.json(), indent=2)

    print(strikes_req)
    print(strikes_json)

if __name__ == "__main__":
    contractStrikes()