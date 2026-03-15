import requests
import pyotp
from urllib.parse import urlparse, parse_qs
from kiteconnect import KiteConnect
import os
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class KiteLogin:
    BASE_URL = "https://kite.zerodha.com/api"

    def __init__(self):
        # Validate all required env vars upfront
        required_vars = ["KITE_API_KEY", "KITE_API_SECRET", "KITE_USER_ID",
                         "KITE_PASSWORD", "KITE_TOTP_SECRET"]

        for var in required_vars:
            if not os.environ.get(var):
                raise ValueError(f"Missing required environment variable: {var}")

        self.api_key = os.environ["KITE_API_KEY"]
        self.api_secret = os.environ["KITE_API_SECRET"]
        self.user_id = os.environ["KITE_USER_ID"]
        self.password = os.environ["KITE_PASSWORD"]
        self.totp_secret = os.environ["KITE_TOTP_SECRET"]

        self.kite = KiteConnect(api_key=self.api_key)
        self.session = requests.Session()

    def generate_totp(self) -> str:
        return pyotp.TOTP(self.totp_secret).now()

    def login(self) -> str:
        """Perform login and return request_token"""
        try:
            # Step 1: open login page
            self.session.get(self.kite.login_url())

            # Step 2: send credentials
            login_resp = self.session.post(
                f"{self.BASE_URL}/login",
                data={"user_id": self.user_id, "password": self.password}
            )
            login_resp.raise_for_status()

            login_data = login_resp.json().get("data", {})
            request_id = login_data.get("request_id")

            if not request_id:
                raise ValueError("Login failed: no request_id received")

            # Step 3: TOTP authentication
            twofa_resp = self.session.post(
                f"{self.BASE_URL}/twofa",
                data={
                    "user_id": self.user_id,
                    "request_id": request_id,
                    "twofa_value": self.generate_totp(),
                    "twofa_type": "totp",
                    "skip_session": True
                }
            )
            twofa_resp.raise_for_status()

            # Step 4: fetch request_token
            resp = self.session.get(self.kite.login_url())
            parsed = urlparse(resp.url)

            tokens = parse_qs(parsed.query).get("request_token")
            if not tokens:
                raise ValueError("No request_token in redirect URL")

            return tokens[0]

        except requests.RequestException as e:
            logger.error(f"Network error during login: {e}")
            raise
        except (KeyError, ValueError) as e:
            logger.error(f"Login failed: {e}")
            raise

    def generate_access_token(self, request_token: str) -> str:
        """Exchange request_token for access_token"""
        data = self.kite.generate_session(request_token, api_secret=self.api_secret)
        access_token = data["access_token"]
        self.save_token(access_token)
        logger.info("Access token generated and saved")
        return access_token

    def save_token(self, access_token: str) -> None:
        # Consider using keyring library for production
        with open("access_token.txt", "w") as f:
            f.write(access_token)

    def run(self) -> None:
        request_token = self.login()
        logger.info("Login successful")

        access_token = self.generate_access_token(request_token)
        self.kite.set_access_token(access_token)


if __name__ == "__main__":
    try:
        klogin = KiteLogin()
        klogin.run()
    except Exception as e:
        logger.error(f"Authentication failed: {e}")
        exit(1)