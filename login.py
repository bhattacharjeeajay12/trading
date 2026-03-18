"""
Kite Login Automation - Simple requests-based approach
No Selenium or browser automation needed
"""

import requests
import pyotp
import json
from urllib.parse import urlparse, parse_qs
from kiteconnect import KiteConnect
import os
import logging
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class KiteLogin:
    """Simple Kite login using requests library"""

    def __init__(self, output_dir: str = "assets/loginInfo"):
        """Initialize with environment variables"""
        self._validate_env_vars()

        self.api_key = os.environ["KITE_API_KEY"]
        self.api_secret = os.environ["KITE_API_SECRET"]
        self.user_id = os.environ["KITE_USER_ID"]
        self.password = os.environ["KITE_PASSWORD"]
        self.totp_secret = os.environ["KITE_TOTP_SECRET"]

        self.kite = KiteConnect(api_key=self.api_key)
        self.session = requests.Session()

        # Setup output directory
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _validate_env_vars(self):
        """Validate all required environment variables exist"""
        required_vars = [
            "KITE_API_KEY",
            "KITE_API_SECRET",
            "KITE_USER_ID",
            "KITE_PASSWORD",
            "KITE_TOTP_SECRET"
        ]
        missing = [var for var in required_vars if not os.environ.get(var)]
        if missing:
            raise ValueError(f"Missing environment variables: {', '.join(missing)}")

    def _generate_totp(self) -> str:
        """Generate TOTP code from secret"""
        return pyotp.TOTP(self.totp_secret).now()

    def login(self) -> str:
        """Perform login and return request_token"""

        # Step 1: Login with credentials
        login_url = os.getenv("KITE_LOGIN_URL")
        response = self.session.post(
            login_url,
            data={'user_id': self.user_id, 'password': self.password}
        )
        response.raise_for_status()

        request_id = response.json()['data']['request_id']
        logger.info("✓ Login successful")

        # Step 2: TOTP verification
        twofa_url = os.getenv("KITE_2FA_URL")
        totp_value = self._generate_totp()

        response = self.session.post(
            twofa_url,
            data={
                'user_id': self.user_id,
                'request_id': request_id,
                'twofa_value': totp_value,
                'twofa_type': 'totp'
            }
        )
        response.raise_for_status()
        logger.info("✓ 2FA successful")

        # Step 3: Get request_token
        kite_url = self.kite.login_url()

        try:
            self.session.get(kite_url)
        except Exception as e:
            # Extract request_token from redirect exception
            e_msg = str(e)
            if 'request_token=' in e_msg:
                request_token = e_msg.split('request_token=')[1].split(' ')[0].split('&')[0]
                logger.info("✓ Request token obtained")
                return request_token
            raise ValueError(f"Failed to get request_token: {e_msg}")

    def generate_access_token(self, request_token: str) -> str:
        """Exchange request_token for access_token"""
        data = self.kite.generate_session(request_token, api_secret=self.api_secret)
        access_token = data["access_token"]
        self._save_token(access_token)
        logger.info("✓ Access token generated and saved")
        return access_token

    def _save_token(self, access_token: str) -> None:
        """Save access token to file"""
        token_file = self.output_dir / "access_token.txt"
        with open(token_file, "w") as f:
            f.write(access_token)

    def run(self) -> str:
        """Complete login flow"""
        request_token = self.login()
        access_token = self.generate_access_token(request_token)
        self.kite.set_access_token(access_token)
        logger.info("✅ Authentication complete!")
        return access_token


def main():
    """Main entry point"""
    try:
        kite_login = KiteLogin()
        access_token = kite_login.run()
        print(f"\n✅ Success!")
        print(f"Access token saved to: assets/loginInfo/access_token.txt")
        print(f"Token valid until: 6:00 AM tomorrow")
        return 0
    except Exception as e:
        logger.error(f"❌ Authentication failed: {e}")
        return 1


if __name__ == "__main__":
    exit(main())