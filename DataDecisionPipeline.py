import logging
import uuid
from typing import Dict, Any, Optional

from DataKeeper import DataKeeper
from strategy.MomentBasedStrategy import MomentBasedStrategy
from strategy.StopLoss import StopLoss

logger = logging.getLogger(__name__)


class DataDecisionPipeline:
    """
    Orchestrator that wires together DataKeeper (tick aggregation),
    Strategy (signal generation) and StopLoss (trade management).

    Per-symbol lifecycle:
        1. Tick is forwarded to DataKeeper, which appends to that symbol's
           AggregatedDataBlock.
        2. If the symbol has no open position, the strategy is run on the
           aggregated dataframe. A BUY signal opens a position and registers
           a StopLoss tracker for that symbol.
        3. While the position is open, every tick for that symbol is fed to
           its StopLoss instance. When StopLoss reports an exit, the tracker
           is cleared so the strategy may run again on subsequent ticks.
        4. When a symbol is unsubscribed (e.g. ATM moves), its aggregator and
           any open tracker are removed.
    """

    INDEX_SYMBOL = "NIFTY 50"

    def __init__(self, fraction: int):
        self.fraction = fraction
        self.data_keeper = DataKeeper(fraction)
        self.strategy = MomentBasedStrategy()

        self.active_positions: Dict[str, StopLoss] = {}
        self.order_ids: Dict[str, Optional[str]] = {}

        logger.info(
            f"DataDecisionPipeline initialised | fraction={fraction} | "
            f"strategy={self.strategy.name}"
        )

    def process_tick(self, tick: Dict[str, Any]) -> None:
        """Entry point for every tick coming from the streamer."""
        symbol = tick.get("symbol")
        try:
            if not symbol or symbol == self.INDEX_SYMBOL:
                return

            self.data_keeper.run(purpose="ADD", tick_data=tick, symbol=symbol)

            if symbol in self.active_positions:
                self._manage_open_position(symbol, tick)
            else:
                self._evaluate_strategy(symbol, tick)

        except Exception as e:
            logger.error(
                f"DataDecisionPipeline.process_tick failed for symbol={symbol}: {e}",
                exc_info=True,
            )

    def remove_symbol(self, symbol: str) -> None:
        """Drop a symbol's aggregator and any open-position tracker."""
        try:
            if not symbol:
                return
            self.data_keeper.run(purpose="DELETE", tick_data=None, symbol=symbol)
            # Surface the (rare but real) case where ATM moved out from under
            # an open trade, so a position is being abandoned without an exit.
            if symbol in self.active_positions:
                logger.warning(
                    f"[order_id={self.order_ids.get(symbol)} symbol={symbol}] "
                    f"Active trade dropped due to unsubscribe (ATM moved). "
                    f"No exit was placed."
                )
            self.active_positions.pop(symbol, None)
            self.order_ids.pop(symbol, None)
        except Exception as e:
            logger.error(f"DataDecisionPipeline.remove_symbol failed for {symbol}: {e}", exc_info=True)

    def _evaluate_strategy(self, symbol: str, tick: Dict[str, Any]) -> None:
        """Run the strategy on the symbol's aggregated dataframe."""
        agg = self.data_keeper.DataAggDict.get(symbol)
        if agg is None or agg.df_aggregated is None or agg.df_aggregated.empty:
            return

        try:
            self.strategy.run(agg.df_aggregated)
        except Exception as e:
            logger.error(f"Strategy execution failed for {symbol}: {e}", exc_info=True)
            return

        if self.strategy.signal == 1:
            self._open_position(symbol, tick)

    def _place_order(self)-> str:
        # TODO Zerodha: place buy market order via kite.place_order(...) and capture the real order id from the broker response.
        return str(uuid.uuid4())

    def _open_position(self, symbol: str, tick: Dict[str, Any]) -> None:
        """Place a buy order (placeholder) and start tracking stop-loss."""
        try:
            order_id = self._place_order()
            self.order_ids[symbol] = order_id
            self.active_positions[symbol] = StopLoss()
            logger.info(
                f"[order_id={order_id} symbol={symbol}] BUY ORDER PLACED | "
                f"signal=BUY | ltp={tick.get('last_price')} | "
                f"strategy={self.strategy.name}"
            )
        except Exception as e:
            logger.error(f"Failed to open position for symbol={symbol}: {e}", exc_info=True)

    def _manage_open_position(self, symbol: str, tick: Dict[str, Any]) -> None:
        """Forward tick to the symbol's StopLoss; clear tracker on exit."""
        sl = self.active_positions.get(symbol)
        if sl is None:
            return

        order_id = self.order_ids.get(symbol)
        try:
            status = sl.run(tick, order_id)
        except Exception as e:
            logger.error(
                f"[order_id={order_id} symbol={symbol}] StopLoss.run failed: {e}",
                exc_info=True,
            )
            return

        if status == "EXITED":
            logger.info(
                f"[order_id={order_id} symbol={symbol}] POSITION CLOSED | "
                f"symbol eligible for re-entry on next tick"
            )
            self.active_positions.pop(symbol, None)
            self.order_ids.pop(symbol, None)
