import pandas as pd
import numpy as np
import pandas_ta as ta

def apply_trailing_logic(df, params):

    trades = []

    # ---------------------------
    # STATE VARIABLES
    # ---------------------------
    in_trade = False

    entry_price = None
    entry_time = None

    highest_price = None
    exit_target = None

    sl_trigger = None
    ltp_crossed_target = False

    # =========================
    # MAIN LOOP
    # =========================
    for i in range(len(df)):

        row = df.iloc[i]

        current_price = row["last_price"]
        current_time = row["last_trade_time"]

        # =========================
        # ENTRY LOGIC
        # =========================
        if not in_trade and row["predicted"] == "BUY":

            entry_price = current_price
            entry_time = current_time

            highest_price = entry_price

            exit_target = entry_price * (1 + params["target_pct"])

            # Initial Stop Loss
            sl_trigger = entry_price * (1 - params["initial_sl_pct"])

            ltp_crossed_target = False
            in_trade = True

            continue

        # =========================
        # TRADE MANAGEMENT
        # =========================
        if in_trade:

            ltp = current_price

            # ---------------------------
            # Update highest price
            # ---------------------------
            if ltp > highest_price:
                highest_price = ltp

            # ---------------------------
            # MODE 1: BEFORE TARGET
            # ---------------------------
            if not ltp_crossed_target and ltp < exit_target:

                new_trigger = highest_price * (1 - params["trail_sl_pct"])

                if new_trigger > sl_trigger:
                    sl_trigger = new_trigger

            # ---------------------------
            # MODE 2: AFTER TARGET
            # ---------------------------
            else:
                ltp_crossed_target = True

                new_trigger = ltp - params["tight_sl_offset"]

                if new_trigger > sl_trigger:
                    sl_trigger = new_trigger

            # ---------------------------
            # EXIT CONDITION
            # ---------------------------
            if ltp <= sl_trigger:
                quantity = 650

                exit_price = sl_trigger
                exit_time = current_time

                profit = exit_price - entry_price
                profit_pct = profit / entry_price

                GST_RATE = 0.18
                TRANSACTION_CHARGE = 0.00053
                STT_SELL = 0.0005
                STAMP_DUTY = 0.00003
                SEBI_CHARGES = 0.000001
                turnover_buy = quantity * entry_price
                turnover_sell = quantity * exit_price
                stt = turnover_sell * STT_SELL
                brokerage = 20 * 2
                transaction_charge = (turnover_buy + turnover_sell) * TRANSACTION_CHARGE
                stamp_duty = (turnover_buy) * STAMP_DUTY
                sebi_charge = (turnover_buy + turnover_sell) * SEBI_CHARGES
                gst = (brokerage + transaction_charge + sebi_charge) * GST_RATE
                total_charges = brokerage + stt + transaction_charge + stamp_duty + sebi_charge + gst
                pnl = turnover_sell - turnover_buy - total_charges


                trades.append({
                    "entry_time": entry_time,
                    "entry_price": entry_price,
                    "exit_time": exit_time,
                    "exit_price": exit_price,
                    "profit": profit,
                    "profit_pct": profit_pct,
                    "pnl": pnl,
                })


                # ---------------------------
                # RESET STATE
                # ---------------------------
                in_trade = False

                entry_price = None
                entry_time = None
                highest_price = None
                exit_target = None
                sl_trigger = None
                ltp_crossed_target = False

    return trades

def resample_fractional_minute(original_df, time_col, n=4, depth=True):
    df = original_df.copy()
    df[time_col] = pd.to_datetime(df[time_col])
    df = df.sort_values(time_col)

    # -----------------------------
    # Step 1: Minute-level OHLC
    # -----------------------------
    df["minute"] = df[time_col].dt.floor("min")

    agg_dict = dict(
        minute_open=("last_price", "first"),
        minute_high=("last_price", "max"),
        minute_low=("last_price", "min"),
        minute_close=("last_price", "last"),
    )

    agg_dict["depth_list"] = ("depth", list)
    agg_dict["ltp_list"] = ("last_price", list)

    minute_ohlc = (
        df.groupby("minute")
        .agg(**agg_dict)
        .reset_index()
    )

    # Candle type
    minute_ohlc["candle_type"] = np.where(
        minute_ohlc["minute_close"] > minute_ohlc["minute_open"],
        "BUY",
        "SELL"
    )

    # -----------------------------
    # Step 2: Fractional buckets
    # -----------------------------
    bucket_size = 60 // n

    df["sec"] = df[time_col].dt.second
    df["bucket"] = df["sec"] // bucket_size

    df["bucket_time"] = df["minute"] + pd.to_timedelta(
        df["bucket"] * bucket_size, unit="s"
    )

    # -----------------------------
    # Step 3: OHLC per bucket
    # -----------------------------
    out = df.groupby(["bucket_time", "minute"]).agg(
        open=("last_price", "first"),
        high=("last_price", "max"),
        low=("last_price", "min"),
        close=("last_price", "last"),
        volume_open=("volume_at_tick", "first"),
        volume_high=("volume_at_tick", "max"),
        volume_low=("volume_at_tick", "min"),
        volume_close=("volume_at_tick", "last")
    ).reset_index()

    # -----------------------------
    # Step 4: Attach candle type
    # -----------------------------
    merge_cols = [
        "minute",
        "candle_type",
        "minute_open",
        "minute_high",
        "minute_low",
        "minute_close"
    ]

    # -----------------------------
    # Step 5: Close to Ask or Bid
    # -----------------------------
    if depth:
        def get_best_bid_ask(depth_list):
            # adjust depending on your depth structure
            bids = []
            asks = []
            for d in depth_list:
                if isinstance(d, dict):
                    if "bid" in d and "ask" in d:
                        bids.append(d["bid"])
                        asks.append(d["ask"])
            return (
                np.nanmean(bids) if bids else np.nan,
                np.nanmean(asks) if asks else np.nan
            )

        # extract avg bid/ask per minute
        bid_ask = minute_ohlc["depth_list"].apply(get_best_bid_ask)
        minute_ohlc["avg_bid"] = [x[0] for x in bid_ask]
        minute_ohlc["avg_ask"] = [x[1] for x in bid_ask]

        # merge into output
        out = out.merge(
            minute_ohlc[["minute", "avg_bid", "avg_ask"]],
            on="minute",
            how="left"
        )

        # determine closeness
        out["close_to"] = np.where(
            abs(out["close"] - out["avg_ask"]) < abs(out["close"] - out["avg_bid"]),
            "Ask",
            "Bid"
        )

    if depth:
        merge_cols += ["depth_list", "ltp_list"]

    out = out.merge(
        minute_ohlc[merge_cols],
        on="minute",
        how="left"
    )

    return out

def flatten_depth(row):
    depth = row['depth']
    data = {}

    for i in range(5):
        if i < len(depth['buy']):
            data[f'bid_p{i+1}'] = depth['buy'][i]['price']
            data[f'bid_q{i+1}'] = depth['buy'][i]['quantity']
        else:
            data[f'bid_p{i+1}'] = None
            data[f'bid_q{i+1}'] = None

    for i in range(5):
        if i < len(depth['sell']):
            data[f'ask_p{i+1}'] = depth['sell'][i]['price']
            data[f'ask_q{i+1}'] = depth['sell'][i]['quantity']
        else:
            data[f'ask_p{i+1}'] = None
            data[f'ask_q{i+1}'] = None

    return pd.Series(data)

def getAggregatedVolume(tns, n):
    # 1. Map n to the required frequency strings
    freq_map = {
        1: '1min',
        2: '30s',
        3: '20s',
        4: '15s'
    }

    # Handle the "and so on" logic if n > 4 (e.g., n=5 -> 12s, n=6 -> 10s)
    # Frequency (seconds) = 60 / n
    freq = freq_map.get(n, f"{int(60/n)}s")

    # 2. Ensure timestamp is a datetime object (crucial for resampling)
    # We create a temporary copy to avoid modifying the original dataframe
    temp_df = tns.copy()
    temp_df['timestamp'] = pd.to_datetime(temp_df['timestamp'], dayfirst=True)

    # 3. Perform Resampling
    # 'label=left' ensures the timestamp is the START of the bucket
    agg_df = temp_df.resample(freq, on='timestamp').agg({
        'quantity': 'sum'
    }).reset_index()

    # 4. Format the bucket_timestamp back to your required string format
    agg_df.rename(columns={'timestamp': 'bucket_timestamp'}, inplace=True)
    agg_df['bucket_timestamp'] = agg_df['bucket_timestamp'].dt.strftime('%d-%m-%Y %H:%M:%S')

    return agg_df

# def resample_fractional_minute(original_df, time_col, n=4, depth=True):
#     df = original_df.copy()
#     df[time_col] = pd.to_datetime(df[time_col])
#     df = df.sort_values(time_col)
#
#     # -----------------------------
#     # Step 1: Minute-level OHLC
#     # -----------------------------
#     df["minute"] = df[time_col].dt.floor("min")
#     minute_ohlc = (
#         df.groupby("minute")
#         .agg(
#             minute_open=("last_price", "first"),
#             minute_high=("last_price", "max"),
#             minute_low=("last_price", "min"),
#             minute_close=("last_price", "last"),
#             depth_list=("depth", list),
#             ltp_list = ("last_price", list)
#         )
#         .reset_index()
#     )
#
#     # Candle type
#     minute_ohlc["candle_type"] = np.where(
#         minute_ohlc["minute_close"] > minute_ohlc["minute_open"],
#         "BUY",
#         "SELL"
#     )
#
#     # -----------------------------
#     # Step 2: Fractional buckets
#     # -----------------------------
#     bucket_size = 60 // n
#
#     df["sec"] = df[time_col].dt.second
#     df["bucket"] = df["sec"] // bucket_size
#
#     df["bucket_time"] = df["minute"] + pd.to_timedelta(
#         df["bucket"] * bucket_size, unit="s"
#     )
#
#     # -----------------------------
#     # Step 3: OHLC per bucket
#     # -----------------------------
#     out = df.groupby(["bucket_time", "minute"]).agg(
#         # Price aggregations
#         open=("last_price", "first"),
#         high=("last_price", "max"),
#         low=("last_price", "min"),
#         close=("last_price", "last"),
#
#         # Volume aggregations
#         volume_open=("volume_at_tick", "first"),
#         volume_high=("volume_at_tick", "max"),
#         volume_low=("volume_at_tick", "min"),
#         volume_close=("volume_at_tick", "last")
#     ).reset_index()
#
#     # -----------------------------
#     # Step 4: Attach candle type
#     # -----------------------------
#     out = out.merge(minute_ohlc[["minute", "candle_type", "minute_open", "minute_high", "minute_low", "minute_close", "depth_list", "ltp_list"]],
#                     on="minute",
#                     how="left")
#     return out

def tradingview_bb(close, length=20, mult=2.0):
    basis = close.rolling(length).mean()
    std = close.rolling(length).std(ddof=0)
    dev = mult * std
    upper = basis + dev
    lower = basis - dev
    return basis, upper, lower


def tradingview_roc(close, length=10):
    prev = close.shift(length)
    roc = 100 * (close / prev - 1)
    return roc


def compute_signals(df,
                    bb_length=20,
                    bb_mult=2.0,
                    dmi_length=14,
                    roc_length=10):

    df = df.copy()
    df = df.sort_values('date').reset_index(drop=True)

    # BB
    df['basis'], df['upper_bb'], df['lower_bb'] = tradingview_bb(
        df['close'], bb_length, bb_mult
    )

    # DMI
    dmi = ta.adx(df['high'], df['low'], df['close'], length=dmi_length)
    df['plus_di']  = dmi[[c for c in dmi.columns if "DMP" in c][0]]
    df['minus_di'] = dmi[[c for c in dmi.columns if "DMN" in c][0]]
    df['adx']      = dmi[[c for c in dmi.columns if "ADX" in c][0]]

    # ROC
    df['roc'] = tradingview_roc(df['close'], roc_length)

    return df

# def generate_signal(
#     df,
#     window: int,
#     price_pct_threshold = 0.001,
#     volume_threshold = 0,
#
#     use_price_pct_level=True,
#     use_price_trend=True,
#     use_volume=False
# ):
#
#     print("use_price_pct_level : ", use_price_pct_level)
#     print("use_price_trend : ", use_price_trend)
#     print("use_volume : ", use_volume)
#     # ---------------------------
#     # BASE CONDITIONS (level)
#     # ---------------------------
#
#
#     if use_price_pct_level:
#         cond_buy = df["price_pct"] > price_pct_threshold
#         cond_sell = df["price_pct"] < -price_pct_threshold
#         buy_threshold_streak = cond_buy.rolling(window).min() == 1
#         sell_threshold_streak = cond_sell.rolling(window).min() == 1
#     else:
#         buy_threshold_streak = pd.Series(True, index=df.index)
#         sell_threshold_streak = pd.Series(True, index=df.index)
#
#     # # ---------------------------
#     # # PRICE TREND (monotonic)
#     # # ---------------------------
#     # if use_price_trend:
#     #     price_diff = df["close"].diff()
#     #
#     #     cond_buy_trend = price_diff > 0
#     #     cond_sell_trend = price_diff < 0
#     #
#     #     buy_trend_streak = cond_buy_trend.rolling(window).min() == 1
#     #     sell_trend_streak = cond_sell_trend.rolling(window).min() == 1
#     # else:
#     #     buy_trend_streak = pd.Series(True, index=df.index)
#     #     sell_trend_streak = pd.Series(True, index=df.index)
#
#     # ---------------------------
#     # PRICE TREND (incremental)
#     # ---------------------------
#     if use_price_trend:
#         price_diff = df["close"].diff()
#
#         # cond_buy_trend = price_diff > 0
#         # cond_sell_trend = price_diff < 0
#         #
#         # buy_trend_streak = cond_buy_trend.rolling(window - 1).sum() == (window - 1)
#         # sell_trend_streak = cond_sell_trend.rolling(window - 1).sum() == (window - 1)
#
#         buy_trend_streak = price_diff.rolling(window).apply(
#             lambda x: (x > 0).all() and x.is_monotonic_increasing
#         )
#
#         sell_trend_streak = price_diff.rolling(window).apply(
#             lambda x: (x < 0).all() and x.is_monotonic_decreasing
#         )
#     else:
#         buy_trend_streak = pd.Series(True, index=df.index)
#         sell_trend_streak = pd.Series(True, index=df.index)
#
#     # ---------------------------
#     # VOLUME CONDITION
#     # ---------------------------
#     if use_volume:
#         # print("use_volume : ", use_volume)
#         # vol_diff = df["volume_participated"].diff()
#         # cond_vol = vol_diff >= 0
#         # vol_streak = cond_vol.rolling(window).min() == 1
#
#         cond_vol = df["volume_close"] > (df["volume_avg"] * 1.2)
#         vol_streak = cond_vol.rolling(window).min() == 1
#     else:
#         vol_streak = pd.Series(True, index=df.index)
#
#     # ---------------------------
#     # FINAL STREAKS
#     # ---------------------------
#     buy_streak = buy_threshold_streak & buy_trend_streak & vol_streak
#     sell_streak = sell_threshold_streak & sell_trend_streak & vol_streak
#
#     # ---------------------------
#     # SIGNAL
#     # ---------------------------
#     # df["predicted"] = np.where(
#     #     buy_streak, "BUY",
#     #     np.where(sell_streak, "SELL", None)
#     # )
#     conditions = [
#         buy_streak & ~sell_streak,
#         sell_streak & ~buy_streak
#     ]
#     choices = ["BUY", "SELL"]
#     df["predicted"] = np.select(conditions, choices, default=None)
#     return df


def generate_signal(
    df,
    window: int,
    price_pct_threshold = 0.001,
    volume_threshold = 0,

    use_price_pct_level=True,
    use_price_trend=True,
    use_volume=False
):

    print("use_price_pct_level : ", use_price_pct_level)
    print("use_price_trend : ", use_price_trend)
    print("use_volume : ", use_volume)
    # ---------------------------
    # BASE CONDITIONS (level)
    # ---------------------------


    if use_price_pct_level:
        cond_buy = df["price_pct"] > price_pct_threshold
        cond_sell = df["price_pct"] < -price_pct_threshold
        buy_threshold_streak = cond_buy.rolling(window).min() == 1
        sell_threshold_streak = cond_sell.rolling(window).min() == 1
    else:
        buy_threshold_streak = pd.Series(True, index=df.index)
        sell_threshold_streak = pd.Series(True, index=df.index)

    # # ---------------------------
    # # PRICE TREND (monotonic)
    # # ---------------------------
    # if use_price_trend:
    #     price_diff = df["close"].diff()
    #
    #     cond_buy_trend = price_diff > 0
    #     cond_sell_trend = price_diff < 0
    #
    #     buy_trend_streak = cond_buy_trend.rolling(window).min() == 1
    #     sell_trend_streak = cond_sell_trend.rolling(window).min() == 1
    # else:
    #     buy_trend_streak = pd.Series(True, index=df.index)
    #     sell_trend_streak = pd.Series(True, index=df.index)

    # ---------------------------
    # PRICE TREND (incremental)
    # ---------------------------
    if use_price_trend:
        price_diff = df["close"].diff()

        # cond_buy_trend = price_diff > 0
        # cond_sell_trend = price_diff < 0
        #
        # buy_trend_streak = cond_buy_trend.rolling(window - 1).sum() == (window - 1)
        # sell_trend_streak = cond_sell_trend.rolling(window - 1).sum() == (window - 1)

        buy_trend_streak = price_diff.rolling(window).apply(
            lambda x: (x > 0).all() and x.is_monotonic_increasing
        )

        sell_trend_streak = price_diff.rolling(window).apply(
            lambda x: (x < 0).all() and x.is_monotonic_decreasing
        )
    else:
        buy_trend_streak = pd.Series(True, index=df.index)
        sell_trend_streak = pd.Series(True, index=df.index)

    # ---------------------------
    # VOLUME CONDITION
    # ---------------------------
    if use_volume:
        # print("use_volume : ", use_volume)
        # vol_diff = df["volume_participated"].diff()
        # cond_vol = vol_diff >= 0
        # vol_streak = cond_vol.rolling(window).min() == 1

        cond_vol = df["volume_close"] > (df["volume_avg"] * 1.2)
        vol_streak = cond_vol.rolling(window).min() == 1
    else:
        vol_streak = pd.Series(True, index=df.index)

    # The updated condition
    # Make sure 'window' is defined somewhere above, e.g., window = 3

    # 1. Define the base conditions
    cond_4_buy = (df["open"] < df["close"]) & (df["open"] > df["open"].shift(1))
    cond_4_sell = (df["open"] > df["close"]) & (df["open"] < df["open"].shift(1))

    # 2. Check for the streak using .sum() == window
    # buy_cond_4_streak = cond_4_buy.rolling(window).sum() == window
    # sell_cond_4_streak = cond_4_sell.rolling(window).sum() == window

    buy_cond_4_streak = cond_4_buy.rolling(window).sum() == window
    sell_cond_4_streak = cond_4_sell.rolling(window).sum() == window


    # ---------------------------
    # FINAL STREAKS
    # ---------------------------
    buy_streak = buy_threshold_streak & buy_trend_streak & vol_streak & buy_cond_4_streak
    sell_streak = sell_threshold_streak & sell_trend_streak & vol_streak & sell_cond_4_streak

    # ---------------------------
    # SIGNAL
    # ---------------------------
    # df["predicted"] = np.where(
    #     buy_streak, "BUY",
    #     np.where(sell_streak, "SELL", None)
    # )
    conditions = [
        buy_streak & ~sell_streak,
        sell_streak & ~buy_streak
    ]
    choices = ["BUY", "SELL"]
    df["predicted"] = np.select(conditions, choices, default=None)
    return df