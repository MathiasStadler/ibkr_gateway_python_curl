# FROM HERE
# https://www.interactivebrokers.com/campus/ibkr-quant-news/tutorial-web-api-connect-to-brokerage-session/

curl -k -s "https://localhost:4002/v1/api/iserver/accounts" \
-H "accept: application/json" \
-G