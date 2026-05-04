from ib_insync import *
ib = IB()
try:
    ib.connect('127.0.0.1', 4002, clientId=1, timeout=5)
    print("Verbindung erfolgreich!")
    ib.disconnect()
except Exception as e:
    print(f"Fehler: {e}")