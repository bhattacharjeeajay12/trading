from strategy.Strategy import Strategy
import pandas as pd
from strategy.config import strategy_list
import logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

class MomentBasedStrategy(Strategy):
    def __init__(self):
        # 1. Initialize the parent class first
        super().__init__()
        self.df = None
        self.name = "MomentBasedStrategy"
        self.min_candle_required = None
        self.price_pct_threshold = None
        self.signal = None

    def get_params(self):
        if self.name in strategy_list:
            self.min_candle_required = strategy_list[self.name]["window"]
            self.price_pct_threshold = strategy_list[self.name]["price_pct_threshold"]

    def process_data(self):
        self.df["price_pct"] = (self.df["close"] - self.df["open"]) / self.df["open"]
        self.df["price_chg"] = (self.df["close"] - self.df["open"])

    def get_signal(self):
        """generate signal if in DF the columns have price_pct > self.price_pct_threshold and
        price_chg is increasing monotonically
        """
        df_slice = self.df.tail(self.min_candle_required)
        cond_pct = (df_slice["price_pct"] > self.price_pct_threshold).all() and (df_slice["price_pct"] > 0).all()
        # cond_mono = df_slice["price_chg"].is_monotonic_increasing and (df_slice["price_chg"] > 0).all()
        if cond_pct:
            self.signal = 1 # BUY
            logger.info(f"Buy Signal detected for strategy: {self.name}")
            return
        self.signal = 0
        return

    def run(self, df):
        self.df = df
        self.get_params()
        if len(self.df) < self.min_candle_required:
            logger.info(f"The DF doesn't have enough candles to run strategy - {self.name}.")
            return 0
        self.process_data()
        self.get_signal()
        return None

if __name__ == "__main__":
    # test case 1:
    from pathlib import Path
    path_ = Path(r"D:\Study\Programs\trading\strategies_dev\clubbed_df_8_11.xlsx")
    df = pd.read_excel(path_)
    stg = MomentBasedStrategy()
    stg.run(df[["close", "open"]])
    chk=1

