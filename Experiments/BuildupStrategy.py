import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ─────────────────────────────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────────────────────────────
CONFIG = {
    # --- Compression detection
    "compression_lookback":     16,    # candles to compute baseline range
    "compression_window":        5,    # candles that must be compressed
    "compression_threshold":    0.60,  # compressed candles range < X * baseline range

    # --- Accumulation detection (inside compression)
    "accum_vol_lookback":        3,    # candles to compare volume trend in compression
    "accum_vol_rise_ratio":     1.05,  # recent avg vol must be X * earlier avg vol
    "accum_buy_dominance":      0.55,  # green candle vol / total vol during compression

    # --- Breakout (trigger) candle
    "breakout_vol_multiplier":  2.0,   # breakout vol > X * rolling avg vol
    "breakout_range_expansion": 1.5,   # breakout range > X * avg compression range
    "breakout_close_position":  0.65,  # close must be in top X% of breakout candle
    "breakout_close_above_compression": True,  # close > highest close in compression

    # --- Rolling vol baseline
    "vol_rolling_window":       20,
}


# ─────────────────────────────────────────────────────────────────────
#  STATE MACHINE STATES
# ─────────────────────────────────────────────────────────────────────
class Phase(Enum):
    IDLE        = "IDLE"
    COMPRESSION = "COMPRESSION"
    ACCUMULATION= "ACCUMULATION"
    SIGNAL      = "SIGNAL"


@dataclass
class MarketState:
    phase:               Phase = Phase.IDLE
    compression_start:   int   = 0
    compression_candles: list  = field(default_factory=list)  # indices
    accumulation_confirmed: bool = False

    def reset(self):
        self.phase               = Phase.IDLE
        self.compression_start   = 0
        self.compression_candles = []
        self.accumulation_confirmed = False


# ─────────────────────────────────────────────────────────────────────
#  INDICATOR HELPERS
# ─────────────────────────────────────────────────────────────────────
def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy().reset_index(drop=True)

    df["range"]          = df["high"] - df["low"]
    df["body"]           = abs(df["close"] - df["open"])
    df["is_bullish"]     = df["close"] >= df["open"]
    df["close_position"] = np.where(
        df["range"] > 0,
        (df["close"] - df["low"]) / df["range"],
        0.5
    )

    # Rolling average volume (excludes current candle)
    df["vol_rolling_avg"] = (
        df["volume"].shift(1)
        .rolling(window=CONFIG["vol_rolling_window"], min_periods=5)
        .mean()
    )

    # Baseline range: rolling mean of candle ranges
    df["range_baseline"] = (
        df["range"].shift(1)
        .rolling(window=CONFIG["compression_lookback"], min_periods=8)
        .mean()
    )

    return df


# ─────────────────────────────────────────────────────────────────────
#  PHASE CHECKERS
# ─────────────────────────────────────────────────────────────────────
def is_compressed(row: pd.Series) -> bool:
    """Single candle compression check against its baseline."""
    if pd.isna(row["range_baseline"]) or row["range_baseline"] == 0:
        return False
    return row["range"] < CONFIG["compression_threshold"] * row["range_baseline"]


def check_accumulation(df: pd.DataFrame, compression_indices: list) -> dict:
    """
    During compression, check if:
      1. Volume is quietly rising (not flat or falling)
      2. Green candles are volume-dominant over red candles
    Returns dict with result and supporting stats.
    """
    if len(compression_indices) < CONFIG["accum_vol_lookback"] * 2:
        # Not enough candles yet — defer judgment, assume not confirmed
        return {"confirmed": False, "reason": "insufficient candles"}

    comp_df = df.loc[compression_indices].copy()

    # Split into earlier half and recent half
    mid = len(comp_df) // 2
    earlier = comp_df.iloc[:mid]
    recent  = comp_df.iloc[mid:]

    earlier_avg_vol = earlier["volume"].mean()
    recent_avg_vol  = recent["volume"].mean()

    vol_rising = (
        earlier_avg_vol > 0 and
        (recent_avg_vol / earlier_avg_vol) >= CONFIG["accum_vol_rise_ratio"]
    )

    # Buy dominance: green candle volume vs total
    total_vol = comp_df["volume"].sum()
    buy_vol   = comp_df.loc[comp_df["is_bullish"], "volume"].sum()
    buy_dom   = buy_vol / total_vol if total_vol > 0 else 0

    buy_dominant = buy_dom >= CONFIG["accum_buy_dominance"]

    confirmed = vol_rising or buy_dominant  # either condition validates accumulation

    return {
        "confirmed":        confirmed,
        "vol_rising":       vol_rising,
        "buy_dominant":     buy_dominant,
        "buy_dominance_pct": round(buy_dom * 100, 1),
        "vol_ratio_recent_vs_earlier": round(recent_avg_vol / earlier_avg_vol, 2) if earlier_avg_vol > 0 else None,
    }


def check_breakout(df: pd.DataFrame, idx: int, compression_indices: list) -> dict:
    """
    Check if current candle is a valid breakout:
      1. Volume spike vs rolling avg
      2. Range expansion vs compression avg range
      3. Strong close position
      4. Close above highest close in compression phase
    """
    row      = df.loc[idx]
    comp_df  = df.loc[compression_indices]

    # 1. Volume spike
    vol_ok = (
        not pd.isna(row["vol_rolling_avg"]) and
        row["vol_rolling_avg"] > 0 and
        row["volume"] >= CONFIG["breakout_vol_multiplier"] * row["vol_rolling_avg"]
    )

    # 2. Range expansion
    avg_comp_range = comp_df["range"].mean()
    range_ok = (
        avg_comp_range > 0 and
        row["range"] >= CONFIG["breakout_range_expansion"] * avg_comp_range
    )

    # 3. Close position (close near top of candle)
    close_pos_ok = row["close_position"] >= CONFIG["breakout_close_position"]

    # 4. Close above all closes in compression
    highest_comp_close = comp_df["close"].max()
    close_above_ok = (
        row["close"] > highest_comp_close
        if CONFIG["breakout_close_above_compression"]
        else True
    )

    # 5. Must be bullish
    bullish_ok = row["is_bullish"]

    all_pass = vol_ok and range_ok and close_pos_ok and close_above_ok and bullish_ok

    return {
        "valid":              all_pass,
        "vol_ok":             vol_ok,
        "range_ok":           range_ok,
        "close_pos_ok":       close_pos_ok,
        "close_above_ok":     close_above_ok,
        "bullish_ok":         bullish_ok,
        "vol_ratio":          round(row["volume"] / row["vol_rolling_avg"], 2) if not pd.isna(row["vol_rolling_avg"]) and row["vol_rolling_avg"] > 0 else None,
        "range_expansion":    round(row["range"] / avg_comp_range, 2) if avg_comp_range > 0 else None,
        "close_position":     round(row["close_position"], 2),
        "highest_comp_close": round(highest_comp_close, 6),
    }


# ─────────────────────────────────────────────────────────────────────
#  STATE MACHINE — MAIN LOOP
# ─────────────────────────────────────────────────────────────────────
def run_state_machine(df: pd.DataFrame) -> list:
    """
    Iterate candle by candle through the state machine.
    Returns list of signal dicts.
    """
    signals = []
    state   = MarketState()
    cw      = CONFIG["compression_window"]

    for idx in df.index:
        row = df.loc[idx]

        # ── IDLE: look for start of compression
        if state.phase == Phase.IDLE:
            if is_compressed(row):
                state.phase             = Phase.COMPRESSION
                state.compression_start = idx
                state.compression_candles = [idx]
            # else stay IDLE

        # ── COMPRESSION: accumulate compressed candles
        elif state.phase == Phase.COMPRESSION:
            if is_compressed(row):
                state.compression_candles.append(idx)

                # Once we have enough compressed candles, check for accumulation
                if len(state.compression_candles) >= cw:
                    accum = check_accumulation(df, state.compression_candles)
                    if accum["confirmed"]:
                        state.phase = Phase.ACCUMULATION
                        state.accumulation_confirmed = True
                        state._accum_stats = accum
            else:
                # Candle broke out of compression — check if it's a valid breakout
                if state.accumulation_confirmed and len(state.compression_candles) >= cw:
                    bk = check_breakout(df, idx, state.compression_candles)
                    if bk["valid"]:
                        state.phase = Phase.SIGNAL
                        signals.append(_build_signal(df, idx, state, bk))
                # Reset regardless
                state.reset()

        # ── ACCUMULATION: compression confirmed + accumulation detected, wait for breakout
        elif state.phase == Phase.ACCUMULATION:
            if is_compressed(row):
                state.compression_candles.append(idx)
                # Refresh accumulation check with new candle
                accum = check_accumulation(df, state.compression_candles)
                state._accum_stats = accum
            else:
                # This candle broke out — validate it
                bk = check_breakout(df, idx, state.compression_candles)
                if bk["valid"]:
                    state.phase = Phase.SIGNAL
                    signals.append(_build_signal(df, idx, state, bk))
                state.reset()

        # ── SIGNAL: emitted, reset and start fresh
        elif state.phase == Phase.SIGNAL:
            state.reset()
            # Re-evaluate this candle from IDLE
            if is_compressed(row):
                state.phase             = Phase.COMPRESSION
                state.compression_start = idx
                state.compression_candles = [idx]

    return signals


def _build_signal(df, idx, state, breakout_stats):
    row        = df.loc[idx]
    comp_df    = df.loc[state.compression_candles]
    accum      = getattr(state, "_accum_stats", {})
    ts         = row.get("timestamp", idx)

    return {
        "signal_index":        idx,
        "timestamp":           ts,
        "breakout_open":       round(row["open"],   6),
        "breakout_high":       round(row["high"],   6),
        "breakout_low":        round(row["low"],    6),
        "breakout_close":      round(row["close"],  6),
        "breakout_volume":     int(row["volume"]),
        "vol_ratio":           breakout_stats["vol_ratio"],
        "range_expansion":     breakout_stats["range_expansion"],
        "close_position":      breakout_stats["close_position"],
        "compression_candles": len(state.compression_candles),
        "compression_start":   state.compression_start,
        "accum_vol_rising":    accum.get("vol_rising"),
        "accum_buy_dominant":  accum.get("buy_dominant"),
        "buy_dominance_pct":   accum.get("buy_dominance_pct"),
        "vol_trend_ratio":     accum.get("vol_ratio_recent_vs_earlier"),
        # Outcome placeholders
        "next_1_close":        df.loc[idx + 1, "close"] if idx + 1 in df.index else None,
        "next_2_close":        df.loc[idx + 2, "close"] if idx + 2 in df.index else None,
    }


# ─────────────────────────────────────────────────────────────────────
#  OUTCOME TRACKER
# ─────────────────────────────────────────────────────────────────────
def compute_outcomes(signals: list) -> list:
    for s in signals:
        c0 = s["breakout_close"]
        c1 = s["next_1_close"]
        c2 = s["next_2_close"]

        r1 = ((c1 - c0) / c0 * 100) if c1 else None
        r2 = ((c2 - c0) / c0 * 100) if c2 else None

        s["return_c1_pct"] = round(r1, 5) if r1 is not None else None
        s["return_c2_pct"] = round(r2, 5) if r2 is not None else None

        if r1 is None:
            s["outcome"] = "NO_DATA"
        elif r1 > 0 and r2 is not None and r2 > r1:
            s["outcome"] = "STRONG_FOLLOW"
        elif r1 > 0 and r2 is not None and r2 > 0:
            s["outcome"] = "WEAK_FOLLOW"
        elif r1 > 0 and r2 is not None and r2 <= 0:
            s["outcome"] = "REVERSED_C2"
        elif r1 > 0:
            s["outcome"] = "FOLLOW"
        else:
            s["outcome"] = "FAILED"

    return signals


# ─────────────────────────────────────────────────────────────────────
#  CONSOLE LOGGER
# ─────────────────────────────────────────────────────────────────────
def log_signals(signals: list, total_candles: int):
    W = 74

    print("\n" + "═" * W)
    print("  BUILD-UP STRATEGY  |  State Machine Signal Detector")
    print("═" * W)
    print(f"  Total candles  : {total_candles}")
    print(f"  Signals found  : {len(signals)}")
    print("─" * W)
    print(f"  Phases: IDLE → COMPRESSION ({CONFIG['compression_window']}+ candles) "
          f"→ ACCUMULATION → BREAKOUT SIGNAL")
    print("═" * W)

    if not signals:
        print("\n  [INFO] No build-up signals found. Try relaxing thresholds.\n")
        print("═" * W)
        return

    for s in signals:
        outcome_label = {
            "STRONG_FOLLOW": "✅  STRONG FOLLOW-THROUGH",
            "WEAK_FOLLOW":   "🟡  WEAK FOLLOW (faded on C2)",
            "REVERSED_C2":   "⚠️   REVERSED ON CANDLE 2",
            "FOLLOW":        "✅  FOLLOW (C2 data missing)",
            "FAILED":        "❌  FAILED",
            "NO_DATA":       "⬜  NO OUTCOME DATA",
        }.get(s["outcome"], s["outcome"])

        print(f"\n  ┌── BUY SIGNAL @ {s['timestamp']}  (index {s['signal_index']})")
        print(f"  │")
        print(f"  │   ── BREAKOUT CANDLE ─────────────────────────────────────")
        print(f"  │   O={s['breakout_open']}  H={s['breakout_high']}"
              f"  L={s['breakout_low']}  C={s['breakout_close']}")
        print(f"  │   Volume       : {s['breakout_volume']:>12,.0f}"
              f"  ({s['vol_ratio']}x rolling avg)")
        print(f"  │   Range expand : {s['range_expansion']}x compression avg range")
        print(f"  │   Close pos    : {s['close_position']}  (1.0 = top of candle)")
        print(f"  │")
        print(f"  │   ── BUILD-UP STATS ───────────────────────────────────────")
        print(f"  │   Compression  : {s['compression_candles']} candles"
              f"  (from index {s['compression_start']})")
        print(f"  │   Vol rising   : {s['accum_vol_rising']}"
              f"  |  trend ratio: {s['vol_trend_ratio']}x")
        print(f"  │   Buy dominant : {s['accum_buy_dominant']}"
              f"  |  buy vol %: {s['buy_dominance_pct']}%")
        print(f"  │")
        print(f"  │   ── OUTCOME ──────────────────────────────────────────────")

        r1 = s.get("return_c1_pct")
        r2 = s.get("return_c2_pct")

        if r1 is not None:
            arrow1 = "▲" if r1 > 0 else "▼"
            print(f"  │   Candle +1    : {arrow1} {r1:+.5f}%")
        else:
            print(f"  │   Candle +1    : (end of data)")

        if r2 is not None:
            arrow2 = "▲" if r2 > 0 else "▼"
            print(f"  │   Candle +2    : {arrow2} {r2:+.5f}%")
        else:
            print(f"  │   Candle +2    : (end of data)")

        print(f"  └── {outcome_label}")

    # ── Summary
    print("\n" + "─" * W)
    print("  OUTCOME SUMMARY")
    print("─" * W)

    from collections import Counter
    counts = Counter(s["outcome"] for s in signals)
    total  = len(signals)

    for outcome, count in sorted(counts.items(), key=lambda x: -x[1]):
        pct = count / total * 100
        bar = "█" * int(pct / 4)
        print(f"  {outcome:<20} {count:>3}  ({pct:5.1f}%)  {bar}")

    winners    = [s for s in signals if s["outcome"] in ("STRONG_FOLLOW", "WEAK_FOLLOW", "FOLLOW")]
    win_rate   = len(winners) / total * 100
    valid_r1   = [s["return_c1_pct"] for s in signals if s["return_c1_pct"] is not None]
    valid_r2   = [s["return_c2_pct"] for s in signals if s["return_c2_pct"] is not None]

    print("─" * W)
    print(f"  Win rate (C+1 positive)  : {win_rate:.1f}%")
    if valid_r1:
        print(f"  Avg return C+1           : {np.mean(valid_r1):+.5f}%")
    if valid_r2:
        print(f"  Avg return C+2           : {np.mean(valid_r2):+.5f}%")
    print("═" * W + "\n")


# ─────────────────────────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────────────────────────
def run(df: pd.DataFrame) -> tuple[pd.DataFrame, list]:
    """
    Pass your DataFrame here.
    Required columns: timestamp, open, high, low, close, volume

    Returns:
        df_enriched  — original df with computed indicators
        signals      — list of signal dicts
    """
    df_enriched = compute_indicators(df)
    signals     = run_state_machine(df_enriched)
    signals     = compute_outcomes(signals)
    log_signals(signals, total_candles=len(df_enriched))
    return df_enriched, signals


# ─────────────────────────────────────────────────────────────────────
#  SYNTHETIC TEST
# ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    np.random.seed(7)
    n          = 300
    timestamps = pd.date_range("2024-01-01 09:15:00", periods=n, freq="10s")
    price      = 100.0
    prices     = []
    volumes    = []

    for i in range(n):
        # Inject a buildup + breakout pattern at index 60 and 180
        if i in (55, 56, 57, 58, 59, 60, 61, 62, 63, 64):
            # Compression zone
            move   = np.random.uniform(-0.003, 0.003)
            vol    = np.random.randint(400, 600)
        elif i == 65:
            # Breakout candle
            move   = 0.025
            vol    = 5500
        elif i in (145, 146, 147, 148, 149, 150, 151, 152, 153, 154):
            move   = np.random.uniform(-0.002, 0.002)
            vol    = np.random.randint(350, 500) + int((i - 145) * 30)  # slowly rising vol
        elif i == 155:
            move   = 0.030
            vol    = 6200
        else:
            move   = np.random.randn() * 0.015
            vol    = np.random.randint(800, 2000)

        price += move
        prices.append(price)
        volumes.append(vol)

    prices  = np.array(prices)
    volumes = np.array(volumes, dtype=float)
    noise   = np.abs(np.random.randn(n) * 0.005)

    df_test = pd.DataFrame({
        "timestamp": timestamps,
        "open":      prices - np.random.uniform(0, 0.005, n),
        "high":      prices + noise,
        "low":       prices - noise,
        "close":     prices,
        "volume":    volumes,
    })

    # Ensure OHLC integrity
    df_test["high"]  = df_test[["open", "close", "high"]].max(axis=1)
    df_test["low"]   = df_test[["open", "close", "low"]].min(axis=1)

    df_result, signals = run(df_test)

    # Optional: inspect as DataFrame
    if signals:
        sig_df = pd.DataFrame(signals)
        print("  Signal DataFrame preview:")
        print(sig_df[["timestamp", "breakout_close", "vol_ratio",
                       "compression_candles", "buy_dominance_pct",
                       "return_c1_pct", "return_c2_pct", "outcome"]].to_string(index=False))
        print()