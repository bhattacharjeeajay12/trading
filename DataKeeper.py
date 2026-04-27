import logging
from typing import Dict, Any, Optional

from AggregatedData import AggregatedDataBlock

logger = logging.getLogger(__name__)


class DataKeeper:
    """
    Manages one AggregatedDataBlock per traded symbol.
    """

    def __init__(self, fraction: int):
        self.fraction = fraction
        self.DataAggDict: Dict[str, AggregatedDataBlock] = dict()
        logger.info(f"DataKeeper initialised | fraction={fraction} | bucket_size_sec={60 // fraction}")

    def _get_aggregator(self, symbol: str) -> Optional[AggregatedDataBlock]:
        return self.DataAggDict.get(symbol)

    def _add_aggregator(self, symbol: str) -> AggregatedDataBlock:
        agg = AggregatedDataBlock(self.fraction, symbol)
        self.DataAggDict[symbol] = agg
        logger.info(
            f"[symbol={symbol}] Aggregator created | active_aggregators={len(self.DataAggDict)}"
        )
        return agg

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
                        f"[symbol={symbol}] Aggregator removed | "
                        f"active_aggregators={len(self.DataAggDict)}"
                    )
                else:
                    logger.debug(f"[symbol={symbol}] DELETE requested but no aggregator existed")

            elif purpose == "ADD":
                if symbol not in self.DataAggDict:
                    self._add_aggregator(symbol)
                if tick_data is not None:
                    self.DataAggDict[symbol].run(tick_data)
                    logger.debug(
                        f"[symbol={symbol}] Tick added | "
                        f"df_rows={len(self.DataAggDict[symbol].df_aggregated)}"
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

    dk = DataKeeper(6)

    for idx, row in df.iterrows():
        tick = {"last_trade_time": row["last_trade_time"],
                "last_price": row["last_price"],
                "symbol": row["symbol"],
                }
        dk.run("ADD", tick, None)
    chk = 1
