"""
============================================================
  MARKET SENTIMENT EXPERIMENTS — INDIAN MARKETS (1 tick/sec)
============================================================

AUTHOR: Generated for plug-and-play experimentation
CONTEXT: NSE/BSE instruments with ~1 tick/sec data feed

------------------------------------------------------------
  EXPECTED INPUT DATA FORMATS
------------------------------------------------------------

1. ORDER BOOK DATA  →  `ob_df`  (one row per tick)
   CSV columns (exact names required):
     timestamp       : datetime string  e.g. "2024-01-15 09:15:01"
     last_price      : float            e.g. 19450.25
     bid_p1..bid_p5  : float            best bid price → 5th bid price
     bid_q1..bid_q5  : int              qty at each bid level
     ask_p1..ask_p5  : float            best ask price → 5th ask price
     ask_q1..ask_q5  : int              qty at each ask level
     buy_limit_qty   : int              total pending buy limit orders
     sell_limit_qty  : int              total pending sell limit orders

   Example row:
     timestamp,       last_price, bid_p1,  bid_q1, bid_p2,  bid_q2, ..., ask_p1,  ask_q1, ...
     2024-01-15 09:15:01, 19450.25, 19450.0, 500,   19449.5, 320,   ..., 19450.5, 410,    ...

2. TIME & SALES DATA  →  `tns_df`  (one row per print)
   CSV columns:
     timestamp  : datetime string  (same resolution as ob_df)
     price      : float            trade price
     quantity   : int              aggregated qty at that price/time

   Note: direction (buy/sell) is NOT required — we infer it from price vs mid.

------------------------------------------------------------
  HOW TO USE
------------------------------------------------------------

   df_ob  = pd.read_csv("your_ob_data.csv",  parse_dates=["timestamp"])
   df_tns = pd.read_csv("your_tns_data.csv", parse_dates=["timestamp"])

   results = run_all_experiments(df_ob, df_tns)

   # See summary of all experiments
   print(results["summary"])

   # Deep dive into a specific experiment
   print(results["exp1"]["detail"])
   plot_experiment(results, exp_number=2)

------------------------------------------------------------
  HOW TO EVALUATE RESULTS
------------------------------------------------------------

   Each experiment outputs:
     - signal_accuracy  : % of times signal predicted correct direction
                          > 52% is interesting, > 56% is meaningful
     - avg_move_when_bullish  : avg price change in next N seconds when signal = bullish
     - avg_move_when_bearish  : avg price change in next N seconds when signal = bearish
     - signal_count     : how many times the signal fired (low count = unreliable stats)
     - confusion_matrix : TP/FP/TN/FN breakdown

   What to look for:
     - accuracy > 55% consistently across different days = real signal
     - avg_move asymmetry (bullish moves > bearish moves) = directional edge
     - signal_count too low (<50) = not enough data to conclude anything
     - accuracy ~50% = noise, discard or combine with other signals

============================================================
"""

import pandas as pd
import numpy as np
import warnings
from typing import Tuple, Dict, Optional
import matplotlib

matplotlib.use("Agg")  # non-interactive backend — safe for all environments
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec

warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────
#  CONFIGURATION  — tweak these before running
# ─────────────────────────────────────────────

CFG = {
    # How many seconds ahead to measure price outcome
    "forward_window_sec": 20,  # prediction horizon: did price go up/down in next 20s?

    # Minimum price move to count as "up" or "down" (filters noise)
    # Set this to roughly 1 tick size of your instrument
    # e.g. Nifty futures: 0.5,  midcap stocks: 0.05, BankNifty: 1.0
    "min_move_threshold": 0.5,

    # Rolling window for OBI trend (number of ticks)
    "obi_rolling_window": 20,

    # Mid-price drift window (number of ticks)
    "mid_drift_window": 5,

    # T&S aggression rolling window (seconds)
    "tns_window_sec": 20,

    # Absorption detection: minimum consecutive ticks of level-1 qty shrinking
    "absorption_min_ticks": 3,

    # OBI threshold to consider signal "strong enough" to act on
    # Range: 0 to 1. Higher = fewer but stronger signals.
    "obi_signal_threshold": 0.15,
}


# ─────────────────────────────────────────────
#  DATA VALIDATION
# ─────────────────────────────────────────────

def validate_ob_data(ob_df: pd.DataFrame) -> pd.DataFrame:
    """
    Validates and cleans order book dataframe.
    Will raise clear errors if required columns are missing.
    """
    required_cols = (
            ["timestamp", "last_price", "buy_limit_qty", "sell_limit_qty"] +
            [f"bid_p{i}" for i in range(1, 6)] +
            [f"bid_q{i}" for i in range(1, 6)] +
            [f"ask_p{i}" for i in range(1, 6)] +
            [f"ask_q{i}" for i in range(1, 6)]
    )
    missing = [c for c in required_cols if c not in ob_df.columns]
    if missing:
        raise ValueError(f"Order book data missing columns: {missing}\nSee file header for expected format.")

    df = ob_df.copy()
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"])

    # Drop rows where prices are clearly bad
    df = df[df["last_price"] > 0]
    df = df[df["bid_p1"] > 0]
    df = df[df["ask_p1"] > 0]
    df = df[df["bid_p1"] < df["ask_p1"]]  # bid must be below ask

    print(f"[OK] Order book data: {len(df)} valid ticks loaded.")
    return df


def validate_tns_data(tns_df: pd.DataFrame) -> pd.DataFrame:
    """
    Validates and cleans time & sales dataframe.
    """
    required_cols = ["timestamp", "price", "quantity"]
    missing = [c for c in required_cols if c not in tns_df.columns]
    if missing:
        raise ValueError(f"T&S data missing columns: {missing}\nSee file header for expected format.")

    df = tns_df.copy()
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df[df["price"] > 0]
    df = df[df["quantity"] > 0]

    print(f"[OK] T&S data: {len(df)} valid prints loaded.")
    return df


# ─────────────────────────────────────────────
#  FEATURE ENGINEERING — shared across experiments
# ─────────────────────────────────────────────

def compute_base_features(ob_df: pd.DataFrame) -> pd.DataFrame:
    """
    Computes features used across multiple experiments.
    All features derived from order book snapshots only.
    """
    df = ob_df.copy()

    # --- Mid price ---
    df["mid"] = (df["ask_p1"] + df["bid_p1"]) / 2

    # --- Spread ---
    df["spread"] = df["ask_p1"] - df["bid_p1"]

    # --- Weighted OBI (level proximity weighting: level1=5x, level5=1x) ---
    # Rationale: orders closer to best price are more likely to be filled
    # and represent more genuine pressure than far-away levels.
    weights = [5, 4, 3, 2, 1]
    bid_weighted = sum(w * df[f"bid_q{i + 1}"] for i, w in enumerate(weights))
    ask_weighted = sum(w * df[f"ask_q{i + 1}"] for i, w in enumerate(weights))
    total_weighted = bid_weighted + ask_weighted
    df["obi"] = np.where(total_weighted > 0, (bid_weighted - ask_weighted) / total_weighted, 0)

    # --- Simple (unweighted) OBI for comparison ---
    bid_total = sum(df[f"bid_q{i}"] for i in range(1, 6))
    ask_total = sum(df[f"ask_q{i}"] for i in range(1, 6))
    df["obi_simple"] = np.where(
        (bid_total + ask_total) > 0,
        (bid_total - ask_total) / (bid_total + ask_total),
        0
    )

    # --- Limit order skew ---
    limit_total = df["buy_limit_qty"] + df["sell_limit_qty"]
    df["limit_skew"] = np.where(limit_total > 0,
                                (df["buy_limit_qty"] - df["sell_limit_qty"]) / limit_total, 0)

    # --- Forward return: price N seconds later minus current price ---
    # This is our prediction TARGET for all experiments.
    fw = CFG["forward_window_sec"]
    df["future_price"] = df["last_price"].shift(-fw)
    df["forward_return"] = df["future_price"] - df["last_price"]

    # --- Direction label: +1 (up), -1 (down), 0 (flat/noise) ---
    thr = CFG["min_move_threshold"]
    df["future_direction"] = np.where(
        df["forward_return"] > thr, 1,
        np.where(df["forward_return"] < -thr, -1, 0)
    )

    return df


def compute_forward_label(series: pd.Series, threshold: float, window: int) -> pd.Series:
    """Returns +1/-1/0 direction label for each row."""
    future = series.shift(-window)
    ret = future - series
    return np.where(ret > threshold, 1, np.where(ret < -threshold, -1, 0))


def evaluate_signal(signal: pd.Series, label: pd.Series) -> Dict:
    """
    Core evaluation function used by all experiments.

    signal : pd.Series of +1 (bullish), -1 (bearish), 0 (no signal)
    label  : pd.Series of +1 (price went up), -1 (down), 0 (flat)

    Returns dict with accuracy, signal counts, and move statistics.
    """
    mask = (signal != 0) & (label != 0)  # only evaluate when both signal and outcome are non-neutral
    s = signal[mask]
    l = label[mask]

    if len(s) < 10:
        return {"error": "Too few signal+outcome pairs to evaluate (<10). Adjust thresholds or use more data."}

    correct = (s == l).sum()
    total = len(s)
    accuracy = correct / total

    tp = ((s == 1) & (l == 1)).sum()
    fp = ((s == 1) & (l == -1)).sum()
    tn = ((s == -1) & (l == -1)).sum()
    fn = ((s == -1) & (l == 1)).sum()

    return {
        "signal_accuracy": round(accuracy * 100, 2),
        "signal_count": int(total),
        "bullish_signals": int((signal == 1).sum()),
        "bearish_signals": int((signal == -1).sum()),
        "confusion_matrix": {"TP": int(tp), "FP": int(fp), "TN": int(tn), "FN": int(fn)},
        "baseline_accuracy": round((l == 1).mean() * 100, 2),  # % of times price went up regardless of signal
    }


# ─────────────────────────────────────────────
#  EXPERIMENT 1 — Rolling OBI Trend
# ─────────────────────────────────────────────

def experiment_1_rolling_obi(df: pd.DataFrame) -> Dict:
    """
    EXPERIMENT 1: Rolling OBI Trend
    --------------------------------
    Hypothesis:
        A single OBI snapshot is noisy. But if OBI has been
        consistently positive (or negative) for the last N ticks,
        it suggests sustained pressure that is likely to push price.

    Signal logic:
        - Compute OBI at each tick (weighted by level proximity)
        - Take rolling mean over last `obi_rolling_window` ticks
        - If rolling OBI > threshold → Bullish signal
        - If rolling OBI < -threshold → Bearish signal

    What to look for in results:
        - accuracy > 55%: rolling OBI has edge at your instrument
        - accuracy ~50%: OBI is too noisy at 1 tick/sec, skip this
        - If bullish_signals >> bearish_signals: your instrument has
          upward bias in the test period (normal for indices in bull market)
    """
    window = CFG["obi_rolling_window"]
    threshold = CFG["obi_signal_threshold"]

    result_df = df.copy()
    result_df["rolling_obi"] = result_df["obi"].rolling(window, min_periods=3).mean()

    result_df["signal"] = np.where(
        result_df["rolling_obi"] > threshold, 1,
        np.where(result_df["rolling_obi"] < -threshold, -1, 0)
    )

    eval_result = evaluate_signal(result_df["signal"], result_df["future_direction"])
    eval_result["experiment"] = "Rolling OBI Trend"
    eval_result["description"] = f"OBI rolling mean over {window} ticks, threshold ±{threshold}"
    eval_result["detail"] = result_df[["timestamp", "obi", "rolling_obi", "signal",
                                       "last_price", "future_direction"]].dropna()
    return eval_result


# ─────────────────────────────────────────────
#  EXPERIMENT 2 — OBI + Mid-Price Confluence
# ─────────────────────────────────────────────

def experiment_2_obi_mid_confluence(df: pd.DataFrame) -> Dict:
    """
    EXPERIMENT 2: OBI + Mid-Price Drift Confluence
    ------------------------------------------------
    Hypothesis:
        False positives drop significantly when two independent signals
        agree. OBI shows order book pressure. Mid-price drift shows
        that price is already starting to move. When both point the
        same direction, conviction is higher.

    Signal logic:
        - Rolling OBI > threshold  AND  mid drifted up over N ticks → Bullish
        - Rolling OBI < -threshold AND  mid drifted down over N ticks → Bearish
        - Disagreement → no signal (sit out)

    What to look for in results:
        - Compare accuracy to Experiment 1. It should be higher here.
        - signal_count will be lower than Exp 1 (confluence filters out noise).
        - If signal_count drops drastically (< 20), your instrument doesn't
          have sustained moves at this timescale — widen the thresholds.
    """
    obi_window = CFG["obi_rolling_window"]
    mid_window = CFG["mid_drift_window"]
    threshold = CFG["obi_signal_threshold"]

    result_df = df.copy()
    result_df["rolling_obi"] = result_df["obi"].rolling(obi_window, min_periods=3).mean()

    # Mid-price drift: current mid minus mid N ticks ago
    result_df["mid_drift"] = result_df["mid"] - result_df["mid"].shift(mid_window)

    obi_bull = result_df["rolling_obi"] > threshold
    obi_bear = result_df["rolling_obi"] < -threshold
    mid_bull = result_df["mid_drift"] > 0
    mid_bear = result_df["mid_drift"] < 0

    result_df["signal"] = np.where(
        obi_bull & mid_bull, 1,
        np.where(obi_bear & mid_bear, -1, 0)
    )

    eval_result = evaluate_signal(result_df["signal"], result_df["future_direction"])
    eval_result["experiment"] = "OBI + Mid-Price Confluence"
    eval_result["description"] = (f"Rolling OBI ({obi_window} ticks) + "
                                  f"Mid drift ({mid_window} ticks) must agree")
    eval_result["detail"] = result_df[["timestamp", "rolling_obi", "mid_drift",
                                       "signal", "last_price", "future_direction"]].dropna()
    return eval_result


# ─────────────────────────────────────────────
#  EXPERIMENT 3 — Best Level Absorption
# ─────────────────────────────────────────────

def experiment_3_absorption(df: pd.DataFrame) -> Dict:
    """
    EXPERIMENT 3: Ask / Bid Absorption Detection
    ---------------------------------------------
    Hypothesis:
        When a large seller (ask wall) is being absorbed tick-by-tick —
        best ask quantity is shrinking even though ask price hasn't moved
        — buyers are eating through the supply. Once the wall breaks,
        price jumps. Same logic inverted for bid absorption.

    Signal logic:
        - Track best ask qty (ask_q1) tick over tick
        - If ask_q1 has been shrinking for `absorption_min_ticks` consecutive
          ticks AND ask price hasn't moved → Bullish absorption signal
        - If bid_q1 has been shrinking for N ticks AND bid price unchanged → Bearish

    Limitation at 1 tick/sec:
        Between ticks, qty can be refilled. So this signal catches
        *net* absorption, not every single fill. Still useful.

    What to look for:
        - This signal fires rarely — it needs N consecutive ticks of shrinkage.
        - When it does fire with high accuracy, it's a strong indicator.
        - If accuracy is high but count is very low, it's still useful as
          a "confirmation" signal layered onto Exp 2.
    """
    n = CFG["absorption_min_ticks"]
    result_df = df.copy()

    # Ask absorption: ask_q1 falling, ask_p1 unchanged
    ask_qty_change = result_df["ask_q1"].diff()
    ask_price_unchanged = result_df["ask_p1"].diff().abs() < 0.01

    # Count consecutive ticks of ask qty shrinking with price stable
    ask_absorb_tick = (ask_qty_change < 0) & ask_price_unchanged
    ask_consec = ask_absorb_tick.groupby(
        (~ask_absorb_tick).cumsum()
    ).cumcount()
    result_df["ask_absorption"] = ask_consec >= n

    # Bid absorption: bid_q1 falling, bid_p1 unchanged
    bid_qty_change = result_df["bid_q1"].diff()
    bid_price_unchanged = result_df["bid_p1"].diff().abs() < 0.01
    bid_absorb_tick = (bid_qty_change < 0) & bid_price_unchanged
    bid_consec = bid_absorb_tick.groupby(
        (~bid_absorb_tick).cumsum()
    ).cumcount()
    result_df["bid_absorption"] = bid_consec >= n

    result_df["signal"] = np.where(
        result_df["ask_absorption"], 1,  # ask wall being eaten → price likely up
        np.where(result_df["bid_absorption"], -1, 0)
    )

    eval_result = evaluate_signal(result_df["signal"], result_df["future_direction"])
    eval_result["experiment"] = "Best Level Absorption"
    eval_result["description"] = (f"Best ask/bid qty shrinking for {n}+ "
                                  f"consecutive ticks with price unchanged")
    eval_result["detail"] = result_df[["timestamp", "ask_p1", "ask_q1", "bid_p1",
                                       "bid_q1", "ask_absorption", "bid_absorption",
                                       "signal", "last_price", "future_direction"]].dropna()
    return eval_result


# ─────────────────────────────────────────────
#  EXPERIMENT 4 — T&S Aggression Proxy
# ─────────────────────────────────────────────

def experiment_4_tns_aggression(ob_df: pd.DataFrame, tns_df: pd.DataFrame) -> Dict:
    """
    EXPERIMENT 4: Time & Sales Aggression Proxy
    --------------------------------------------
    Hypothesis:
        Trades happening at or above mid-price = buyers are aggressive
        (hitting the ask). Trades below mid = sellers aggressive.
        Rolling aggression score over 15 seconds captures sustained
        one-sided flow, which predicts near-term price direction.

    Signal logic:
        For each T&S print, compare trade price to mid at that second:
          - price >= mid  → buy-aggression, weight by quantity
          - price < mid   → sell-aggression, weight by quantity
        Compute 15-second rolling net aggression score.
        Merge back to ob_df on nearest timestamp.

    Limitation:
        Since your T&S is aggregated, a single print at mid could be
        either direction. We use >= mid as a conservative buy proxy.
        It won't be perfectly clean but directionally valid.

    What to look for:
        - aggression_score consistently predicting direction → real flow signal
        - If accuracy ≈ 50%: aggregation is too lossy to extract direction
        - Compare to Exp 2: if both are ~55-58%, combining them (Exp 5) will help
    """
    tns_window = CFG["tns_window_sec"]

    ob = ob_df.copy()
    tns = tns_df.copy()

    # Merge mid-price into T&S on nearest timestamp
    ob_mid = ob[["timestamp", "mid"]].set_index("timestamp")
    tns = tns.sort_values("timestamp")
    tns = pd.merge_asof(tns.sort_values("timestamp"),
                        ob_mid.reset_index(),
                        on="timestamp",
                        direction="backward")

    tns = tns.dropna(subset=["mid"])

    # Signed quantity: positive = buy aggression, negative = sell aggression
    tns["signed_qty"] = np.where(tns["price"] >= tns["mid"],
                                 tns["quantity"],
                                 -tns["quantity"])

    # Aggregate signed qty per second and merge into ob_df
    tns["ts_floor"] = tns["timestamp"].dt.floor("s")
    agg = tns.groupby("ts_floor")["signed_qty"].sum().reset_index()
    agg.columns = ["timestamp", "signed_qty_per_sec"]

    ob = pd.merge_asof(ob.sort_values("timestamp"),
                       agg.sort_values("timestamp"),
                       on="timestamp",
                       direction="backward")

    ob["signed_qty_per_sec"] = ob["signed_qty_per_sec"].fillna(0)

    # Rolling aggression score over tns_window seconds
    ob["aggression_score"] = ob["signed_qty_per_sec"].rolling(
        tns_window, min_periods=3).sum()

    # Normalise to -1..+1 range for interpretability
    max_agg = ob["aggression_score"].abs().quantile(0.95)
    ob["aggression_norm"] = (ob["aggression_score"] / max_agg).clip(-1, 1)

    threshold = 0.2  # normalised threshold — tune this
    ob["signal"] = np.where(
        ob["aggression_norm"] > threshold, 1,
        np.where(ob["aggression_norm"] < -threshold, -1, 0)
    )

    eval_result = evaluate_signal(ob["signal"], ob["future_direction"])
    eval_result["experiment"] = "T&S Aggression Proxy"
    eval_result["description"] = (f"Signed T&S qty (price vs mid), "
                                  f"rolling {tns_window}s, threshold ±{threshold}")
    eval_result["detail"] = ob[["timestamp", "aggression_score", "aggression_norm",
                                "signal", "last_price", "future_direction"]].dropna()
    return eval_result


# ─────────────────────────────────────────────
#  EXPERIMENT 5 — OBI Divergence from Price
# ─────────────────────────────────────────────

def experiment_5_obi_divergence(df: pd.DataFrame) -> Dict:
    """
    EXPERIMENT 5: OBI Divergence from Price
    ----------------------------------------
    Hypothesis:
        Divergence between OBI and price direction is often more
        predictive than trend-following.

        Case A — Bullish divergence:
            Price is flat or falling over last N ticks BUT OBI is
            strongly positive → buying pressure is building, price
            likely to catch up upward.

        Case B — Bearish divergence:
            Price is rising BUT OBI is negative → the move lacks
            book support, likely to reverse or stall.

    Signal logic:
        - Price trend over last `mid_drift_window` ticks
        - Rolling OBI over same window
        - Divergence = they disagree in direction
        - Bullish divergence  → signal = +1
        - Bearish divergence  → signal = -1
        - Agreement or neutral → signal = 0 (no trade)

    What to look for:
        - This signal is contrarian. It catches reversals, not continuations.
        - If accuracy > 55% here but Exp 2 also has accuracy > 55%, you
          have two complementary signals — one trend-following, one mean-reversion.
        - If both Exp 2 and Exp 5 are >55%, you can build a regime detector:
          use Exp 2 when market is trending, Exp 5 when it's range-bound.
    """
    obi_window = CFG["obi_rolling_window"]
    mid_window = CFG["mid_drift_window"]
    threshold = CFG["obi_signal_threshold"]

    result_df = df.copy()
    result_df["rolling_obi"] = result_df["obi"].rolling(obi_window, min_periods=3).mean()
    result_df["mid_drift"] = result_df["mid"] - result_df["mid"].shift(mid_window)

    # Categorise price trend
    price_up = result_df["mid_drift"] > CFG["min_move_threshold"] * 0.3
    price_down = result_df["mid_drift"] < -CFG["min_move_threshold"] * 0.3
    price_flat = ~price_up & ~price_down

    obi_bull = result_df["rolling_obi"] > threshold
    obi_bear = result_df["rolling_obi"] < -threshold

    # Bullish divergence: price weak/falling but OBI bullish
    bullish_div = obi_bull & (price_down | price_flat)
    # Bearish divergence: price strong/rising but OBI bearish
    bearish_div = obi_bear & (price_up | price_flat)

    result_df["signal"] = np.where(
        bullish_div, 1,
        np.where(bearish_div, -1, 0)
    )

    eval_result = evaluate_signal(result_df["signal"], result_df["future_direction"])
    eval_result["experiment"] = "OBI Divergence from Price"
    eval_result["description"] = (f"Divergence: OBI and mid-price drift disagree "
                                  f"(window: {obi_window} ticks OBI, {mid_window} ticks mid)")
    eval_result["detail"] = result_df[["timestamp", "rolling_obi", "mid_drift",
                                       "signal", "last_price", "future_direction"]].dropna()
    return eval_result


# ─────────────────────────────────────────────
#  BONUS: COMPOSITE SIGNAL
# ─────────────────────────────────────────────

def experiment_composite(df: pd.DataFrame,
                         exp1: Dict, exp2: Dict,
                         exp3: Dict, exp5: Dict) -> Dict:
    """
    COMPOSITE SIGNAL: Vote across all OB-based experiments
    -------------------------------------------------------
    Combines signals from Exp1, Exp2, Exp3, Exp5 using majority vote.
    Only fires when at least 3 out of 4 agree.

    Rationale: Each experiment captures a different aspect of the book.
    When multiple signals agree, confidence is higher. This is your
    most conservative signal — fewer fires, but higher precision.

    What to look for:
        - Composite accuracy should be the highest of all experiments.
        - If it's NOT higher than individual experiments, your signals
          are highly correlated (they're all seeing the same thing)
          and diversification isn't helping.
    """
    votes = pd.DataFrame({
        "exp1": exp1["detail"].set_index("timestamp")["signal"],
        "exp2": exp2["detail"].set_index("timestamp")["signal"],
        "exp3": exp3["detail"].set_index("timestamp")["signal"],
        "exp5": exp5["detail"].set_index("timestamp")["signal"],
    }).fillna(0)

    vote_sum = votes.sum(axis=1)
    composite_signal = pd.Series(
        np.where(vote_sum >= 3, 1, np.where(vote_sum <= -3, -1, 0)),
        index=votes.index
    )

    # Align with future_direction from main df
    fd = df.set_index("timestamp")["future_direction"]
    aligned = pd.concat([composite_signal, fd], axis=1).dropna()
    aligned.columns = ["signal", "future_direction"]

    eval_result = evaluate_signal(aligned["signal"], aligned["future_direction"])
    eval_result["experiment"] = "Composite (3-of-4 vote)"
    eval_result["description"] = "Fires only when Exp1 + Exp2 + Exp3 + Exp5 have 3+ agreements"
    return eval_result


# ─────────────────────────────────────────────
#  PLOTTING
# ─────────────────────────────────────────────

def plot_experiment(results: Dict, exp_number: int = 2, save_path: str = None):
    """
    Plots signal overlaid on price chart for visual inspection.
    exp_number: 1, 2, 3, 4, or 5
    """
    exp_key = f"exp{exp_number}"
    if exp_key not in results:
        print(f"Experiment {exp_number} not found in results.")
        return

    exp = results[exp_key]
    if "detail" not in exp:
        print("No detail dataframe found. Cannot plot.")
        return

    detail = exp["detail"].copy().dropna()
    if len(detail) == 0:
        print("Detail dataframe is empty.")
        return

    fig, axes = plt.subplots(3, 1, figsize=(14, 9), sharex=True)
    fig.suptitle(f"Experiment {exp_number}: {exp.get('experiment', '')}", fontsize=13)

    ax1, ax2, ax3 = axes

    # Price
    ax1.plot(detail["timestamp"], detail["last_price"], color="#444", linewidth=0.8)
    bull_mask = detail["signal"] == 1
    bear_mask = detail["signal"] == -1
    ax1.scatter(detail["timestamp"][bull_mask], detail["last_price"][bull_mask],
                marker="^", color="#1D9E75", s=25, zorder=3, label="Bullish signal")
    ax1.scatter(detail["timestamp"][bear_mask], detail["last_price"][bear_mask],
                marker="v", color="#D85A30", s=25, zorder=3, label="Bearish signal")
    ax1.set_ylabel("Last Price")
    ax1.legend(fontsize=8)
    ax1.grid(True, alpha=0.2)

    # Signal line (use whatever the primary signal column is)
    sig_cols = [c for c in detail.columns
                if c not in ["timestamp", "last_price", "future_direction", "signal",
                             "future_price"] and detail[c].dtype in [float, np.float64]]
    if sig_cols:
        primary = sig_cols[0]
        ax2.plot(detail["timestamp"], detail[primary], color="#185FA5", linewidth=0.8)
        ax2.axhline(0, color="#aaa", linewidth=0.5, linestyle="--")
        ax2.set_ylabel(primary.replace("_", " ").title())
        ax2.grid(True, alpha=0.2)

    # Outcome vs signal
    outcome_colors = detail["future_direction"].map({1: "#1D9E75", -1: "#D85A30", 0: "#ccc"})
    ax3.bar(detail["timestamp"], detail["signal"], color=outcome_colors.values,
            alpha=0.6, width=pd.Timedelta(seconds=1))
    ax3.set_ylabel("Signal direction")
    ax3.set_xlabel("Time")
    ax3.grid(True, alpha=0.2)

    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=120, bbox_inches="tight")
        print(f"Plot saved to {save_path}")
    else:
        plt.show()


def plot_summary(results: Dict, save_path: str = None):
    """
    Bar chart comparing accuracy across all experiments.
    """
    exp_keys = ["exp1", "exp2", "exp3", "exp4", "exp5", "composite"]
    names, accuracies, counts = [], [], []

    for k in exp_keys:
        if k in results and "signal_accuracy" in results[k]:
            names.append(results[k]["experiment"])
            accuracies.append(results[k]["signal_accuracy"])
            counts.append(results[k]["signal_count"])

    if not names:
        print("No experiment results to plot.")
        return

    fig, ax = plt.subplots(figsize=(12, 5))
    bars = ax.barh(names, accuracies, color=["#1D9E75" if a > 55 else
                                             "#EF9F27" if a > 52 else
                                             "#D85A30" for a in accuracies])
    ax.axvline(50, color="#aaa", linewidth=1, linestyle="--", label="Random baseline (50%)")
    ax.axvline(55, color="#1D9E75", linewidth=1, linestyle=":", alpha=0.7, label="Useful threshold (55%)")

    for bar, acc, cnt in zip(bars, accuracies, counts):
        ax.text(bar.get_width() + 0.3, bar.get_y() + bar.get_height() / 2,
                f"{acc:.1f}%  (n={cnt})", va="center", fontsize=9)

    ax.set_xlabel("Signal Accuracy (%)")
    ax.set_title("Experiment Comparison — Signal Accuracy")
    ax.set_xlim(40, max(accuracies) + 8)
    ax.legend(fontsize=8)
    plt.tight_layout()

    if save_path:
        plt.savefig(save_path, dpi=120, bbox_inches="tight")
        print(f"Summary plot saved to {save_path}")
    else:
        plt.show()


# ─────────────────────────────────────────────
#  MAIN RUNNER
# ─────────────────────────────────────────────

def run_all_experiments(ob_df: pd.DataFrame,
                        tns_df: Optional[pd.DataFrame] = None) -> Dict:
    """
    Run all experiments and return results dict.

    Parameters
    ----------
    ob_df  : Order book dataframe (required)
    tns_df : Time & sales dataframe (optional — needed for Exp 4 only)

    Returns
    -------
    Dict with keys: exp1, exp2, exp3, exp4, exp5, composite, summary
    """
    print("\n" + "=" * 55)
    print("  MARKET SENTIMENT EXPERIMENTS")
    print("=" * 55)

    # Validate
    ob_df = validate_ob_data(ob_df)
    if tns_df is not None:
        tns_df = validate_tns_data(tns_df)

    # Base features
    df = compute_base_features(ob_df)
    n_rows = len(df.dropna(subset=["future_direction"]))
    print(f"[OK] Base features computed. {n_rows} rows have valid forward labels.")
    print(f"     Forward window: {CFG['forward_window_sec']}s | "
          f"Move threshold: {CFG['min_move_threshold']}\n")

    results = {}

    # Run experiments
    for exp_num, exp_fn, kwargs, name in [
        (1, experiment_1_rolling_obi, {"df": df}, "Rolling OBI Trend"),
        (2, experiment_2_obi_mid_confluence, {"df": df}, "OBI + Mid Confluence"),
        (3, experiment_3_absorption, {"df": df}, "Absorption Detection"),
        (5, experiment_5_obi_divergence, {"df": df}, "OBI Divergence"),
    ]:
        print(f"Running Experiment {exp_num}: {name}...")
        try:
            r = exp_fn(**kwargs)
            results[f"exp{exp_num}"] = r
            if "error" in r:
                print(f"  [WARN] {r['error']}")
            else:
                print(f"  Accuracy: {r['signal_accuracy']}%  |  "
                      f"Signals: {r['signal_count']}  |  "
                      f"Baseline: {r['baseline_accuracy']}%")
        except Exception as e:
            print(f"  [ERROR] Experiment {exp_num} failed: {e}")
            results[f"exp{exp_num}"] = {"error": str(e)}

    # Exp 4 — requires T&S
    if tns_df is not None:
        print("Running Experiment 4: T&S Aggression Proxy...")
        try:
            r = experiment_4_tns_aggression(df, tns_df)
            results["exp4"] = r
            if "error" in r:
                print(f"  [WARN] {r['error']}")
            else:
                print(f"  Accuracy: {r['signal_accuracy']}%  |  "
                      f"Signals: {r['signal_count']}  |  "
                      f"Baseline: {r['baseline_accuracy']}%")
        except Exception as e:
            print(f"  [ERROR] Experiment 4 failed: {e}")
            results["exp4"] = {"error": str(e)}
    else:
        print("Skipping Experiment 4 (no T&S data provided).")

    # Composite — only if core experiments ran clean
    core_ok = all("signal_accuracy" in results.get(f"exp{k}", {}) for k in [1, 2, 3, 5])
    if core_ok:
        print("Running Composite Signal...")
        try:
            r = experiment_composite(df, results["exp1"], results["exp2"],
                                     results["exp3"], results["exp5"])
            results["composite"] = r
            if "error" in r:
                print(f"  [WARN] {r['error']}")
            else:
                print(f"  Accuracy: {r['signal_accuracy']}%  |  "
                      f"Signals: {r['signal_count']}")
        except Exception as e:
            print(f"  [ERROR] Composite failed: {e}")

    # Summary table
    print("\n" + "=" * 55)
    print("  RESULTS SUMMARY")
    print("=" * 55)
    summary_rows = []
    for k in ["exp1", "exp2", "exp3", "exp4", "exp5", "composite"]:
        if k in results and "signal_accuracy" in results[k]:
            r = results[k]
            edge = r["signal_accuracy"] - r.get("baseline_accuracy", 50)
            verdict = ("STRONG" if r["signal_accuracy"] > 56 else
                       "WEAK" if r["signal_accuracy"] > 52 else
                       "NOISE")
            summary_rows.append({
                "Experiment": r["experiment"],
                "Accuracy %": r["signal_accuracy"],
                "Baseline %": r.get("baseline_accuracy", "n/a"),
                "Edge %": round(edge, 2),
                "Signals": r["signal_count"],
                "Verdict": verdict,
            })

    summary_df = pd.DataFrame(summary_rows)
    print(summary_df.to_string(index=False))
    results["summary"] = summary_df

    print("\nDone. Access results['expN']['detail'] for row-level data.")
    print("Call plot_experiment(results, exp_number=2) to visualise.")
    print("Call plot_summary(results) for accuracy comparison chart.")

    return results


# ─────────────────────────────────────────────
#  QUICK SYNTHETIC TEST  — run this to verify
#  the file works before plugging real data
# ─────────────────────────────────────────────

def generate_synthetic_data(n_ticks: int = 500) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Generates synthetic order book + T&S data for testing.
    Plug your real CSVs in place of this.
    """
    np.random.seed(42)
    timestamps = pd.date_range("2024-01-15 09:15:00", periods=n_ticks, freq="s")

    price = 19450.0
    prices = []
    for _ in range(n_ticks):
        price += np.random.randn() * 0.3
        prices.append(round(price, 2))

    prices = np.array(prices)
    rows = []
    for i, (ts, lp) in enumerate(zip(timestamps, prices)):
        # Simulate some OBI signal correlated with future price
        future_drift = prices[min(i + 5, n_ticks - 1)] - lp
        obi_signal = np.clip(future_drift / 2 + np.random.randn() * 0.5, -1, 1)

        bid_base = lp - 0.25
        ask_base = lp + 0.25
        base_qty = 400

        row = {"timestamp": ts, "last_price": lp,
               "buy_limit_qty": int(base_qty * (1 + obi_signal) * 30),
               "sell_limit_qty": int(base_qty * (1 - obi_signal) * 30)}

        for j in range(1, 6):
            row[f"bid_p{j}"] = round(bid_base - (j - 1) * 0.25, 2)
            row[f"ask_p{j}"] = round(ask_base + (j - 1) * 0.25, 2)
            row[f"bid_q{j}"] = max(10, int(base_qty * (1 + obi_signal * 0.5) * (1 + np.random.randn() * 0.2)))
            row[f"ask_q{j}"] = max(10, int(base_qty * (1 - obi_signal * 0.5) * (1 + np.random.randn() * 0.2)))

        rows.append(row)

    ob_df = pd.DataFrame(rows)

    # Synthetic T&S
    tns_rows = []
    for i, (ts, lp) in enumerate(zip(timestamps, prices)):
        n_prints = np.random.randint(1, 4)
        for _ in range(n_prints):
            tns_rows.append({
                "timestamp": ts + pd.Timedelta(seconds=np.random.rand()),
                "price": lp + np.random.choice([-0.25, 0, 0.25]),
                "quantity": np.random.randint(10, 200),
            })

    tns_df = pd.DataFrame(tns_rows)
    return ob_df, tns_df


if __name__ == "__main__":
    print("Running with SYNTHETIC data. Replace with your real CSVs.")
    print("─" * 55)
    print("To use real data:")
    print("  ob_df  = pd.read_csv('your_ob_file.csv',  parse_dates=['timestamp'])")
    print("  tns_df = pd.read_csv('your_tns_file.csv', parse_dates=['timestamp'])")
    print("  results = run_all_experiments(ob_df, tns_df)")
    print("─" * 55 + "\n")

    ob_df, tns_df = generate_synthetic_data(n_ticks=600)
    results = run_all_experiments(ob_df, tns_df)

    # Save plots
    plot_summary(results, save_path="sentiment_summary.png")
    plot_experiment(results, exp_number=2, save_path="exp2_confluence.png")