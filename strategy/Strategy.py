import pandas as pd
class Strategy():
    def __init__(self, df: pd.DataFrame):
        self.name = None
        self.df = df

    def process_data(self):
        pass

    def place_stop_loss(self):
        pass

    def update_stop_loss(self):
        pass

    def is_sell_order_execuetd(self):
        pass

    def place_order(self):
        pass

    def cancel_order(self):
        pass

    def get_signal(self):
        pass

    def run(self):
        pass
