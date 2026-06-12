# FROM HERE
# https://www.interactivebrokers.com/campus/ibkr-quant-news/tutorial-web-api-connect-to-brokerage-session/

curl -k -s "https://localhost:4002/v1/api/iserver/accounts" \
-H "accept: application/json" \
-G


https://localhost:4002/v1/api/iserver/marketdata/snapshot?conids=879026857,884162826,880840696,879026794,884162771,880840644,879026731,693950996,880840591,879028668&fields=84,85&genericTickList=100&snapshot=0


curl -k -s "https://localhost:4002/v1/api/iserver/marketdata/snapshot?conids=879026857,884162826,880840696,879026794,884162771,880840644,879026731,693950996,880840591,879028668&fields=84,85&genericTickList=100&snapshot=0
" \
-H "accept: application/json" \
-G

curl -k -s "https://localhost:4002/v1/api/iserver/marketdata/snapshot?conids=879026857,884162826&fields=84,85&genericTickList=100&snapshot=0" \
-H "accept: application/json" \
-G

[{"88":"",
"conidEx":"879026857",
"_updated":1781289653230,
"87_raw":605.0,
"server_id":"q9",
"6509":"Dd",
"conid":879026857,
"6119":"q9",
"84":"",
"85":"1,236",
"86":"0.14",
"6508":"&serviceID1=1142&serviceID2=1042&serviceID3=1432&serviceID4=775&serviceID5=215&serviceID6=216&serviceID7=1078&serviceID8=1474&serviceID9=1396&serviceID10=1473&serviceID11=1471&serviceID12=661&serviceID13=122&serviceID14=123&serviceID15=1049&serviceID16=1048&serviceID17=1047&serviceID18=1146&serviceID19=203&serviceID20=1046&serviceID21=204&serviceID22=205&serviceID23=206&serviceID24=108&serviceID25=109","87":"605"}]