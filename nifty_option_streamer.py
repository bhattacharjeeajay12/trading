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
# CONFIGURATION - Modify these parameters as needed
# ============================================================================

# Nifty 50 Index instrument token (Common: 256265)
NIFTY_INDEX_TOKEN = 256265

# Number of ITM and OTM strikes to subscribe on each side of ATM
# Example: depth=5 means ATM ± 5 strikes (11 strikes total, 22 options + 1 index)
OPTION_DEPTH = 2

# Option expiry date in YYYY-MM-DD format
# Set to None to automatically use the nearest expiry
OPTION_EXPIRY = None  # Example: "2024-03-28" or None

# Market end time (IST) - streamer will auto-stop at this time
MARKET_END_TIME = "11:25"  # Format: HH:MM e.g. 15:35; 3:35 PM IST (5 minutes after market close),

# ============================================================================

# Configure logging at module level
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Create date-based log directory structure and filename
# Example: assets/logs/19MAR2024/ticks/19MAR2024_ticks.log
# IST is UTC+5:30
ist = timezone(timedelta(hours=5, minutes=30))
log_date = datetime.now(ist).strftime('%d%b%Y').upper()
log_dir = os.path.join('assets', 'logs', log_date, 'ticks')
log_filename = os.path.join(log_dir, f"{log_date}_ticks.log")

# Create directory structure if it doesn't exist
os.makedirs(log_dir, exist_ok=True)

# Rotating file handler (10MB per file, keep 5 backups)
# When file reaches 10MB, it becomes 19MAR2024_ticks.log.1, then .2, etc.
file_handler = RotatingFileHandler(
    log_filename,
    maxBytes=10 * 1024 * 1024,  # 10MB
    backupCount=5
)
file_handler.setFormatter(
    logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
)

# Console handler - will only show non-tick messages
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(
    logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
)


# Filter to exclude tick_data messages from console
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
    RECONNECT_DELAY = 5  # seconds
    NIFTY_STRIKE_INTERVAL = 50  # Nifty options have 50 point strike intervals
    RESUBSCRIBE_THRESHOLD = 25  # Resubscribe when index moves 25 points from last ATM

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
        self.option_chain: Dict[int, Dict] = {}  # strike -> {CE: token, PE: token, CE_symbol, PE_symbol}

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

        # Fall back to file
        try:
            with open(os.path.join("assets", "loginInfo", "access_token.txt"), "r") as f:
                token = f.read().strip()
                if token:
                    return token
        except FileNotFoundError:
            pass

        raise ValueError(
            "Access token not found. Set KITE_ACCESS_TOKEN env var "
            "or create access_token.txt file"
        )

    def load_option_chain(self):
        """Load Nifty option chain from Kite Connect"""
        try:
            logger.info("Fetching instruments from Kite Connect...")
            instruments = self.kite.instruments("NFO")

            nifty_options = [
                inst for inst in instruments
                if inst['name'] == 'NIFTY' and inst['instrument_type'] in ['CE', 'PE']
            ]

            # Filter by expiry if specified
            if self.expiry:
                nifty_options = [
                    opt for opt in nifty_options
                    if opt['expiry'].strftime('%Y-%m-%d') == self.expiry
                ]
            else:
                # Get nearest expiry
                if nifty_options:
                    nearest_expiry = min(opt['expiry'] for opt in nifty_options)
                    nifty_options = [
                        opt for opt in nifty_options
                        if opt['expiry'] == nearest_expiry
                    ]
                    self.expiry = nearest_expiry.strftime('%Y-%m-%d')
                    logger.info(f"Using nearest expiry: {self.expiry}")

            # Build option chain dictionary
            for opt in nifty_options:
                strike = opt['strike']
                if strike not in self.option_chain:
                    self.option_chain[strike] = {}

                opt_type = opt['instrument_type']
                self.option_chain[strike][f'{opt_type}_token'] = opt['instrument_token']
                self.option_chain[strike][f'{opt_type}_symbol'] = opt['tradingsymbol']

            logger.info(f"Loaded option chain with {len(self.option_chain)} strikes for expiry {self.expiry}")

        except Exception as e:
            logger.error(f"Error loading option chain: {e}", exc_info=True)
            raise

    def calculate_atm_strike(self, spot_price: float) -> int:
        """Calculate ATM strike based on spot price"""
        return round(spot_price / self.NIFTY_STRIKE_INTERVAL) * self.NIFTY_STRIKE_INTERVAL

    def get_tokens_to_subscribe(self, atm_strike: int) -> Set[int]:
        """
        Get list of tokens to subscribe based on ATM strike and depth

        Returns set of tokens including Nifty index and option contracts
        """
        tokens = {self.nifty_token}  # Always include Nifty index

        # Subscribe to ATM and surrounding strikes
        for i in range(-self.depth, self.depth + 1):
            strike = atm_strike + (i * self.NIFTY_STRIKE_INTERVAL)

            if strike in self.option_chain:
                # Add CE token
                ce_token = self.option_chain[strike].get('CE_token')
                if ce_token:
                    tokens.add(ce_token)

                # Add PE token
                pe_token = self.option_chain[strike].get('PE_token')
                if pe_token:
                    tokens.add(pe_token)

        return tokens

    def update_subscriptions(self, new_atm_strike: int):
        """Update subscriptions based on new ATM strike"""
        new_tokens = self.get_tokens_to_subscribe(new_atm_strike)

        # Find tokens to unsubscribe and subscribe
        tokens_to_unsubscribe = self.subscribed_tokens - new_tokens
        tokens_to_subscribe = new_tokens - self.subscribed_tokens

        if tokens_to_unsubscribe:
            try:
                self.kws.unsubscribe(list(tokens_to_unsubscribe))
                logger.info(f"Unsubscribed from {len(tokens_to_unsubscribe)} tokens")

                # Log details of unsubscribed tokens
                for token in tokens_to_unsubscribe:
                    symbol = self.get_symbol_from_token(token)
                    logger.info(f"UNSUBSCRIBED: Token={token}, Symbol={symbol}")

            except Exception as e:
                logger.error(f"Error unsubscribing: {e}")

        if tokens_to_subscribe:
            try:
                self.kws.subscribe(list(tokens_to_subscribe))
                self.kws.set_mode(self.kws.MODE_FULL, list(tokens_to_subscribe))
                logger.info(f"Subscribed to {len(tokens_to_subscribe)} new tokens")

                # Log details of newly subscribed tokens
                for token in tokens_to_subscribe:
                    symbol = self.get_symbol_from_token(token)
                    logger.info(f"SUBSCRIBED: Token={token}, Symbol={symbol}")

            except Exception as e:
                logger.error(f"Error subscribing: {e}")

        self.subscribed_tokens = new_tokens
        self.current_atm_strike = new_atm_strike

        logger.info(
            f"Subscription update complete. ATM Strike: {new_atm_strike}, "
            f"Total subscribed: {len(self.subscribed_tokens)}"
        )

    def get_symbol_from_token(self, token: int) -> str:
        """Get trading symbol from token"""
        if token == self.nifty_token:
            return "NIFTY 50"

        for strike, options in self.option_chain.items():
            if options.get('CE_token') == token:
                return options.get('CE_symbol', f'CE_{strike}')
            if options.get('PE_token') == token:
                return options.get('PE_symbol', f'PE_{strike}')

        return f"UNKNOWN_{token}"

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
        """Callback when ticks are received"""
        try:
            # Check if market end time reached (using IST)
            ist_tz = timezone(timedelta(hours=5, minutes=30))
            current_time_ist = datetime.now(ist_tz)

            # Parse MARKET_END_TIME string to time object (e.g., "15:35" -> time(15, 35))
            end_hour, end_minute = map(int, MARKET_END_TIME.split(':'))
            market_end_time_obj = time(end_hour, end_minute)

            if current_time_ist.time() >= market_end_time_obj:
                logger.info(f"Market end time ({MARKET_END_TIME} IST) reached. Stopping streamer...")
                self.market_ended = True
                self.stop()
                return  # Exit the callback cleanly

            local_time = current_time_ist.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]

            for tick in ticks:
                token = tick['instrument_token']

                # Convert tick to JSON-serializable format by handling datetime recursively
                def convert_datetime(obj):
                    if isinstance(obj, datetime):
                        return obj.isoformat()
                    elif isinstance(obj, dict):
                        return {k: convert_datetime(v) for k, v in obj.items()}
                    elif isinstance(obj, list):
                        return [convert_datetime(item) for item in obj]
                    return obj

                tick_serializable = convert_datetime(tick)

                # Log every tick with prefix for easy parsing
                logger.info(f"tick_data: {local_time} | {json.dumps(tick_serializable)}")

                # Check if this is Nifty index tick
                if token == self.nifty_token:
                    ltp = tick.get('last_price')
                    if ltp:
                        self.current_nifty_price = ltp
                        new_atm_strike = self.calculate_atm_strike(ltp)

                        # Check if we need to update subscriptions
                        if self.current_atm_strike is None:
                            # First time - subscribe to option chain
                            logger.info(f"Nifty LTP: {ltp}, Initial ATM Strike: {new_atm_strike}")
                            self.update_subscriptions(new_atm_strike)

                        elif abs(ltp - self.current_atm_strike) >= self.RESUBSCRIBE_THRESHOLD:
                            # Price moved significantly - resubscribe
                            logger.info(
                                f"Nifty moved from {self.current_nifty_price} to {ltp}. "
                                f"Updating subscriptions. Old ATM: {self.current_atm_strike}, "
                                f"New ATM: {new_atm_strike}"
                            )
                            self.update_subscriptions(new_atm_strike)

        except Exception as e:
            logger.error(f"Error processing ticks: {e}", exc_info=True)

    def on_close(self, ws, code, reason):
        """Callback when WebSocket closes"""
        logger.warning(f"WebSocket closed - Code: {code}, Reason: {reason}")

        # Don't reconnect if market has ended
        if self.market_ended:
            logger.info("Market ended. Stopping reactor and exiting gracefully.")
            # Stop the Twisted reactor to unblock the main thread
            if reactor.running:
                reactor.callFromThread(reactor.stop)
            return

        # Attempt reconnection
        if self.reconnect_attempts < self.MAX_RECONNECT_ATTEMPTS:
            self.reconnect_attempts += 1
            logger.info(
                f"Attempting reconnection {self.reconnect_attempts}/"
                f"{self.MAX_RECONNECT_ATTEMPTS} in {self.RECONNECT_DELAY}s"
            )
            time_module.sleep(self.RECONNECT_DELAY)
            self.start()
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

            # Get fresh access token
            access_token = self.get_access_token()

            # Create new KiteTicker instance
            self.kws = KiteTicker(self.api_key, access_token)

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
    """Main entry point"""

    streamer = None

    try:
        logger.info(f"Starting Nifty Option Streamer...")
        logger.info(f"Configuration: Nifty Token={NIFTY_INDEX_TOKEN}, Depth={OPTION_DEPTH}, Expiry={OPTION_EXPIRY}")

        streamer = NiftyOptionStreamer(
            nifty_token=NIFTY_INDEX_TOKEN,
            depth=OPTION_DEPTH,
            expiry=OPTION_EXPIRY
        )
        streamer.start()

    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt. Shutting down...")
        if streamer:
            streamer.stop()
        sys.exit(0)

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()