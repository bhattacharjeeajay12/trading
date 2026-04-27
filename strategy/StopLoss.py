import logging
from typing import Any, Dict, Optional

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
        self.buy_price = self.get_buy_value(buy_order_id, tick=tick)
        self.highest_ltp = self.buy_price
        self.stop_loss_price = self.buy_price * self.stop_loss_pct
        self.place_stop_loss_order()
        logger.info(
            f"StopLoss initialised: buy_price={self.buy_price}, "
            f"stop_loss_price={self.stop_loss_price}, order_id={buy_order_id}"
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
                self.place_market_order()
                self.is_active = False
                logger.info(
                    f"StopLoss triggered: ltp={ltp} <= stop_loss_price={self.stop_loss_price}"
                )
                return self.STATUS_EXITED

            if ltp > self.highest_ltp:
                self.highest_ltp = ltp
                self.stop_loss_price = ltp * self.stop_loss_pct
                self.sl_update_count += 1
                # Use the above changed self.stop_loss_price to update_stop_loss_order
                self.update_stop_loss_order()
                logger.info(
                    f"StopLoss trailed: highest_ltp={self.highest_ltp}, "
                    f"new stop_loss_price={self.stop_loss_price}"
                )
                return self.STATUS_TRAILED

            return self.STATUS_HOLDING

        except Exception as e:
            logger.error(f"StopLoss.run error: {e}", exc_info=True)
            return self.STATUS_ERROR
