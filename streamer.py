import json
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
from kiteconnect import KiteTicker
from typing import List
import time

# Configure logging at module level
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Rotating file handler (10MB per file, keep 5 backups)
file_handler = RotatingFileHandler(
    "ticks.log",
    maxBytes=10 * 1024 * 1024,  # 10MB
    backupCount=5
)
file_handler.setFormatter(
    logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
)

# Console handler
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(
    logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
)

logger.addHandler(file_handler)
logger.addHandler(console_handler)


class MarketStreamer:
    MAX_RECONNECT_ATTEMPTS = 5
    RECONNECT_DELAY = 5  # seconds

    def __init__(self):
        # Validate environment variables
        self.api_key = os.environ.get("KITE_API_KEY")
        if not self.api_key:
            raise ValueError("KITE_API_KEY environment variable not set")

        # Load tokens
        self.tokens = self.load_tokens()
        if not self.tokens:
            raise ValueError("No instrument tokens found in instruments.json")

        logger.info(f"Loaded {len(self.tokens)} instrument tokens")

        self.kws = None
        self.reconnect_attempts = 0

    def load_tokens(self) -> List[int]:
        """Load instrument tokens from JSON file"""
        try:
            with open("instruments.json") as f:
                data = json.load(f)

            tokens = data.get("tokens", [])

            # Validate tokens are integers
            if not all(isinstance(t, int) for t in tokens):
                raise ValueError("All tokens must be integers")

            return tokens

        except FileNotFoundError:
            logger.error("instruments.json file not found")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in instruments.json: {e}")
            raise
        except KeyError:
            logger.error("'tokens' key not found in instruments.json")
            raise

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
            with open("access_token.txt", "r") as f:
                token = f.read().strip()
                if token:
                    return token
        except FileNotFoundError:
            pass

        raise ValueError(
            "Access token not found. Set KITE_ACCESS_TOKEN env var "
            "or create access_token.txt file"
        )

    def on_connect(self, ws, response):
        """Callback when WebSocket connects"""
        logger.info(f"WebSocket connected: {response}")
        self.reconnect_attempts = 0  # Reset reconnect counter

        try:
            # Subscribe to instruments
            ws.subscribe(self.tokens)
            logger.info(f"Subscribed to {len(self.tokens)} instruments")

            # Set mode to FULL for complete tick data
            ws.set_mode(ws.MODE_FULL, self.tokens)
            logger.info("Set mode to FULL")

        except Exception as e:
            logger.error(f"Error in on_connect: {e}")

    def on_ticks(self, ws, ticks):
        """Callback when ticks are received"""
        try:
            for tick in ticks:
                # Log structured data
                logger.info(
                    f"Token: {tick['instrument_token']} | "
                    f"LTP: {tick.get('last_price', 'N/A')} | "
                    f"Volume: {tick.get('volume', 'N/A')}"
                )

                # You can also log full tick if needed
                # logger.debug(f"Full tick: {tick}")

        except Exception as e:
            logger.error(f"Error processing ticks: {e}")

    def on_close(self, ws, code, reason):
        """Callback when WebSocket closes"""
        logger.warning(f"WebSocket closed - Code: {code}, Reason: {reason}")

        # Attempt reconnection
        if self.reconnect_attempts < self.MAX_RECONNECT_ATTEMPTS:
            self.reconnect_attempts += 1
            logger.info(
                f"Attempting reconnection {self.reconnect_attempts}/"
                f"{self.MAX_RECONNECT_ATTEMPTS} in {self.RECONNECT_DELAY}s"
            )
            time.sleep(self.RECONNECT_DELAY)
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
            logger.error(f"Failed to start WebSocket: {e}")
            raise

    def stop(self):
        """Gracefully stop the WebSocket connection"""
        if self.kws:
            logger.info("Stopping WebSocket connection...")
            self.kws.close()


if __name__ == "__main__":
    try:
        streamer = MarketStreamer()
        streamer.start()

    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt. Shutting down...")
        if streamer:
            streamer.stop()
        sys.exit(0)

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)