import json
import logging
from kiteconnect import KiteConnect, KiteTicker
import os

# -------------------------
# CONFIG
# -------------------------

API_KEY = os.getenv('KITE_API_KEY')
ACCESS_TOKEN = os.getenv('KITE_ACCESS_TOKEN')

CONFIG_FILE = "instrument_config.json"

# -------------------------
# LOGGING SETUP
# -------------------------

logging.basicConfig(
    filename="ticks.log",
    level=logging.INFO,
    format="%(asctime)s %(message)s"
)

# -------------------------
# READ JSON CONFIG
# -------------------------

with open(CONFIG_FILE) as f:
    config = json.load(f)

instrument_names = config["instruments"]
exchange = config["exchange"]

# -------------------------
# INIT KITE CONNECT
# -------------------------

kite = KiteConnect(api_key=API_KEY)
kite.set_access_token(ACCESS_TOKEN)

# -------------------------
# FETCH INSTRUMENT MASTER
# -------------------------

all_instruments = kite.instruments(exchange)

token_map = {}

for inst in all_instruments:
    token_map[inst["tradingsymbol"]] = inst["instrument_token"]

# -------------------------
# BUILD TOKEN LIST
# -------------------------

tokens = []

for name in instrument_names:
    if name in token_map:
        tokens.append(token_map[name])
    else:
        print(f"Instrument not found: {name}")

print("Subscribing to tokens:", tokens)

# -------------------------
# INIT TICKER
# -------------------------

kws = KiteTicker(API_KEY, ACCESS_TOKEN)

# -------------------------
# CALLBACKS
# -------------------------

def on_connect(ws, response):
    print("Connected")

    ws.subscribe(tokens)

    ws.set_mode(ws.MODE_FULL, tokens)


def on_ticks(ws, ticks):

    for tick in ticks:
        logging.info(tick)


def on_close(ws, code, reason):
    print("Connection closed:", reason)


# -------------------------
# ASSIGN CALLBACKS
# -------------------------

kws.on_connect = on_connect
kws.on_ticks = on_ticks
kws.on_close = on_close

# -------------------------
# CONNECT
# -------------------------

kws.connect(threaded=False)