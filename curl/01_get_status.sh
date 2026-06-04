# FOUND HERE
# https://www.interactivebrokers.com/campus/ibkr-quant-news/tutorial-web-api-connect-to-brokerage-session/

curl -k -s "https://localhost:4002/v1/api/iserver/auth/status" \
-H "accept: application/json" \
-G 

https://localhost:4002/v1/api/iserver/secdef/search?symbol={symbol}

curl -k -s "https://localhost:4002/v1/api/iserver/secdef/search?symbol=PLTR" \
-H "accept: application/json" \
-G 