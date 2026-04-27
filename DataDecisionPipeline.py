import logging
import uuid
from typing import Dict, Any, List, Optional, Tuple

from DataKeeper import DataKeeper
from strategy.StopLoss import StopLoss
from strategy.config import strategies_list, stoploss_dict
from strategy.registry import STRATEGY_REGISTRY

logger = logging.getLogger(__name__)


class DataDecisionPipeline:
    """
    Orchestrator that wires together DataKeeper (tick aggregation),
    Strategies (signal generation) and StopLoss (trade management).

    What runs is fully driven by `strategies_list` in strategy/config.py:
        - Strategies with `is_deployed=True` are instantiated.
        - Each strategy declares which aggregator it consumes; the union of
          those aggregators is what DataKeeper maintains per symbol.
        - On every tick, deployed strategies are evaluated in config-insertion
          order. The FIRST one returning signal==1 places the trade; the rest
          are skipped for that tick (single-buy-per-tick rule).

    Per-symbol lifecycle:
        1. Tick is forwarded to DataKeeper, which fans it out to every
           aggregator registered for that symbol.
        2. If the symbol has no open position, deployed strategies are run in
           order against their respective aggregator dataframes. A BUY signal
           opens a position and registers a StopLoss tracker.
        3. While the position is open, every tick for that symbol is fed to
           its StopLoss instance. When StopLoss reports an exit, the tracker
           is cleared so the strategies may run again on subsequent ticks.
        4. When a symbol is unsubscribed (e.g. ATM moves), its aggregators and
           any open tracker are removed.
    """

    INDEX_SYMBOL = "NIFTY 50"

    def __init__(self, fraction: int):
        # `fraction` is kept for backward compatibility with the streamer's
        # constructor call. Per-strategy aggregator params now live in
        # `strategies_list`; `fraction` is unused by this pipeline directly.
        self.fraction = fraction

        # 1. Instantiate deployed strategies in declared order.
        # 2. Capture each strategy's required aggregator (name + params) so we
        #    can build only what's needed.
        self.deployed_strategies: List[Tuple[Any, str]] = []  # (strategy_instance, aggregator_name)
        aggregator_specs: List[Tuple[str, Dict[str, Any]]] = []

        for cfg_name, cfg in strategies_list.items():
            if not cfg.get("is_deployed"):
                logger.info(f"Strategy disabled in config | name={cfg_name}")
                continue
            cls = STRATEGY_REGISTRY.get(cfg_name)
            if cls is None:
                logger.error(
                    f"Strategy '{cfg_name}' is_deployed=True but not in STRATEGY_REGISTRY; skipping"
                )
                continue
            agg_name = cfg.get("aggregator")
            agg_params = cfg.get("aggregator_params", {})
            if not agg_name:
                logger.error(f"Strategy '{cfg_name}' has no 'aggregator' declared; skipping")
                continue

            instance = cls()
            self.deployed_strategies.append((instance, agg_name))
            aggregator_specs.append((agg_name, agg_params))
            logger.info(
                f"Strategy deployed | name={cfg_name} | aggregator={agg_name} | "
                f"aggregator_params={agg_params}"
            )

        # 3. DataKeeper builds (deduplicated) aggregators per symbol.
        self.data_keeper = DataKeeper(aggregator_specs=aggregator_specs)

        self.active_positions: Dict[str, StopLoss] = {}
        self.order_ids: Dict[str, Optional[str]] = {}
        # Records which strategy fired the open trade -- handy in logs and for
        # future per-strategy stop-loss tuning.
        self.position_strategy: Dict[str, str] = {}

        logger.info(
            f"DataDecisionPipeline initialised | fraction={fraction} | "
            f"deployed_count={len(self.deployed_strategies)} | "
            f"strategies={[s.name for s, _ in self.deployed_strategies]}"
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
            self.position_strategy.pop(symbol, None)
        except Exception as e:
            logger.error(f"DataDecisionPipeline.remove_symbol failed for {symbol}: {e}", exc_info=True)

    def _evaluate_strategy(self, symbol: str, tick: Dict[str, Any]) -> None:
        """Run deployed strategies in declared order; first BUY signal wins."""
        for strategy, agg_name in self.deployed_strategies:
            agg = self.data_keeper.get_aggregator(symbol, agg_name)
            if agg is None or agg.df_aggregated is None or agg.df_aggregated.empty:
                continue

            try:
                strategy.run(agg.df_aggregated)
            except Exception as e:
                logger.error(
                    f"Strategy execution failed | strategy={strategy.name} | "
                    f"symbol={symbol} | {e}",
                    exc_info=True,
                )
                continue

            if strategy.signal == 1:
                self._open_position(symbol, tick, fired_by=strategy.name)
                # Single-buy-per-tick rule: do not consult further strategies.
                return

    def _place_order(self) -> str:
        # TODO Zerodha: place buy market order via kite.place_order(...) and capture the real order id from the broker response.
        return str(uuid.uuid4())

    def _open_position(self, symbol: str, tick: Dict[str, Any], fired_by: str) -> None:
        """Place a buy order (placeholder) and start tracking stop-loss."""
        try:
            order_id = self._place_order()
            self.order_ids[symbol] = order_id
            self.position_strategy[symbol] = fired_by
            self.active_positions[symbol] = StopLoss(
                stoploss_dict["stop_loss_pct"],
                stoploss_dict["new_stop_loss_pct"],
            )
            logger.info(
                f"[order_id={order_id} symbol={symbol}] BUY ORDER PLACED | "
                f"signal=BUY | ltp={tick.get('last_price')} | "
                f"strategy={fired_by}"
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
                f"strategy={self.position_strategy.get(symbol)} | "
                f"symbol eligible for re-entry on next tick"
            )
            self.active_positions.pop(symbol, None)
            self.order_ids.pop(symbol, None)
            self.position_strategy.pop(symbol, None)
