import logging
from typing import Dict, Any, Optional, List, Tuple

from AggregatedData import AGGREGATOR_REGISTRY, BaseAggregator

logger = logging.getLogger(__name__)


class DataKeeper:
    """
    Manages per-symbol data aggregators.

    The set of aggregators maintained per symbol is driven by `aggregator_specs`
    -- a list of (aggregator_name, params) pairs supplied by the pipeline,
    which in turn derives it from the union of aggregators required by all
    deployed strategies in `strategies_list`.

    Storage shape:
        DataAggDict[symbol][aggregator_name] -> BaseAggregator
    """

    def __init__(
        self,
        aggregator_specs: List[Tuple[str, Dict[str, Any]]],
    ):
        # De-duplicate specs: if two strategies request the same aggregator with
        # the same params, we only build one instance per symbol.
        seen = set()
        self.aggregator_specs: List[Tuple[str, Dict[str, Any]]] = []
        for name, params in aggregator_specs:
            key = (name, tuple(sorted((params or {}).items())))
            if key in seen:
                continue
            seen.add(key)
            self.aggregator_specs.append((name, dict(params or {})))

        self.DataAggDict: Dict[str, Dict[str, BaseAggregator]] = {}
        logger.info(
            f"DataKeeper initialised | aggregators_per_symbol="
            f"{[name for name, _ in self.aggregator_specs]}"
        )

    def get_aggregator(self, symbol: str, aggregator_name: str) -> Optional[BaseAggregator]:
        """Public accessor used by the pipeline to fetch a strategy's data view."""
        return self.DataAggDict.get(symbol, {}).get(aggregator_name)

    def _build_aggregators_for(self, symbol: str) -> Dict[str, BaseAggregator]:
        """Instantiate every configured aggregator for a newly seen symbol."""
        bucket: Dict[str, BaseAggregator] = {}
        for name, params in self.aggregator_specs:
            cls = AGGREGATOR_REGISTRY.get(name)
            if cls is None:
                logger.error(
                    f"[symbol={symbol}] Aggregator '{name}' not in AGGREGATOR_REGISTRY; skipping"
                )
                continue
            bucket[name] = cls(symbol=symbol, **params)
        return bucket

    def _add_symbol(self, symbol: str) -> Dict[str, BaseAggregator]:
        bucket = self._build_aggregators_for(symbol)
        self.DataAggDict[symbol] = bucket
        logger.info(
            f"[symbol={symbol}] Aggregators created | "
            f"types={list(bucket.keys())} | active_symbols={len(self.DataAggDict)}"
        )
        return bucket

    def run(self, purpose: str, tick_data: Dict[str, Any] | None, symbol: str | None) -> None:
        try:
            if tick_data is not None:
                symbol = tick_data.get("symbol")

            if not symbol:
                logger.warning(
                    f"DataKeeper.run dropped: missing symbol | purpose={purpose} | "
                    f"tick_keys={list(tick_data.keys()) if tick_data else None}"
                )
                return

            if purpose == "DELETE":
                if symbol in self.DataAggDict:
                    del self.DataAggDict[symbol]
                    logger.info(
                        f"[symbol={symbol}] Aggregators removed | "
                        f"active_symbols={len(self.DataAggDict)}"
                    )
                else:
                    logger.debug(f"[symbol={symbol}] DELETE requested but no aggregator existed")

            elif purpose == "ADD":
                if symbol not in self.DataAggDict:
                    self._add_symbol(symbol)
                if tick_data is not None:
                    # Fan the tick out to every aggregator registered for this symbol.
                    for agg in self.DataAggDict[symbol].values():
                        agg.run(tick_data)
                    logger.debug(
                        f"[symbol={symbol}] Tick fan-out complete | "
                        f"aggregators={list(self.DataAggDict[symbol].keys())}"
                    )
            else:
                logger.warning(f"DataKeeper.run unknown purpose='{purpose}' | symbol={symbol}")

        except Exception as e:
            logger.error(
                f"DataKeeper.run failed | purpose={purpose} | symbol={symbol} | {e}",
                exc_info=True,
            )


if __name__ == "__main__":
    date_ = "24APR2026"
    file_name = "NIFTY26APR24200CE.xlsx"
    from pathlib import Path
    import pandas as pd
    file_path = Path(fr"D:\Study\Programs\trading\assets\logs\{date_}\{file_name}")
    df = pd.read_excel(file_path)

    dk = DataKeeper(aggregator_specs=[("OHLCAggregator", {"fraction": 6})])

    for idx, row in df.iterrows():
        tick = {
            "last_trade_time": row["last_trade_time"],
            "last_price": row["last_price"],
            "symbol": row["symbol"],
        }
        dk.run("ADD", tick, None)
    chk = 1
