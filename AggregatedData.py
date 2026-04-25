import pandas as pd
from typing import Dict, Any


class AggregatedDataFraction:

    def __init__(self, fraction: int):
        # Initialize attributes
        self.symbol: str = ""
        self.tick_data: Dict[Any, Any] = None
        self.df_aggregated: pd.DataFrame = pd.DataFrame()
        self.fraction: int = fraction

    def run(self, tick_data: Dict[Any, Any]) -> pd.DataFrame:
        return df


if __name__ == "__main__":

    # Example usage:

    sample_tick_data = [{"tradable": true, "mode": "full", "instrument_token": 18499586, "last_price": 222.9,
                         "last_traded_quantity": 65, "average_traded_price": 188.87, "volume_traded": 214717620,
                         "total_buy_quantity": 3118375, "total_sell_quantity": 409305,
                         "ohlc": {"open": 130.0, "high": 276.0, "low": 79.75, "close": 119.6},
                         "change": 86.371237458194, "last_trade_time": "2026-04-24T15:07:13", "oi": 7441265,
                         "oi_day_high": 10454145, "oi_day_low": 6973265, "exchange_timestamp": null, "depth": {
            "buy": [{"quantity": 845, "price": 221.85, "orders": 3}, {"quantity": 390, "price": 221.8, "orders": 3},
                    {"quantity": 715, "price": 221.75, "orders": 5}, {"quantity": 845, "price": 221.7, "orders": 6},
                    {"quantity": 1170, "price": 221.65, "orders": 9}],
            "sell": [{"quantity": 195, "price": 222.3, "orders": 1}, {"quantity": 65, "price": 222.35, "orders": 1},
                     {"quantity": 260, "price": 222.4, "orders": 1}, {"quantity": 845, "price": 222.45, "orders": 6},
                     {"quantity": 715, "price": 222.5, "orders": 4}]}, "local_time": "2026-04-24 15:07:14.533",
                         "symbol": "NIFTY26APR24000PE", "option_CE_PE": "PE", "option_type": "atm_plus_2.0",
                         "strike": 24000.0}]

    agg = AggregatedDataFraction(6)

    for tick_data in sample_tick_data:
        agg.run(tick_data)

print("Class initialized successfully.")