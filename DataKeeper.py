from typing import List, Dict, Any, Optional
from AggregatedData import  AggregatedDataBlock

class DataKeeper:
    """
    Manages dataAggregators
    """
    def __init__(self, fraction: int):
        self.fraction = fraction
        self.DataAggDict: Dict[str, AggregatedDataBlock] = dict()

    def _get_aggregator(self, symbol: str) -> Optional[AggregatedDataBlock]:
        """Helper to find an aggregator by symbol."""
        if symbol in self.DataAggDict:
            return self.DataAggDict[symbol]
        return None

    def _add_aggregator(self, symbol: str):
        """Helper to add a new aggregator by symbol."""
        self.DataAggDict[symbol] = AggregatedDataBlock(6, symbol)
        return self.DataAggDict[symbol]

    def run(self, purpose: str, tick_data: Dict[str, Any] | None, symbol: str | None) -> None:
        if tick_data is not None:
            symbol = tick_data.get("symbol")

        if not symbol:
            return

        if purpose == "DELETE":
            # Identify using symbol and remove from list
            # agg_to_remove = self._get_aggregator(symbol)
            if symbol in self.DataAggDict:
                del self.DataAggDict[symbol]

        elif purpose == "ADD":
            # Identify using symbol. If not exists, create it.
            if symbol not in self.DataAggDict:
                # create new Aggregator
                self._add_aggregator(symbol)
            if tick_data is not None:
                self.DataAggDict[symbol].run(tick_data)
        else:
            pass

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
    # dk.run("ADD", tick_data_list[0], None)
    # dk.run("ADD", tick_data_list[1], None)
    # dk.run("DELETE", None, tick_data_list[1]["symbol"])
    # dk.run("ADD", tick_data_list[2], None)
    # dk.run("ADD", tick_data_list[3], None)
