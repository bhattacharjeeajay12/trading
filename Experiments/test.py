import pandas as pd
import numpy as np


# ─────────────────────────────────────────────
#  CONFIGURATION — tune these to your instrument
# ─────────────────────────────────────────────
CONFIG = {
    "volume_spike_multiplier": 2.5,   # spike candle must be Nx the rolling avg
    "rolling_window":          20,    # candles used to compute rolling avg volume
    "body_ratio_min":          0.4,   # body / total_range — filters weak candles
    "close_position_min":      0.6,   # close must be in top X% of candle range
    "lookahead_candles":       2,     # how many candles forward to track outcome
}


# ─────────────────────────────────────────────
#  CORE ANALYSIS
# ─────────────────────────────────────────────
def analyze_volume_spikes(df: pd.DataFrame) -> pd.DataFrame:
    """
    Expects a DataFrame with columns:
        timestamp, open, high, low, close, volume

    Returns a DataFrame of detected spike signals with outcome tracking.
    """
    df = df.copy().reset_index(drop=True)

    # ── Step 1: Rolling average volume (excludes current candle)
    df["vol_rolling_avg"] = (
        df["volume"]
        .shift(1)
        .rolling(window=CONFIG["rolling_window"], min_periods=5)
        .mean()
    )
    df["vol_ratio"] = df["volume"] / df["vol_rolling_avg"]

    # ── Step 2: Candle anatomy
    df["total_range"]    = df["high"] - df["low"]
    df["body"]           = abs(df["close"] - df["open"])
    df["body_ratio"]     = np.where(df["total_range"] > 0, df["body"] / df["total_range"], 0)

    # Where in the candle did price close? 0 = bottom, 1 = top
    df["close_position"] = np.where(
        df["total_range"] > 0,
        (df["close"] - df["low"]) / df["total_range"],
        0.5
    )

    df["is_bullish"] = df["close"] > df["open"]

    # ── Step 3: Detect spike candles
    is_spike      = df["vol_ratio"]      >= CONFIG["volume_spike_multiplier"]
    is_strong     = df["body_ratio"]     >= CONFIG["body_ratio_min"]
    is_high_close = df["close_position"] >= CONFIG["close_position_min"]
    is_bullish    = df["is_bullish"]

    df["signal"] = is_spike & is_strong & is_high_close & is_bullish

    # ── Step 4: Track outcome for next N candles
    n = CONFIG["lookahead_candles"]
    for i in range(1, n + 1):
        df[f"next_{i}_close"]  = df["close"].shift(-i)
        df[f"next_{i}_return"] = ((df[f"next_{i}_close"] - df["close"]) / df["close"]) * 100

    # ── Step 5: Classify outcome
    if n >= 2:
        df["outcome"] = np.where(
            df["next_1_return"] > 0,
            np.where(df["next_2_return"] > df["next_1_return"], "STRONG_FOLLOW",
            np.where(df["next_2_return"] > 0,                   "WEAK_FOLLOW",
                                                                 "REVERSED_C2")),
            "FAILED"
        )
    else:
        df["outcome"] = np.where(df["next_1_return"] > 0, "FOLLOW", "FAILED")

    return df


# ─────────────────────────────────────────────
#  SIGNAL LOGGER
# ─────────────────────────────────────────────
def log_signals(df: pd.DataFrame):
    signals = df[df["signal"] == True].copy()

    if signals.empty:
        print("\n[INFO] No volume spike signals found in the dataset.\n")
        return

    n = CONFIG["lookahead_candles"]

    print("\n" + "═" * 72)
    print("  VOLUME SPIKE SIGNAL DETECTOR  |  Scalper Mode")
    print("═" * 72)
    print(f"  Dataset   : {len(df)} candles")
    print(f"  Signals   : {len(signals)} spikes detected")
    print(f"  Vol spike : >{CONFIG['volume_spike_multiplier']}x rolling avg ({CONFIG['rolling_window']}-candle window)")
    print(f"  Lookahead : {n} candles")
    print("═" * 72)

    for idx, row in signals.iterrows():
        ts = row.get("timestamp", idx)

        print(f"\n  ┌── SIGNAL @ {ts}  (index {idx})")
        print(f"  │   Price        : O={row['open']:.4f}  H={row['high']:.4f}"
              f"  L={row['low']:.4f}  C={row['close']:.4f}")
        print(f"  │   Volume       : {row['volume']:,.0f}"
              f"  ({row['vol_ratio']:.1f}x avg of {row['vol_rolling_avg']:,.0f})")
        print(f"  │   Body ratio   : {row['body_ratio']:.2f}"
              f"  (min: {CONFIG['body_ratio_min']})")
        print(f"  │   Close pos    : {row['close_position']:.2f}"
              f"  (0=bottom  1=top)")

        print(f"  │   ── Outcome ──────────────────────────────")
        for i in range(1, n + 1):
            ret = row.get(f"next_{i}_return", None)
            if pd.notna(ret):
                direction = "▲" if ret > 0 else "▼"
                print(f"  │   Candle +{i}     : {direction} {ret:+.4f}%")
            else:
                print(f"  │   Candle +{i}     : (no data — end of series)")

        outcome = row.get("outcome", "N/A")
        label = {
            "STRONG_FOLLOW": "✅ STRONG FOLLOW-THROUGH",
            "WEAK_FOLLOW":   "🟡 WEAK FOLLOW (faded C2)",
            "REVERSED_C2":   "⚠️  REVERSED ON CANDLE 2",
            "FAILED":        "❌ FAILED (C1 went down)",
            "FOLLOW":        "✅ FOLLOW-THROUGH",
        }.get(outcome, outcome)

        print(f"  └── {label}")

    # ── Summary stats
    print("\n" + "─" * 72)
    print("  OUTCOME SUMMARY")
    print("─" * 72)
    outcome_counts = signals["outcome"].value_counts()
    total = len(signals)
    for outcome, count in outcome_counts.items():
        pct = (count / total) * 100
        bar = "█" * int(pct / 5)
        print(f"  {outcome:<18} {count:>3}  ({pct:5.1f}%)  {bar}")

    winners = signals[signals["outcome"].isin(["STRONG_FOLLOW", "WEAK_FOLLOW", "FOLLOW"])]
    win_rate = len(winners) / total * 100 if total > 0 else 0
    avg_c1   = signals["next_1_return"].mean()

    print("─" * 72)
    print(f"  Win rate (C1 positive) : {win_rate:.1f}%")
    print(f"  Avg return C+1         : {avg_c1:+.4f}%")
    if n >= 2:
        avg_c2 = signals["next_2_return"].mean()
        print(f"  Avg return C+2         : {avg_c2:+.4f}%")
    print("═" * 72 + "\n")


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────
def run(df: pd.DataFrame):
    """
    Pass your DataFrame here.
    Required columns: timestamp, open, high, low, close, volume
    """
    result_df = analyze_volume_spikes(df)
    log_signals(result_df)
    return result_df


# ─────────────────────────────────────────────
#  QUICK TEST WITH SYNTHETIC DATA
# ─────────────────────────────────────────────
if __name__ == "__main__":
    np.random.seed(42)
    n = 200
    timestamps = pd.date_range("2024-01-01 09:15:00", periods=n, freq="10s")
    close      = 100 + np.cumsum(np.random.randn(n) * 0.02)
    open_      = close + np.random.randn(n) * 0.01
    high       = np.maximum(close, open_) + abs(np.random.randn(n) * 0.01)
    low        = np.minimum(close, open_) - abs(np.random.randn(n) * 0.01)
    volume     = np.random.randint(500, 2000, size=n).astype(float)

    # Inject a few artificial spikes
    for spike_idx in [40, 90, 130, 170]:
        volume[spike_idx] = volume[spike_idx - 5 : spike_idx].mean() * 4
        # Make it a strong bullish candle
        open_[spike_idx]  = low[spike_idx]  + 0.001
        close[spike_idx]  = high[spike_idx] - 0.001

    df_test = pd.DataFrame({
        "timestamp": timestamps,
        "open":      open_,
        "high":      high,
        "low":       low,
        "close":     close,
        "volume":    volume,
    })

    result = run(df_test)