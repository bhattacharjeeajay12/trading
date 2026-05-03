import argparse
import itertools
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
import ast
import numpy as np
import pandas as pd

import speed_analysis_config as cfg


@dataclass(frozen=True)
class ParameterSet:
    window_seconds: int
    speed_threshold: float
    acceleration_threshold: float
    target_points: float
    stop_loss_points: float
    max_holding_seconds: int


REQUIRED_COLUMNS = list(cfg.REQUIRED_COLUMNS)
SIGNAL_OUTPUT_COLUMNS = [
    "entry_index",
    "entry_time",
    "entry_price",
    "direction",
    "speed_points_per_sec",
    "acceleration_points_per_sec2",
    "exit_time",
    "exit_price",
    "outcome",
    "is_success",
    "signed_move_points",
    "holding_seconds",
    "window_seconds",
    "speed_threshold",
    "acceleration_threshold",
    "target_points",
    "stop_loss_points",
    "max_holding_seconds",
]

def _safe_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    denominator = denominator.replace(0, np.nan)
    return numerator / denominator


def _normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(col).strip().lower() for col in df.columns]
    return df

def flatten_depth(row):
    depth = row['depth']
    data = {}

    for side, prefix in [('buy', 'bid'), ('sell', 'ask')]:
        for i in range(5):
            entry = depth[side][i] if i < len(depth[side]) else {}
            data[f'{prefix}_price_{i+1}'] = entry.get('price')
            data[f'{prefix}_qty_{i+1}']   = entry.get('quantity')
    return pd.Series(data)


def update_volume(row):
    """
    Since volume_traded is cumulative, each tick alone doesn't convey the per-tick volume.
    This function returns the per-tick delta.
    """
    if row['volume_traded'] == row['volume_traded_prev']:
        return np.nan
    return row['volume_traded'] - row['volume_traded_prev']

def load_and_clean_ticks(input_path: Path) -> pd.DataFrame:
    """Load one option tick file and enforce the expected analysis schema."""
    df = pd.read_excel(input_path)
    df['last_trade_time'] = pd.to_datetime(df['last_trade_time'])
    df = df.sort_values('last_trade_time').reset_index(drop=True)

    if 'depth' in df.columns:
        df['depth'] = df.apply(lambda row: ast.literal_eval(row['depth']), axis=1)
        df = df.join(df.apply(flatten_depth, axis=1, result_type='expand'))

    if 'volume_traded' in df.columns:
        df['volume_traded_prev'] = df['volume_traded'].shift(1)
        df['volume_traded'] = df.apply(lambda row: update_volume(row), axis=1)
        df['volume_traded'] = df['volume_traded'].ffill()
    df = df.drop(columns=['depth'])
    df = _normalise_columns(df)
    print(df.columns)
    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Input file is missing required columns: {missing}")

    df["_source_row"] = np.arange(len(df)) + 2
    df = df.drop_duplicates().copy()

    df["last_trade_time"] = pd.to_datetime(df["last_trade_time"], errors="coerce")
    try:
        if df["last_trade_time"].dt.tz is not None:
            df["last_trade_time"] = df["last_trade_time"].dt.tz_localize(None)
    except AttributeError:
        df["last_trade_time"] = pd.to_datetime(df["last_trade_time"].astype(str), errors="coerce")

    numeric_columns = [col for col in REQUIRED_COLUMNS if col != "last_trade_time"]
    for col in numeric_columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["last_trade_time", "last_price"]).copy()
    df = df.sort_values(["last_trade_time", "_source_row"], kind="mergesort").reset_index(drop=True)

    df["volume_delta"] = df["volume_traded"].diff()
    df.loc[df["volume_delta"] < 0, "volume_delta"] = np.nan
    df["volume_delta"] = df["volume_delta"].fillna(0)

    df["best_bid_ask_mid"] = (df["bid_price_1"] + df["ask_price_1"]) / 2
    df["bid_ask_spread"] = df["ask_price_1"] - df["bid_price_1"]
    df["spread_pct_mid"] = _safe_ratio(df["bid_ask_spread"], df["best_bid_ask_mid"])

    df["best_qty_imbalance"] = _safe_ratio(
        df["bid_qty_1"] - df["ask_qty_1"],
        df["bid_qty_1"] + df["ask_qty_1"],
    )

    bid_qty_cols = [f"bid_qty_{i}" for i in range(1, 6)]
    ask_qty_cols = [f"ask_qty_{i}" for i in range(1, 6)]
    df["top5_bid_qty"] = df[bid_qty_cols].sum(axis=1)
    df["top5_ask_qty"] = df[ask_qty_cols].sum(axis=1)
    df["top5_qty_imbalance"] = _safe_ratio(
        df["top5_bid_qty"] - df["top5_ask_qty"],
        df["top5_bid_qty"] + df["top5_ask_qty"],
    )
    df["total_qty_imbalance"] = _safe_ratio(
        df["total_buy_quantity"] - df["total_sell_quantity"],
        df["total_buy_quantity"] + df["total_sell_quantity"],
    )

    return df


def build_analysis_frame(clean_df: pd.DataFrame, window_seconds: int) -> pd.DataFrame:
    if window_seconds == 0:
        analysis = clean_df.copy()
        analysis["analysis_time"] = analysis["last_trade_time"]
        analysis["window_seconds"] = 0
        analysis["open"] = analysis["last_price"]
        analysis["high"] = analysis["last_price"]
        analysis["low"] = analysis["last_price"]
        analysis["close"] = analysis["last_price"]
        analysis["tick_count"] = 1
    else:
        work = clean_df.copy()
        work["bucket_time"] = work["last_trade_time"].dt.floor(f"{window_seconds}s")
        analysis = (
            work.groupby("bucket_time", as_index=False)
            .agg(
                analysis_start=("last_trade_time", "first"),
                analysis_time=("last_trade_time", "last"),
                open=("last_price", "first"),
                high=("last_price", "max"),
                low=("last_price", "min"),
                close=("last_price", "last"),
                tick_count=("last_price", "size"),
                volume_delta=("volume_delta", "sum"),
                total_buy_quantity=("total_buy_quantity", "last"),
                total_sell_quantity=("total_sell_quantity", "last"),
                bid_ask_spread=("bid_ask_spread", "mean"),
                spread_pct_mid=("spread_pct_mid", "mean"),
                best_qty_imbalance=("best_qty_imbalance", "mean"),
                top5_qty_imbalance=("top5_qty_imbalance", "mean"),
                total_qty_imbalance=("total_qty_imbalance", "mean"),
                bid_price_1=("bid_price_1", "last"),
                ask_price_1=("ask_price_1", "last"),
            )
            .sort_values("analysis_time")
            .reset_index(drop=True)
        )
        analysis["window_seconds"] = window_seconds

    analysis["elapsed_seconds"] = analysis["analysis_time"].diff().dt.total_seconds()
    analysis.loc[analysis["elapsed_seconds"] <= 0, "elapsed_seconds"] = np.nan

    analysis["price_delta"] = analysis["close"].diff()
    analysis["price_pct_delta"] = analysis["close"].pct_change()
    analysis["log_price_delta"] = np.log(analysis["close"]).diff()

    analysis["speed_points_per_sec"] = analysis["price_delta"] / analysis["elapsed_seconds"]
    analysis["speed_pct_per_sec"] = analysis["price_pct_delta"] / analysis["elapsed_seconds"]
    analysis["speed_log_per_sec"] = analysis["log_price_delta"] / analysis["elapsed_seconds"]

    analysis["speed_delta"] = analysis["speed_points_per_sec"].diff()
    analysis["acceleration_points_per_sec2"] = analysis["speed_delta"] / analysis["elapsed_seconds"]

    eps = cfg.DIRECTION_SPEED_EPSILON
    analysis["direction"] = np.select(
        [
            analysis["speed_points_per_sec"] > eps,
            analysis["speed_points_per_sec"] < -eps,
        ],
        ["up", "down"],
        default="undetermined",
    )

    return analysis


def generate_signals(features: pd.DataFrame, params: ParameterSet) -> pd.DataFrame:
    df = features.copy()
    speed = df["speed_points_per_sec"]
    acceleration = df["acceleration_points_per_sec2"].fillna(0)

    base_signal = (
        df["direction"].isin(["up", "down"])
        & speed.abs().ge(params.speed_threshold)
        & acceleration.abs().ge(params.acceleration_threshold)
    )

    if cfg.REQUIRE_ACCELERATION_ALIGNMENT and params.acceleration_threshold > 0:
        aligned = np.sign(speed).fillna(0).eq(np.sign(acceleration).fillna(0))
        base_signal = base_signal & aligned

    df["signal"] = np.where(base_signal, df["direction"], "none")
    df["signal_side"] = df["signal"].map({"up": 1, "down": -1}).fillna(0).astype(int)
    return df


def _first_outcome(
    future: pd.DataFrame,
    entry_price: float,
    side: int,
    target_points: float,
    stop_loss_points: float,
) -> Tuple[str, Optional[pd.Timestamp], Optional[float]]:
    if side == 1:
        target_price = entry_price + target_points
        stop_price = entry_price - stop_loss_points
        target_hit = future["close"] >= target_price
        stop_hit = future["close"] <= stop_price
    else:
        target_price = entry_price - target_points
        stop_price = entry_price + stop_loss_points
        target_hit = future["close"] <= target_price
        stop_hit = future["close"] >= stop_price

    for idx, row in future.iterrows():
        if bool(target_hit.loc[idx]):
            return "target", row["analysis_time"], float(row["close"])
        if bool(stop_hit.loc[idx]):
            return "stop_loss", row["analysis_time"], float(row["close"])

    return "timeout", None, None


def label_signals(signal_df: pd.DataFrame, params: ParameterSet) -> pd.DataFrame:
    rows = []
    df = signal_df.reset_index(drop=True)

    for idx, row in df[df["signal_side"] != 0].iterrows():
        entry_time = row["analysis_time"]
        cutoff_time = entry_time + pd.Timedelta(seconds=params.max_holding_seconds)
        future = df.loc[(df.index > idx) & (df["analysis_time"] <= cutoff_time)]

        outcome, exit_time, exit_price = _first_outcome(
            future=future,
            entry_price=float(row["close"]),
            side=int(row["signal_side"]),
            target_points=params.target_points,
            stop_loss_points=params.stop_loss_points,
        )

        if exit_time is None:
            exit_time = future["analysis_time"].iloc[-1] if not future.empty else entry_time
            exit_price = float(future["close"].iloc[-1]) if not future.empty else float(row["close"])

        signed_move = (float(exit_price) - float(row["close"])) * int(row["signal_side"])
        rows.append(
            {
                "entry_index": idx,
                "entry_time": entry_time,
                "entry_price": float(row["close"]),
                "direction": row["direction"],
                "speed_points_per_sec": row["speed_points_per_sec"],
                "acceleration_points_per_sec2": row["acceleration_points_per_sec2"],
                "exit_time": exit_time,
                "exit_price": float(exit_price),
                "outcome": outcome,
                "is_success": outcome == "target",
                "signed_move_points": signed_move,
                "holding_seconds": (exit_time - entry_time).total_seconds(),
                **asdict(params),
            }
        )

    return pd.DataFrame(rows, columns=SIGNAL_OUTPUT_COLUMNS)


def score_parameter_set(trades: pd.DataFrame, total_rows: int, params: ParameterSet) -> Dict[str, float]:
    signal_count = len(trades)
    success_count = int(trades["is_success"].sum()) if signal_count else 0
    false_count = signal_count - success_count
    precision = success_count / signal_count if signal_count else 0.0
    recall_proxy = success_count / total_rows if total_rows else 0.0
    f1_proxy = (
        2 * precision * recall_proxy / (precision + recall_proxy)
        if precision + recall_proxy > 0
        else 0.0
    )
    avg_signed_move = float(trades["signed_move_points"].mean()) if signal_count else 0.0
    segment_precisions = _segment_precisions(trades)
    min_segment_precision = min(segment_precisions) if segment_precisions else 0.0

    return {
        **asdict(params),
        "analysis_rows": total_rows,
        "signal_count": signal_count,
        "success_count": success_count,
        "false_count": false_count,
        "precision": precision,
        "recall_proxy": recall_proxy,
        "f1_proxy": f1_proxy,
        "avg_signed_move_points": avg_signed_move,
        "min_segment_precision": min_segment_precision,
        "segment_precisions": ",".join(f"{value:.4f}" for value in segment_precisions),
        "rank_score": f1_proxy if signal_count >= cfg.MIN_SIGNALS_FOR_RANKING else 0.0,
    }


def _segment_precisions(trades: pd.DataFrame, segments: int = 3) -> List[float]:
    if trades.empty:
        return []

    ordered = trades.sort_values("entry_time").reset_index(drop=True)
    split_indexes = np.array_split(np.arange(len(ordered)), segments)
    chunks = [ordered.iloc[indexes] for indexes in split_indexes if len(indexes) > 0]
    return [float(chunk["is_success"].mean()) for chunk in chunks]


def parameter_grid(grid: Dict[str, Iterable]) -> Iterable[ParameterSet]:
    keys = [
        "window_seconds",
        "speed_threshold",
        "acceleration_threshold",
        "target_points",
        "stop_loss_points",
        "max_holding_seconds",
    ]
    for values in itertools.product(*(grid[key] for key in keys)):
        yield ParameterSet(**dict(zip(keys, values)))


def run_playback(clean_df: pd.DataFrame, params: ParameterSet) -> Dict[str, pd.DataFrame]:
    features = build_analysis_frame(clean_df, params.window_seconds)
    signals = generate_signals(features, params)
    trades = label_signals(signals, params)
    summary = pd.DataFrame([score_parameter_set(trades, len(signals), params)])
    return {
        "cleaned_input": clean_df,
        "features": signals,
        "signals": trades,
        "optimization_summary": summary,
        "playback_report": trades,
    }


def run_optimize(clean_df: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    summaries: List[Dict[str, float]] = []
    all_signal_rows: List[pd.DataFrame] = []

    for params in parameter_grid(cfg.OPTIMIZE_GRID):
        features = build_analysis_frame(clean_df, params.window_seconds)
        signals = generate_signals(features, params)
        trades = label_signals(signals, params)

        summaries.append(score_parameter_set(trades, len(signals), params))
        if not trades.empty:
            all_signal_rows.append(trades)

    summary = pd.DataFrame(summaries).sort_values(
        ["rank_score", "min_segment_precision", "precision", "signal_count"],
        ascending=[False, False, False, False],
    )
    best_params = _params_from_dict(summary.iloc[0].to_dict())
    best_features = generate_signals(
        build_analysis_frame(clean_df, best_params.window_seconds),
        best_params,
    )
    best_trades = label_signals(best_features, best_params)

    signal_details = (
        pd.concat(all_signal_rows, ignore_index=True)
        if all_signal_rows
        else pd.DataFrame(columns=SIGNAL_OUTPUT_COLUMNS)
    )

    return {
        "cleaned_input": clean_df,
        "features": best_features,
        "signals": signal_details,
        "optimization_summary": summary,
        "playback_report": best_trades,
    }


def write_report(sheets: Dict[str, pd.DataFrame], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    max_rows = cfg.MAX_ROWS_PER_EXCEL_SHEET

    with pd.ExcelWriter(output_path) as writer:
        for sheet_name, df in sheets.items():
            out = df.copy()
            if max_rows is not None and len(out) > max_rows:
                out = out.head(max_rows)
            out.to_excel(writer, sheet_name=sheet_name[:31], index=False)


def _params_from_dict(values: Dict[str, object]) -> ParameterSet:
    return ParameterSet(
        window_seconds=int(values["window_seconds"]),
        speed_threshold=float(values["speed_threshold"]),
        acceleration_threshold=float(values["acceleration_threshold"]),
        target_points=float(values["target_points"]),
        stop_loss_points=float(values["stop_loss_points"]),
        max_holding_seconds=int(values["max_holding_seconds"]),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Speed analysis for option tick data.")
    parser.add_argument("--mode", choices=["optimize", "playback"], default=cfg.MODE)
    parser.add_argument("--input", type=Path, default=cfg.INPUT_EXCEL_FILE)
    parser.add_argument("--output", type=Path, default=cfg.OUTPUT_EXCEL_FILE)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    clean_df = load_and_clean_ticks(args.input)

    if args.mode == "optimize":
        sheets = run_optimize(clean_df)
    else:
        sheets = run_playback(clean_df, _params_from_dict(cfg.PLAYBACK_PARAMS))

    write_report(sheets, args.output)
    print(f"Completed {args.mode} mode. Report written to: {args.output}")


if __name__ == "__main__":
    main()
