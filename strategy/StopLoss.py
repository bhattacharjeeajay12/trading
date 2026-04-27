class StopLoss:
    def __init__(self):
        self.buy_price = None
        self.stop_loss_pct = 0.98
        self.stop_loss_price = None
        self.sl_update_count = 0

    def place_stop_loss_order(self):
        # To place initial stop loss.
        pass

    def update_stop_loss_order(self):
        # For trailing
        pass

    def check_if_sell_order_execuetd(self):
        # To cofirm is the sell order is executed
        pass

    def place_market_order(self):
        # to buy instrument
        pass

    def cancel_order(self):
        # cancel SL order after maximum updates are reached
        pass

    def run(self, tick, buy_order_id):
        # todo: The stoploss logic is WIP.
        if not self.buy_price:
            buy_price = 200 # todo : given buy order_id determine the buy_price - hit Zerodha (use buy order id).
            self.buy_price = buy_price
        else:
            if self.buy_price:
                # Use tick to update
                self.stop_loss_price = self.buy_price * self.stop_loss_pct # todo: place stop loss order at self.initial_stop_loss_price
                highest_ltp = self.buy_price

                ltp = tick["last_price"]
                if ltp < stop_loss_price:
                    # exit the trade
                if ltp > highest_ltp:
                    self.stop_loss_price = ltp * self.stop_loss_pct
                    # todo: update stop loss order to self.initial_stop_loss_price
                    highest_ltp = ltp



