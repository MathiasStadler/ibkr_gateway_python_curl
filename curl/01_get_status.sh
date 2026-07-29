# FOUND HERE
# https://www.interactivebrokers.com/campus/ibkr-quant-news/tutorial-web-api-connect-to-brokerage-session/

curl -k -s "https://localhost:4002/v1/api/iserver/auth/status" \
-H "accept: application/json" \
-G 

https://localhost:4002/v1/api/iserver/secdef/search?symbol={symbol}

curl -k -s "https://localhost:4002/v1/api/iserver/secdef/search?symbol=PLTR" \
-H "accept: application/json" \
-G 



curl -k -s  "https://localhost:4002/v1/api/iserver/marketdata/snapshot?conids={'conid': 876603258, 'symbol': 'PLTR', 'strike': 138.0, 'maturityDate': '20260605', 'right': 'P', 'month': 'JUN26'}&fields=84,85,86,87,88,89&genericTickList=100,101,104,106&snapshot=0" \
-H "accept: application/json" \
-G 



curl -k -s  "https://localhost:4002/v1/api/iserver/marketdata/snapshot?conids=879026310,884110698,880834549&fields=84,85,86,87,88,89&genericTickList=100,101,104,106&snapshot=0" \
-H "accept: application/json" \
-G | jq

curl -k -s  "" \
-H "accept: application/json" \
-G | jq


curl -k -s  "https://localhost:4002/v1/api/iserver/marketdata/snapshot?conids=833015832,833015904,833015986,833016066,833016164,833016257,833016331,833016414,833016496,833016591&fields=84,85,86,87,88,89&genericTickList=100,101,104,106&snapshot=0
" \
-H "accept: application/json" \
-G | jq

curl https://localhost:4002/v1/api/iserver/marketdata/snapshot?conids=833015832,833015904,833015986,833016066,833016164,833016257,833016331,833016414,833016496,833016591&fields=84,85,86,87,88,89&genericTickList=100,101,104,106&snapshot=0


curl -k -s "https://localhost:4002/v1/api/iserver/auth/status" \
-H "accept: application/json" \
-G 


curl -k -s "https://localhost:4002/v1/api/iserver/marketdata/snapshot?conids=833015832,833015904,833015986,833016066,833016164,833016257,833016331,833016414,833016496,833016591&fields=84,85,86,87,88,89&genericTickList=100,101,104,106&snapshot=0
" \
-H "accept: application/json" \
-G 



#########################

curl -k -s  "https://localhost:4002/v1/api/iserver/marketdata/snapshot?conids=879026310,884110698,880834549&fields=84,85,86,87,88,89&genericTickList=100,101,104,106&snapshot=0" \
-H "accept: application/json" \
-G | jq


########

curl -k -s  "https://localhost:4002/v1/api/iserver/marketdata/snapshot?conids=884110698,880834549&fields=84,85,86,87,88,89&genericTickList=100,101,104,106&snapshot=0" \
-H "accept: application/json" \
-G | jq

## funzt 
curl -k -s  "https://localhost:4002/v1/api/iserver/marketdata/snapshot?conids=879026310,884110698,880834549&fields=84,85,86,87,88,89&genericTickList=100,101,104,106&snapshot=0" -H "accept: application/json" -G | jq