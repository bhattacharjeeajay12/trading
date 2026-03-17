"""
Kite Login Automation using Selenium
Automates Zerodha Kite login and generates access token
"""

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from urllib.parse import urlparse, parse_qs
from kiteconnect import KiteConnect
import pyotp
import os
import logging
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class KiteLogin:
    """Automates Kite login and access token generation"""

    def __init__(self, output_dir: str = "assets/loginInfo"):
        """
        Initialize with environment variables

        Args:
            output_dir: Directory to save access token and error files
        """
        self._validate_env_vars()

        self.api_key = os.environ["KITE_API_KEY"]
        self.api_secret = os.environ["KITE_API_SECRET"]
        self.user_id = os.environ["KITE_USER_ID"]
        self.password = os.environ["KITE_PASSWORD"]
        self.totp_secret = os.environ["KITE_TOTP_SECRET"]

        self.kite = KiteConnect(api_key=self.api_key)

        # Setup output directory
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Output directory: {self.output_dir.absolute()}")

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

    def _setup_driver(self) -> webdriver.Chrome:
        """Setup and return Chrome WebDriver with options"""
        chrome_options = Options()
        chrome_options.add_argument('--headless')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_argument('--window-size=1920,1080')
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)

        return webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=chrome_options
        )

    def _find_totp_field(self, driver):
        """Find TOTP input field (handles both regular and External TOTP)"""
        time.sleep(2)

        # Try regular TOTP field
        try:
            field = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.ID, "totp"))
            )
            logger.info("Found regular TOTP field")
            return field
        except:
            pass

        # Try External TOTP field
        try:
            field = driver.find_element(By.CSS_SELECTOR, "input[type='number'][placeholder*='•']")
            logger.info("Found External TOTP field")
            return field
        except:
            pass

        # Last resort - any number input in 2FA form
        try:
            field = driver.find_element(By.CSS_SELECTOR, ".twofa-form input[type='number']")
            logger.info("Found TOTP field via form selector")
            return field
        except:
            raise ValueError("Could not find TOTP input field")

    def login(self) -> str:
        """
        Perform login using Selenium and return request_token

        Returns:
            str: Request token for session generation
        """
        driver = self._setup_driver()

        try:
            # Navigate to login page
            login_url = f"https://kite.zerodha.com/connect/login?api_key={self.api_key}&v=3"
            logger.info("Opening Kite login page...")
            driver.get(login_url)

            # Enter user ID
            logger.info("Entering credentials...")
            user_id_field = WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.ID, "userid"))
            )
            user_id_field.clear()
            user_id_field.send_keys(self.user_id)

            # Enter password
            password_field = driver.find_element(By.ID, "password")
            password_field.clear()
            password_field.send_keys(self.password)

            # Click login
            login_button = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
            login_button.click()

            # Find and fill TOTP field
            logger.info("Waiting for TOTP page...")
            totp_field = self._find_totp_field(driver)

            totp_value = self._generate_totp()
            logger.info("Entering TOTP...")
            totp_field.clear()
            totp_field.send_keys(totp_value)

            # Get current URL before submitting
            current_url_before = driver.current_url
            logger.info(f"URL before submit: {current_url_before}")

            # Press Enter to submit (don't click button, form auto-submits and redirects immediately)
            totp_field.send_keys(Keys.RETURN)

            # Wait for URL to change (redirect happens)
            logger.info("Waiting for authorization redirect...")
            max_wait = 30
            start_time = time.time()

            request_token = None
            while time.time() - start_time < max_wait:
                try:
                    current_url = driver.current_url

                    # Check if we have request_token in URL
                    if "request_token" in current_url:
                        parsed = urlparse(current_url)
                        tokens = parse_qs(parsed.query).get("request_token")
                        if tokens:
                            request_token = tokens[0]
                            logger.info(f"✓ Found request_token in URL")
                            break

                    time.sleep(0.3)
                except:
                    # URL might be changing, continue polling
                    time.sleep(0.3)
                    continue

            if not request_token:
                # One final check
                try:
                    current_url = driver.current_url
                    logger.info(f"Final URL check: {current_url}")
                    parsed = urlparse(current_url)
                    tokens = parse_qs(parsed.query).get("request_token")
                    if tokens:
                        request_token = tokens[0]
                except:
                    pass

            if not request_token:
                error_screenshot = self.output_dir / "login_error.png"
                driver.save_screenshot(str(error_screenshot))
                raise ValueError(f"No request_token found after {max_wait}s")

            logger.info("✓ Successfully obtained request_token")
            return request_token

        except Exception as e:
            logger.error(f"Login failed: {e}")
            try:
                error_screenshot = self.output_dir / "login_error.png"
                driver.save_screenshot(str(error_screenshot))
                logger.error(f"Current URL: {driver.current_url}")
                logger.error(f"Error screenshot saved to: {error_screenshot}")
            except:
                pass
            raise
        finally:
            driver.quit()

    def generate_access_token(self, request_token: str) -> str:
        """
        Exchange request_token for access_token

        Args:
            request_token: Request token from login

        Returns:
            str: Access token
        """
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
        logger.info(f"Access token saved to: {token_file}")

    def run(self) -> str:
        """
        Complete login flow - get request token and generate access token

        Returns:
            str: Access token
        """
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