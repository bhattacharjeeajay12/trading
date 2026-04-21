import pandas as pd
import numpy as np

def resample_fractional_minute(df, time_col, n=4):
    df = df.copy()
    df[time_col] = pd.to_datetime(df[time_col])
    df = df.sort_values(time_col)

    # -----------------------------
    # Step 1: Minute-level OHLC
    # -----------------------------
    df["minute"] = df[time_col].dt.floor("min")

    minute_ohlc = df.groupby("minute")["last_price"].agg(
        minute_open="first",
        minute_high="max",
        minute_low="min",
        minute_close="last"
    ).reset_index()

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
    # df["bucket_time_next"] = df["bucket_time"].shift(-1)

    # -----------------------------
    # Step 3: OHLC per bucket
    # -----------------------------
    out = df.groupby(["bucket_time", "minute"])["last_price"].agg(
        open="first",
        high="max",
        low="min",
        close="last"
    ).reset_index()

    # -----------------------------
    # Step 4: Attach candle type
    # -----------------------------
    out = out.merge(minute_ohlc[["minute", "candle_type", "minute_open", "minute_high", "minute_low", "minute_close"]],
                    on="minute",
                    how="left")
    return out

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