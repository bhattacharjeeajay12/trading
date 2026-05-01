# ===============================
# data_pipeline.py (FINAL VERSION)
# ===============================

import pandas as pd
import numpy as np


def prepare_data(
    df,
    seq_len=60,
    horizon_sec=30,
    threshold=0.03,
    use_sell_label=True,
    export_path=r"D:\Study\Programs\trading\Experiments\TCNN\data\processed_with_split.csv"
):

    # -------------------------------
    # SORT & TIME
    # -------------------------------
    df = df.sort_values('last_trade_time').reset_index(drop=True)
    df['last_trade_time'] = pd.to_datetime(df['last_trade_time'])

    # -------------------------------
    # FEATURES
    # -------------------------------

    # Time gap → market activity speed
    df['dt'] = df['last_trade_time'].diff().dt.total_seconds().fillna(0)

    # Price movement features
    df['price_return'] = df['last_price'].pct_change().fillna(0)
    df['price_velocity'] = df['last_price'].diff().fillna(0)

    # Trade + volume
    df['trade_size'] = df['last_traded_quantity']
    df['volume_change'] = df['volume_traded'].diff().fillna(0)

    # Order book reference
    df['mid_price'] = (df['bid_price_1'] + df['ask_price_1']) / 2
    df['spread'] = df['ask_price_1'] - df['bid_price_1']

    # Depth aggregation
    bid_sizes = [f'bid_size_{i}' for i in range(1, 6)]
    ask_sizes = [f'ask_size_{i}' for i in range(1, 6)]

    df['total_bid_size'] = df[bid_sizes].sum(axis=1)
    df['total_ask_size'] = df[ask_sizes].sum(axis=1)

    # Liquidity pressure
    df['orderbook_imbalance'] = (
        (df['total_bid_size'] - df['total_ask_size']) /
        (df['total_bid_size'] + df['total_ask_size'] + 1e-9)
    )

    df['l1_imbalance'] = (
        (df['bid_size_1'] - df['ask_size_1']) /
        (df['bid_size_1'] + df['ask_size_1'] + 1e-9)
    )

    # Trade location & aggression
    df['trade_to_mid'] = df['last_price'] - df['mid_price']
    df['is_buy'] = (df['last_price'] >= df['ask_price_1']).astype(int)
    df['is_sell'] = (df['last_price'] <= df['bid_price_1']).astype(int)

    # Activity
    df['tick_speed'] = 1 / (df['dt'] + 1e-6)
    df['activity_10'] = df['dt'].rolling(10).mean().fillna(0)

    # -------------------------------
    # TARGET LABELING
    # -------------------------------
    times = df['last_trade_time'].values
    prices = df['last_price'].values

    future_price = np.full(len(df), np.nan)

    j = 0
    for i in range(len(df)):
        target_time = times[i] + np.timedelta64(horizon_sec, 's')

        while j < len(df) and times[j] < target_time:
            j += 1

        if j < len(df):
            future_price[i] = prices[j]

    df['future_price'] = future_price

    # Future return
    df['future_return'] = (df['future_price'] - df['last_price']) / df['last_price']

    # Label function
    def label_fn(x):
        """
            1 is BUY
            0 is OTHER
            -1 is SELL
        """

        if x > threshold:
            return 1
        elif x < -threshold:
            return -1 if use_sell_label else 0
        else:
            return 0

    df['target'] = df['future_return'].apply(label_fn)

    # Drop rows without future label
    df = df.dropna(subset=['future_price']).reset_index(drop=True)

    print("Target distribution:")
    print(df['target'].value_counts())

    # -------------------------------
    # FEATURES LIST
    # -------------------------------
    features = [
        'price_return','price_velocity','trade_size','volume_change',
        'spread','mid_price','orderbook_imbalance','l1_imbalance',
        'trade_to_mid','is_buy','is_sell','tick_speed','activity_10'
    ]

    data = df[features].values

    # -------------------------------
    # TARGET ENCODING
    # -------------------------------
    if use_sell_label:
        targets = df['target'].values + 1   # [-1,0,1] → [0,1,2]
        num_classes = 3
    else:
        targets = (df['target'] == 1).astype(int)  # binary
        num_classes = 2

    # -------------------------------
    # SEQUENCES
    # -------------------------------
    X, y, seq_indices = [], [], []

    for i in range(seq_len, len(df)):
        X.append(data[i-seq_len:i])
        y.append(targets[i])
        seq_indices.append(i)

    X = np.array(X)
    y = np.array(y)
    seq_indices = np.array(seq_indices)

    # -------------------------------
    # SPLIT (TIME-BASED)
    # -------------------------------
    split = int(0.8 * len(X))

    train_idx = seq_indices[:split]
    test_idx = seq_indices[split:]

    # Initialize split column
    df['split'] = 'unused'

    # Mark train/test
    df.loc[train_idx, 'split'] = 'train'
    df.loc[test_idx, 'split'] = 'test'

    # -------------------------------
    # EXPORT DATAFRAME
    # -------------------------------
    print("\ntrain value counts:")
    print(df[df['split']=='train']['target'].value_counts())
    print("\ntest value counts:")
    print(df[df['split']=='test']['target'].value_counts())
    if export_path:
        df.to_csv(export_path, index=False)
        print(f"Data exported to: {export_path}")

    # -------------------------------
    # CREATE TRAIN/TEST ARRAYS
    # -------------------------------
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]

    # -------------------------------
    # NORMALIZATION
    # -------------------------------
    mean = X_train.mean(axis=(0,1))
    std = X_train.std(axis=(0,1)) + 1e-9

    X_train = (X_train - mean) / std
    X_test  = (X_test - mean) / std

    np.save("mean.npy", mean)
    np.save("std.npy", std)

    return X_train, X_test, y_train, y_test, df, num_classes


def prepare_inference_data(df, mean, std, seq_len=60):

    df = df.sort_values('last_trade_time').reset_index(drop=True)
    df['last_trade_time'] = pd.to_datetime(df['last_trade_time'])

    # --- SAME FEATURES ---
    df['dt'] = df['last_trade_time'].diff().dt.total_seconds().fillna(0)

    df['price_return'] = df['last_price'].pct_change().fillna(0)
    df['price_velocity'] = df['last_price'].diff().fillna(0)

    df['trade_size'] = df['last_traded_quantity']
    df['volume_change'] = df['volume_traded'].diff().fillna(0)

    df['mid_price'] = (df['bid_price_1'] + df['ask_price_1']) / 2
    df['spread'] = df['ask_price_1'] - df['bid_price_1']

    bid_sizes = [f'bid_size_{i}' for i in range(1, 6)]
    ask_sizes = [f'ask_size_{i}' for i in range(1, 6)]

    df['total_bid_size'] = df[bid_sizes].sum(axis=1)
    df['total_ask_size'] = df[ask_sizes].sum(axis=1)

    df['orderbook_imbalance'] = (
        (df['total_bid_size'] - df['total_ask_size']) /
        (df['total_bid_size'] + df['total_ask_size'] + 1e-9)
    )

    df['l1_imbalance'] = (
        (df['bid_size_1'] - df['ask_size_1']) /
        (df['bid_size_1'] + df['ask_size_1'] + 1e-9)
    )

    df['trade_to_mid'] = df['last_price'] - df['mid_price']
    df['is_buy'] = (df['last_price'] >= df['ask_price_1']).astype(int)
    df['is_sell'] = (df['last_price'] <= df['bid_price_1']).astype(int)

    df['tick_speed'] = 1 / (df['dt'] + 1e-6)
    df['activity_10'] = df['dt'].rolling(10).mean().fillna(0)

    # --- FEATURES ---
    features = [
        'price_return','price_velocity','trade_size','volume_change',
        'spread','mid_price','orderbook_imbalance','l1_imbalance',
        'trade_to_mid','is_buy','is_sell','tick_speed','activity_10'
    ]

    data = df[features].values

    # --- SEQUENCES ---
    X = []
    indices = []

    for i in range(seq_len, len(df)):
        X.append(data[i-seq_len:i])
        indices.append(i)

    X = np.array(X)

    # --- APPLY TRAIN SCALER ---
    X = (X - mean) / (std + 1e-9)

    return X, indices, df