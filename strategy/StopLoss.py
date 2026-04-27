import logging
from typing import Any, Dict, Optional
from config import strategy_list
logger = logging.getLogger(__name__)


class StopLoss:
    """
    Trailing stop-loss tracker for a single open position.

    State is persisted on the instance so that `run` can be called once per
    incoming tick. The first call initialises the tracker (using the broker
    fill price for the supplied buy order id); subsequent calls trail the
    stop upwards as the LTP makes new highs and exit when the LTP breaches
    the trailing stop.
    """

    STATUS_INITIALISED = "INITIALISED"
    STATUS_HOLDING = "HOLDING"
    STATUS_TRAILED = "TRAILED"
    STATUS_EXITED = "EXITED"
    STATUS_ERROR = "ERROR"

    def __init__(self, stop_loss_pct: float = 0.98):
        self.stop_loss_pct: float = stop_loss_pct
        self.buy_price: Optional[float] = None
        self.highest_ltp: Optional[float] = None
        self.stop_loss_price: Optional[float] = None
        self.is_active: bool = True
        self.sl_update_count: int = 0
        # Stashed during _initialise so every subsequent log line can be tagged.
        self.symbol: Optional[str] = None
        self.order_id: Optional[str] = None
        self.exit_target_pct: float = 0.01
        self.strategy_name: Optional[str] = None

    def _tag(self) -> str:
        """Uniform prefix so logs from multiple concurrent trades stay greppable."""
        return f"[order_id={self.order_id} symbol={self.symbol}]"

    def place_stop_loss_order(self) -> None:
        # TODO Zerodha: place initial SL-M / SL-L order at self.stop_loss_price.
        pass

    def update_stop_loss_order(self) -> None:
        # TODO Zerodha: modify existing SL order to new self.stop_loss_price.

        pass

    def place_market_order(self) -> None:
        # TODO Zerodha: place market sell order to exit the position.
        pass

    def cancel_order(self) -> None:
        # TODO Zerodha: cancel the live SL order.
        pass

    def get_buy_value(self, buy_order_id: str, tick=None) -> float:
        # todo : Query zerodha to get the actual buy value for the order
        # For testing the buy_price is taken as ltp
        return tick["last_price"]

    def _initialise(self, buy_order_id: Optional[str], tick: Dict[str, Any] | None = None) -> str:
        """Look up fill price for the buy order and arm the initial SL."""
        # TODO Zerodha: fetch actual fill price from buy_order_id via kite.orders().
        # Hardcoded fallback so the pipeline keeps running without a broker.
        self.order_id = buy_order_id
        self.symbol = tick.get("symbol") if tick else None
        self.buy_price = self.get_buy_value(buy_order_id, tick=tick)
        self.highest_ltp = self.buy_price
        self.stop_loss_price = self.buy_price * self.stop_loss_pct
        self.place_stop_loss_order()
        logger.info(
            f"{self._tag()} StopLoss ARMED | buy_price={self.buy_price} | "
            f"initial_sl_price={self.stop_loss_price:.2f} | sl_pct={self.stop_loss_pct}"
        )
        return self.STATUS_INITIALISED

    def run(self, tick: Dict[str, Any], buy_order_id: Optional[str]) -> str:
        """Process one tick and return the resulting status."""
        try:
            if not self.is_active:
                return self.STATUS_EXITED

            if self.buy_price is None:
                return self._initialise(buy_order_id, tick)

            ltp = tick.get("last_price")
            if ltp is None:
                return self.STATUS_HOLDING

            if ltp <= self.stop_loss_price:
                self.place_market_order() # check if sell is executed
                self.is_active = False
                pnl = ltp - self.buy_price
                pnl_pct = (pnl / self.buy_price) * 100 if self.buy_price else 0.0
                logger.info(
                    f"{self._tag()} StopLoss EXIT | exit_ltp={ltp} | buy_price={self.buy_price} | "
                    f"sl_price={self.stop_loss_price:.2f} | pnl={pnl:+.2f} ({pnl_pct:+.2f}%) | "
                    f"sl_updates={self.sl_update_count}"
                )
                return self.STATUS_EXITED

            has_crossed_exit_target = False
            if ltp < self.exit_target_price and not has_crossed_exit_target:
                if ltp > self.highest_ltp:
                    old_sl = self.stop_loss_price
                    self.highest_ltp = ltp
                    self.stop_loss_price = ltp * self.stop_loss_pct
                    self.sl_update_count += 1
                    self.update_stop_loss_order()
                    logger.info(
                        f"{self._tag()} StopLoss TRAIL #{self.sl_update_count} | ltp={ltp} | "
                        f"highest_ltp={self.highest_ltp} | "
                        f"sl_price {old_sl:.2f} -> {self.stop_loss_price:.2f}"
                    )
                    return self.STATUS_TRAILED
            else:
                # ltp has crossed exit target
                if ltp > self.highest_ltp:
                    has_crossed_exit_target = True
                    old_sl = self.stop_loss_price
                    self.highest_ltp = ltp
                    self.stop_loss_price = ltp * self.new_stop_loss_pct
                    self.sl_update_count += 1
                    self.update_stop_loss_order()
                    logger.info(
                        f"{self._tag()} StopLoss TRAIL after exit target #{self.sl_update_count} | ltp={ltp} | "
                        f"highest_ltp={self.highest_ltp} | "
                        f"sl_price {old_sl:.2f} -> {self.stop_loss_price:.2f}"
                    )
                    return self.STATUS_TRAILED


            return self.STATUS_HOLDING

        except Exception as e:
            logger.error(f"{self._tag()} StopLoss.run error: {e}", exc_info=True)
            return self.STATUS_ERROR
