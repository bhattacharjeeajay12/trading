from strategy.Strategy import Strategy
import pandas as pd
from strategy.config import strategy_list
import logging

logger = logging.getLogger(__name__)


class MomentBasedStrategy(Strategy):
    def __init__(self):
        super().__init__()
        self.df = None
        self.name = "MomentBasedStrategy"
        self.min_candle_required = None
        self.price_pct_threshold = None
        self.signal = None
        logger.info(f"Strategy initialised | name={self.name}")

    def get_params(self):
        if self.name in strategy_list:
            self.min_candle_required = strategy_list[self.name]["window"]
            self.price_pct_threshold = strategy_list[self.name]["price_pct_threshold"]
        else:
            logger.error(f"Strategy params not found in config | name={self.name}")

    def process_data(self):
        self.df["price_pct"] = (self.df["close"] - self.df["open"]) / self.df["open"]
        self.df["price_chg"] = (self.df["close"] - self.df["open"])

    def get_signal(self):
        """Generate BUY signal when the last `min_candle_required` candles all
        have `price_pct > price_pct_threshold` (and > 0).
        """
        df_slice = self.df.tail(self.min_candle_required)
        cond_pct = (df_slice["price_pct"] > self.price_pct_threshold).all() and (df_slice["price_pct"] > 0).all()

        if cond_pct:
            self.signal = 1  # BUY
            last_pct = df_slice["price_pct"].iloc[-1]
            logger.info(
                f"BUY SIGNAL | strategy={self.name} | "
                f"window={self.min_candle_required} | "
                f"threshold={self.price_pct_threshold} | "
                f"last_price_pct={last_pct:.5f}"
            )
            return

        self.signal = 0
        logger.debug(
            f"No signal | strategy={self.name} | cond_pct={bool(cond_pct)} | "
            f"price_pct_tail={df_slice['price_pct'].round(5).tolist()}"
        )

    def run(self, df):
        try:
            self.df = df
            self.get_params()
            if self.min_candle_required is None:
                return 0
            if len(self.df) < self.min_candle_required:
                logger.debug(
                    f"Strategy skipped: insufficient candles | strategy={self.name} | "
                    f"have={len(self.df)} | need={self.min_candle_required}"
                )
                self.signal = 0
                return 0
            self.process_data()
            self.get_signal()
            return None
        except Exception as e:
            logger.error(f"Strategy.run failed | strategy={self.name} | {e}", exc_info=True)
            self.signal = 0
            return 0

if __name__ == "__main__":
    # test case 1:
    from pathlib import Path
    path_ = Path(r"D:\Study\Programs\trading\strategies_dev\clubbed_df_8_11.xlsx")
    df = pd.read_excel(path_)
    stg = MomentBasedStrategy()
    stg.run(df[["close", "open"]])
    chk=1

