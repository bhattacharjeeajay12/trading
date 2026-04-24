import json
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
from dotenv import load_dotenv
from kiteconnect import KiteTicker, KiteConnect
from typing import List, Dict, Set
import time as time_module

load_dotenv()
from datetime import datetime, timezone, timedelta, time
from twisted.internet import reactor

# ============================================================================
# CONFIGURATION
# ============================================================================

NIFTY_INDEX_TOKEN = 256265
OPTION_DEPTH = 3
OPTION_EXPIRY = None
MARKET_END_TIME = "15:31"
# ============================================================================

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class ISTFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        ist_tz = timezone(timedelta(hours=5, minutes=30))
        dt = datetime.fromtimestamp(record.created, ist_tz)
        return dt.strftime('%Y-%m-%d %H:%M:%S,%f')[:-3]


ist = timezone(timedelta(hours=5, minutes=30))
log_date = datetime.now(ist).strftime('%d%b%Y').upper()
log_dir = os.path.join('assets', 'logs', log_date, 'ticks')
os.makedirs(log_dir, exist_ok=True)

file_handler = RotatingFileHandler(
    os.path.join(log_dir, f"{log_date}_ticks.log"),
    maxBytes=50 * 1024 * 1024,
    backupCount=50
)
file_handler.setFormatter(ISTFormatter("%(asctime)s - %(levelname)s - %(message)s"))

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(ISTFormatter("%(asctime)s - %(levelname)s - %(message)s"))


class NoTickDataFilter(logging.Filter):
    def filter(self, record):
        return not record.getMessage().startswith("tick_data:")


console_handler.addFilter(NoTickDataFilter())
logger.addHandler(file_handler)
logger.addHandler(console_handler)


class NiftyOptionStreamer:
    """
    Stream Nifty 50 Index and dynamically subscribe to relevant option contracts
    based on ATM (At The Money) position with configurable ITM/OTM depth
    """

    MAX_RECONNECT_ATTEMPTS = 5
    RECONNECT_DELAY = 5
    NIFTY_STRIKE_INTERVAL = 50

    def __init__(self, nifty_token: int, depth: int = 5, expiry: str = None):
        """
        Initialize the Nifty option streamer

        Args:
            nifty_token: Instrument token for Nifty 50 Index
            depth: Number of ITM and OTM strikes to subscribe on each side (default: 5)
            expiry: Expiry date in YYYY-MM-DD format (default: nearest expiry)
        """
        # Validate environment variables
        self.api_key = os.environ.get("KITE_API_KEY")
        if not self.api_key:
            raise ValueError("KITE_API_KEY environment variable not set")

        self.nifty_token = nifty_token
        self.depth = depth
        self.expiry = expiry

        # Track current state
        self.current_nifty_price = None
        self.current_atm_strike = None
        self.subscribed_tokens: Set[int] = set()
        self.option_chain: Dict[int, Dict] = {}

        # FIX: O(1) reverse lookup
        self.token_map: Dict[int, Dict] = {}

        self.kws = None
        self.kite = None
        self.reconnect_attempts = 0
        self.market_ended = False  # Flag to prevent reconnection after market end

        logger.info(f"Initialized NiftyOptionStreamer with depth={depth}, expiry={expiry}")

    def initialize_kite_client(self):
        """Initialize Kite Connect client for instrument lookup"""
        access_token = self.get_access_token()
        self.kite = KiteConnect(api_key=self.api_key)
        self.kite.set_access_token(access_token)
        logger.info("Kite Connect client initialized")

    def get_access_token(self) -> str:
        """
        Get access token - supports both env var and file
        Priority: Environment variable > access_token.txt file
        """
        # Try environment variable first
        token = os.environ.get("KITE_ACCESS_TOKEN")
        if token:
            return token

        with open(os.path.join("assets", "loginInfo", "access_token.txt"), "r") as f:
            return f.read().strip()

    def get_nearest_expiry(self, instruments):
        expiries = sorted({
            inst['expiry']
            for inst in instruments
            if inst['name'] == 'NIFTY' and inst['instrument_type'] in ['CE', 'PE']
        })
        return expiries[0]  # nearest expiry (weekly in most cases)

    def load_option_chain(self):
        instruments = self.kite.instruments("NFO")

        # ✅ Step 1: Auto-select nearest expiry
        if not self.expiry:
            expiries = sorted({
                inst['expiry']
                for inst in instruments
                if inst['name'] == 'NIFTY' and inst['instrument_type'] in ['CE', 'PE']
            })
            self.expiry = expiries[0]
            logger.info(f"Auto-selected expiry: {self.expiry}")

        # ✅ Step 2: Build option chain ONLY for selected expiry
        for inst in instruments:
            if inst['name'] != 'NIFTY':
                continue

            if inst['instrument_type'] not in ['CE', 'PE']:
                continue

            # 🔥 CRITICAL FIX: filter by expiry
            if inst['expiry'] != self.expiry:
                continue

            strike = inst['strike']

            if strike not in self.option_chain:
                self.option_chain[strike] = {}

            opt_type = inst['instrument_type']
            token = inst['instrument_token']
            symbol = inst['tradingsymbol']

            self.option_chain[strike][f'{opt_type}_token'] = token
            self.option_chain[strike][f'{opt_type}_symbol'] = symbol

            # reverse map
            self.token_map[token] = {
                "strike": strike,
                "type": opt_type,
                "symbol": symbol
            }

    # FIX: correct rounding
    def calculate_atm_strike(self, spot_price: float) -> int:
        return int(round(spot_price / self.NIFTY_STRIKE_INTERVAL) * self.NIFTY_STRIKE_INTERVAL)

    def get_tokens_to_subscribe(self, atm_strike: int) -> Set[int]:
        tokens = {self.nifty_token}

        for i in range(-self.depth, self.depth + 1):
            strike = atm_strike + (i * self.NIFTY_STRIKE_INTERVAL)

            if strike in self.option_chain:
                ce = self.option_chain[strike].get('CE_token')
                pe = self.option_chain[strike].get('PE_token')

                if ce:
                    tokens.add(ce)
                if pe:
                    tokens.add(pe)

        return tokens

    def get_symbol_from_token(self, token: int) -> str:
        if token == self.nifty_token:
            return "NIFTY 50"
        return self.token_map.get(token, {}).get("symbol", f"UNKNOWN_{token}")

    def build_window_snapshot(self, atm_strike: int):
        snapshot = {
            "time": datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3],
            "nifty": self.current_nifty_price,
            "atm": atm_strike
        }

        for i in range(-self.depth, self.depth + 1):
            strike = atm_strike + (i * self.NIFTY_STRIKE_INTERVAL)
            label = "ATM" if i == 0 else f"ATM{('+' if i > 0 else '')}{i}"

            if strike in self.option_chain:
                snapshot[f"{label}_CE"] = self.option_chain[strike].get("CE_symbol")
                snapshot[f"{label}_PE"] = self.option_chain[strike].get("PE_symbol")
            else:
                snapshot[f"{label}_CE"] = None
                snapshot[f"{label}_PE"] = None

        return snapshot

    def get_option_metadata(self, token: int, atm_strike: int) -> Dict:
        if token == self.nifty_token:
            return {
                "symbol": "NIFTY 50",
                "option_CE_PE": None,
                "option_type": "index",
                "strike": None
            }

        data = self.token_map.get(token)
        if not data:
            return {"symbol": f"UNKNOWN_{token}", "option_type": "unknown"}

        strike = data["strike"]

        if atm_strike is None:
            diff = 0
        else:
            diff = (strike - atm_strike) // self.NIFTY_STRIKE_INTERVAL

        if diff == 0:
            option_type = "atm"
        elif diff > 0:
            option_type = f"atm_plus_{diff}"
        else:
            option_type = f"atm_minus_{abs(diff)}"

        return {
            "symbol": data["symbol"],
            "option_CE_PE": data["type"],
            "option_type": option_type,
            "strike": strike
        }

    def update_subscriptions(self, new_atm_strike: int):
        old_atm = self.current_atm_strike
        nifty_price = self.current_nifty_price

        new_tokens = self.get_tokens_to_subscribe(new_atm_strike)

        to_unsub = self.subscribed_tokens - new_tokens
        to_sub = new_tokens - self.subscribed_tokens

        if old_atm is not None:
            logger.info(
                f"Nifty moved. Updating subscriptions. "
                f"Nifty={nifty_price}, Old ATM: {old_atm}, New ATM: {new_atm_strike}"
            )

        if to_unsub and self.kws:
            self.kws.unsubscribe(list(to_unsub))
            logger.info(f"Unsubscribed from {len(to_unsub)} tokens")

        if to_sub and self.kws:
            self.kws.subscribe(list(to_sub))
            self.kws.set_mode(self.kws.MODE_FULL, list(to_sub))
            logger.info(f"Subscribed to {len(to_sub)} new tokens")

        self.subscribed_tokens = new_tokens
        self.current_atm_strike = new_atm_strike

    def on_connect(self, ws, response):
        """Callback when WebSocket connects"""
        logger.info(f"WebSocket connected: {response}")
        self.reconnect_attempts = 0  # Reset reconnect counter

        try:
            # Initially subscribe only to Nifty index
            ws.subscribe([self.nifty_token])
            ws.set_mode(ws.MODE_FULL, [self.nifty_token])
            self.subscribed_tokens = {self.nifty_token}
            logger.info(f"Subscribed to Nifty 50 Index (Token: {self.nifty_token})")

        except Exception as e:
            logger.error(f"Error in on_connect: {e}")
    def on_ticks(self, ws, ticks):
        try:
            ist_tz = timezone(timedelta(hours=5, minutes=30))
            current_time_ist = datetime.now(ist_tz)
            local_time = current_time_ist.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]

            end_hour, end_minute = map(int, MARKET_END_TIME.split(':'))
            market_end_time_obj = time(end_hour, end_minute)

            if current_time_ist.time() >= market_end_time_obj:
                logger.info(f"Market end time ({MARKET_END_TIME} IST) reached. Stopping streamer...")
                self.market_ended = True
                self.stop()
                return

            for tick in ticks:
                if tick['instrument_token'] == self.nifty_token:
                    ltp = tick.get('last_price')
                    if ltp:
                        self.current_nifty_price = ltp
                        new_atm = self.calculate_atm_strike(ltp)

                        if self.current_atm_strike is None:
                            logger.info(f"Nifty LTP: {ltp}, Initial ATM Strike: {new_atm}")
                            self.update_subscriptions(new_atm)

                            snapshot = self.build_window_snapshot(new_atm)
                            logger.info(f"WINDOW_SNAPSHOT: {json.dumps(snapshot)}")

                        elif new_atm != self.current_atm_strike:
                            logger.info(f"ATM change: {self.current_atm_strike} -> {new_atm}")
                            self.update_subscriptions(new_atm)

                            snapshot = self.build_window_snapshot(new_atm)
                            logger.info(f"WINDOW_SNAPSHOT: {json.dumps(snapshot)}")

            atm_for_this_batch = self.current_atm_strike

            for tick in ticks:
                token = tick['instrument_token']

                def convert_datetime(obj):
                    if isinstance(obj, datetime):
                        return obj.isoformat()
                    elif isinstance(obj, dict):
                        return {k: convert_datetime(v) for k, v in obj.items()}
                    elif isinstance(obj, list):
                        return [convert_datetime(item) for item in obj]
                    return obj

                tick_serializable = convert_datetime(tick)

                tick_serializable['local_time'] = local_time
                tick_serializable['exchange_timestamp'] = tick.get('timestamp')
                tick_serializable['last_trade_time'] = tick.get('last_trade_time')

                metadata = self.get_option_metadata(token, atm_for_this_batch)
                tick_serializable.update(metadata)

                tick_serializable = convert_datetime(tick_serializable)

                logger.info(f"tick_data: {local_time} | {json.dumps(tick_serializable)}")

        except Exception as e:
            logger.error(f"Error processing ticks: {e}", exc_info=True)

    def on_close(self, ws, code, reason):
        logger.warning(f"WebSocket closed - Code: {code}, Reason: {reason}")

        if self.market_ended:
            if reactor.running:
                reactor.callFromThread(reactor.stop)
            return

        if self.reconnect_attempts < self.MAX_RECONNECT_ATTEMPTS:
            self.reconnect_attempts += 1
            self.kws = None  # ← REQUIRED FIX
            reactor.callLater(self.RECONNECT_DELAY, self.start)
        else:
            logger.error("Max reconnection attempts reached. Exiting.")

    def on_error(self, ws, code, reason):
        """Callback when WebSocket encounters an error"""
        logger.error(f"WebSocket error - Code: {code}, Reason: {reason}")

    def on_reconnect(self, ws, attempts_count):
        """Callback when WebSocket attempts to reconnect"""
        logger.info(f"Reconnecting... Attempt: {attempts_count}")

    def on_noreconnect(self, ws):
        """Callback when WebSocket gives up reconnecting"""
        logger.error("Reconnection failed. No more attempts.")

    def start(self):
        """Start the WebSocket connection"""
        try:
            # Initialize Kite client and load option chain
            if not self.kite:
                self.initialize_kite_client()
                self.load_option_chain()

            # Create new KiteTicker instance
            self.kws = KiteTicker(self.api_key, self.get_access_token())

            # Attach callbacks
            self.kws.on_connect = self.on_connect
            self.kws.on_ticks = self.on_ticks
            self.kws.on_close = self.on_close
            self.kws.on_error = self.on_error
            self.kws.on_reconnect = self.on_reconnect
            self.kws.on_noreconnect = self.on_noreconnect

            logger.info("Starting WebSocket connection...")
            self.kws.connect(threaded=False)  # Blocking call

        except Exception as e:
            logger.error(f"Failed to start WebSocket: {e}", exc_info=True)
            raise

    def stop(self):
        """Gracefully stop the WebSocket connection"""
        if self.kws:
            logger.info("Stopping WebSocket connection...")
            self.kws.close()


def main():
    try:
        logger.info("Starting Nifty Option Streamer...")
        streamer = NiftyOptionStreamer(NIFTY_INDEX_TOKEN, OPTION_DEPTH, OPTION_EXPIRY)
        streamer.start()
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()