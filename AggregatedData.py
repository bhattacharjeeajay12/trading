import logging
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class BaseAggregator:
    """
    Minimal interface every data aggregator must implement.

    A new aggregator type (e.g. TickWindowAggregator, VolumeProfileAggregator)
    only needs to:
        1. Subclass `BaseAggregator`.
        2. Accept `symbol` and any aggregator-specific params via __init__.
        3. Implement `run(tick) -> pd.DataFrame` that ingests one tick and
           returns the latest view of the aggregated dataframe.
        4. Register itself in `AGGREGATOR_REGISTRY` (bottom of this file) and
           add a matching entry to `aggregators_list` in strategy/config.py.
    """

    aggregator_name: str = "BaseAggregator"

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.df_aggregated: pd.DataFrame = pd.DataFrame()

    def run(self, tick: Dict[Any, Any]) -> pd.DataFrame:
        raise NotImplementedError


class AggregatedDataBlock(BaseAggregator):
    """
    Time-bucketed OHLC aggregator. bucket_size_sec = 60 // fraction.
    Registered in `AGGREGATOR_REGISTRY` as "OHLCAggregator".
    """

    aggregator_name: str = "OHLCAggregator"

    def __init__(self, symbol: str, fraction: int = 6):
        super().__init__(symbol=symbol)
        self.fraction: int = fraction
        self.bucket_size_sec: int = 60 // fraction

        # Internal state to track the "active" bucket
        self.current_bucket_start: Optional[datetime] = None
        self.current_data: Dict[str, Any] = {}

        logger.debug(
            f"[symbol={self.symbol}] {self.aggregator_name} created | "
            f"bucket_size_sec={self.bucket_size_sec}"
        )

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
            logger.warning(
                f"[symbol={self.symbol}] Tick skipped: invalid timestamp/price | "
                f"raw_time={raw_time!r} | last_price={last_price!r}"
            )
            return self.df_aggregated

        bucket_start = self._get_bucket_start(tick_time)

        if self.current_bucket_start is None or bucket_start > self.current_bucket_start:
            # New bucket: log the closure of the previous one (if any) so each candle's
            # final OHLC is visible in the log without grepping the dataframe.
            if self.current_bucket_start is not None and not self.df_aggregated.empty:
                prev = self.df_aggregated.iloc[-1]
                # logger.info(
                #     f"[symbol={self.symbol}] Bucket CLOSED "
                #     f"{self.current_bucket_start.strftime('%H:%M:%S')} | "
                #     f"O={prev['open']} H={prev['high']} L={prev['low']} C={prev['close']}"
                # )

            self.current_bucket_start = bucket_start

            new_row = {
                "bucket_time": bucket_start.strftime("%H:%M:%S"),
                "open": last_price,
                "high": last_price,
                "low": last_price,
                "close": last_price,
            }
            temp_df = pd.DataFrame([new_row])
            self.df_aggregated = pd.concat([self.df_aggregated, temp_df], ignore_index=True)

            # logger.info(
            #     f"[symbol={self.symbol}] Bucket OPENED "
            #     f"{bucket_start.strftime('%H:%M:%S')} | open={last_price} | "
            #     f"df_rows={len(self.df_aggregated)}"
            # )
        else:
            idx = self.df_aggregated.index[-1]
            self.df_aggregated.at[idx, "high"] = max(self.df_aggregated.at[idx, "high"], last_price)
            self.df_aggregated.at[idx, "low"] = min(self.df_aggregated.at[idx, "low"], last_price)
            self.df_aggregated.at[idx, "close"] = last_price
            logger.debug(
                f"[symbol={self.symbol}] Bucket UPDATE "
                f"{self.current_bucket_start.strftime('%H:%M:%S')} | last_price={last_price}"
            )

        return self.df_aggregated


# ----------------------------------------------------------------------------
# Aggregator registry.
# Every aggregator listed in `aggregators_list` (strategy/config.py) must
# resolve to a concrete class here. The pipeline uses this dict to instantiate
# the aggregators required by deployed strategies.
# ----------------------------------------------------------------------------
AGGREGATOR_REGISTRY: Dict[str, type] = {
    "OHLCAggregator": AggregatedDataBlock,
}


if __name__ == "__main__":
    sample_tick_data = [
        {
            "last_price": 222.9,
            "last_trade_time": "2026-04-24T09:15:02",
            "symbol": "NIFTY26APR24000PE",
        },
        {
            "last_price": 223.5,
            "last_trade_time": "2026-04-24T09:15:08",
            "symbol": "NIFTY26APR24000PE",
        },
        {
            "last_price": 221.0,
            "last_trade_time": "2026-04-24T09:15:12",
            "symbol": "NIFTY26APR24000PE",
        },
    ]

    agg = AggregatedDataBlock(symbol="NIFTY26APR24000PE", fraction=6)
    for tick in sample_tick_data:
        result_df = agg.run(tick)
        print(result_df)