import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from strategy.config import strategy_list

class AggregatedDataBlock:

    def __init__(self, fraction: int, symbol):
        self.fraction: int = fraction
        self.symbol: str = symbol
        self.bucket_size_sec: int = 60 // fraction
        self.df_aggregated: pd.DataFrame = pd.DataFrame()
        self.max_df_size = strategy_list["MomentBasedStrategy"]["window"]

        # Internal state to track the "active" bucket
        self.current_bucket_start: Optional[datetime] = None
        self.current_data: Dict[str, Any] = {}

    @staticmethod
    def _coerce_datetime(value: Any) -> Optional[datetime]:
        """Accepts a datetime or an ISO/space-separated string and returns a datetime."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            s = value.strip()
            if not s:
                return None
            # Common shapes: "2026-04-24T09:15:02", "2026-04-24T09:15:02+05:30",
            # "2026-04-24 09:15:02.123" (from local_time formatter).
            try:
                return datetime.fromisoformat(s)
            except ValueError:
                pass
            for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    return datetime.strptime(s, fmt)
                except ValueError:
                    continue
        return None

    def _get_bucket_start(self, timestamp: datetime) -> datetime:
        """Calculates the start of the bucket for a given timestamp."""
        total_seconds = timestamp.minute * 60 + timestamp.second
        # Find how many seconds into the current hour we are
        # and floor it to the nearest bucket size
        bucket_seconds = (total_seconds // self.bucket_size_sec) * self.bucket_size_sec

        return timestamp.replace(minute=0, second=0, microsecond=0) + timedelta(seconds=bucket_seconds)

    def run(self, tick: Dict[Any, Any]) -> pd.DataFrame:
        # Parse the timestamp from the tick (using last_trade_time, falling back to local_time).
        # The streamer serialises datetimes to ISO strings before forwarding ticks, so we must
        # accept both `datetime` objects and ISO/space-separated strings here.
        raw_time = tick.get("last_trade_time") or tick.get("local_time")
        tick_time = self._coerce_datetime(raw_time)
        last_price = tick.get("last_price")

        if tick_time is None or last_price is None:
            # Cannot aggregate without a valid timestamp + price; skip silently.
            return self.df_aggregated

        bucket_start = self._get_bucket_start(tick_time)

        # If this is the first tick or a new bucket has started
        if self.current_bucket_start is None or bucket_start > self.current_bucket_start:
            # If there was a previous bucket being tracked, we "finalize" it?
            # In this implementation, we append to DF as soon as a bucket is closed or updated.
            # To match your requirement, we create/update the row for the current bucket.
            self.current_bucket_start = bucket_start

            new_row = {
                "bucket_time": bucket_start.strftime("%H:%M:%S"),
                "open": last_price,
                "high": last_price,
                "low": last_price,
                "close": last_price
            }

            # Create a new DataFrame entry
            temp_df = pd.DataFrame([new_row])
            self.df_aggregated = pd.concat([self.df_aggregated, temp_df], ignore_index=True)
        else:
            # Update the existing last row in the DataFrame
            idx = self.df_aggregated.index[-1]
            self.df_aggregated.at[idx, "high"] = max(self.df_aggregated.at[idx, "high"], last_price)
            self.df_aggregated.at[idx, "low"] = min(self.df_aggregated.at[idx, "low"], last_price)
            self.df_aggregated.at[idx, "close"] = last_price

        # Calculate Derived Columns for the active row
        idx = self.df_aggregated.index[-1]
        close_val = self.df_aggregated.at[idx, "close"]
        open_val = self.df_aggregated.at[idx, "open"]

        return self.df_aggregated


if __name__ == "__main__":
    # Note: Use 'True' instead of 'true' for Python boolean
    sample_tick_data = [
        {
            "last_price": 222.9,
            "last_trade_time": "2026-04-24T09:15:02",
            "symbol": "NIFTY26APR24000PE"
        },
        {
            "last_price": 223.5,
            "last_trade_time": "2026-04-24T09:15:08",
            "symbol": "NIFTY26APR24000PE"
        },
        {
            "last_price": 221.0,
            "last_trade_time": "2026-04-24T09:15:12",  # This should trigger a new bucket (10s later)
            "symbol": "NIFTY26APR24000PE"
        }
    ]



    for tick in sample_tick_data:
        agg = AggregatedDataBlock(6, tick["symbol"])  # 10 second buckets
        result_df = agg.run(tick)
        print(result_df)