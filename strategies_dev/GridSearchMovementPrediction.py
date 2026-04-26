import pandas as pd
import numpy as np
from pathlib import Path
import itertools
from tqdm import tqdm
from dev.utils import resample_fractional_minute, apply_trailing_logic, generate_signal

# ==========================================
# 1. DEFINE ALL INPUTS & HYPERPARAMETERS
# ==========================================
date_ = "23APR2026"
file_names = ["NIFTY26APR24200CE.xlsx"]
base_dir = Path(fr"D:\Study\Programs\trading\assets\logs\{date_}\extracted_symbols")

# Hyperparameter Lists
N_list = [5, 6]
volume_period_list = [20]
window_list = [2, 3]
price_pct_threshold_list = [0.0, 0.001]
volume_threshold_list = [0]
use_price_pct_level_list = [True]
use_price_trend_list = [True]
use_volume_list = [False]

# N_list = [6]
# volume_period_list = [20]
# window_list = [3]
# price_pct_threshold_list = [0.002]
# volume_threshold_list = [1000]
# use_price_pct_level_list = [True]
# use_price_trend_list = [True]
# use_volume_list = [True]

# Static Trailing Parameters
params = {
    "initial_sl_pct": 0.02,
    "target_pct": 0.01,
    "trail_sl_pct": 0.02,
    "tight_sl_offset": 0.5,
}

# ==========================================
# 2. PRE-CALCULATE ITERATIONS FOR TQDM
# ==========================================
# We generate the signal grid once here so we can count its length
signal_combinations = list(itertools.product(
    window_list, price_pct_threshold_list, volume_threshold_list,
    use_price_pct_level_list, use_price_trend_list, use_volume_list
))

# Total runs = N * volume_period * signal_combinations
total_iterations_per_file = len(N_list) * len(volume_period_list) * len(signal_combinations)

all_trades_master = []
performance_master = []

# ==========================================
# 3. THE OPTIMIZED EXECUTION LOOP
# ==========================================
for file_name in file_names:
    print(f"\n--- Loading File: {file_name} ---")
    file_path = base_dir / file_name

    # [A] LOAD AND CLEAN BASE DATA
    df = pd.read_excel(file_path)

    if "PE" in file_name or "CE" in file_name:
        col_name = "last_trade_time"
        df["volume_at_tick"] = df["volume_traded"].diff()
        df.loc[df["volume_at_tick"] == 0, "volume_at_tick"] = np.nan
        df["volume_at_tick"] = df["volume_at_tick"].ffill()
        df["volume_at_tick"] = df["volume_at_tick"].fillna(0)
    else:
        col_name = "local_time"

    df[col_name] = pd.to_datetime(df[col_name])
    target_date = pd.to_datetime(date_).date()
    df = df[df[col_name].dt.date == target_date]
    df = df.sort_values(col_name)

    # Initialize the progress bar for this specific file
    with tqdm(total=total_iterations_per_file, desc=f"Testing {file_name}", unit="combo") as pbar:

        # [B] LEVEL 1 LOOP: Resampling
        for N in N_list:
            clubbed_df_N = resample_fractional_minute(df, col_name, N)
            clubbed_df_N["bucket_time_next"] = clubbed_df_N["bucket_time"].shift(-1)
            clubbed_df_N["price_diff"] = clubbed_df_N["close"] - clubbed_df_N["open"]
            clubbed_df_N["price_pct"] = (clubbed_df_N["close"] - clubbed_df_N["open"]) / clubbed_df_N["open"]
            clubbed_df_N["volume_diff"] = clubbed_df_N["volume_high"] - clubbed_df_N["volume_low"]
            clubbed_df_N["volume_participated"] = clubbed_df_N["volume_high"] - clubbed_df_N["volume_low"]
            clubbed_df_N["volume_pct"] = (clubbed_df_N["volume_close"] - clubbed_df_N["volume_open"]) / clubbed_df_N[
                "volume_open"]

            # [C] LEVEL 2 LOOP: Volume Rolling
            for vol_period in volume_period_list:
                clubbed_df_vp = clubbed_df_N.copy()
                clubbed_df_vp["volume_avg"] = clubbed_df_vp["volume_close"].rolling(vol_period).median()

                # [D] LEVEL 3 LOOP: Signal Generation
                for (window, p_thresh, v_thresh, u_lvl, u_trnd, u_vol) in signal_combinations:

                    clubbed_df_sig = generate_signal(
                        clubbed_df_vp.copy(),
                        window,
                        price_pct_threshold=p_thresh,
                        volume_threshold=v_thresh,
                        use_price_pct_level=u_lvl,
                        use_price_trend=u_trnd,
                        use_volume=u_vol
                    )

                    clubbed_df_sig = clubbed_df_sig.sort_values("bucket_time")
                    df_with_signal = pd.merge_asof(
                        df,
                        clubbed_df_sig[["bucket_time", "predicted"]],
                        left_on=col_name,
                        right_on="bucket_time",
                        direction="backward"
                    )
                    df_with_signal.loc[
                        df_with_signal.duplicated(subset=['bucket_time'], keep='first'), 'predicted'] = np.nan

                    trades = apply_trailing_logic(df_with_signal, params)

                    current_hyperparams = {
                        "file_name": file_name,
                        "N": N,
                        "volume_period": vol_period,
                        "window": window,
                        "price_pct_threshold": p_thresh,
                        "volume_threshold": v_thresh,
                        "use_price_pct_level": u_lvl,
                        "use_price_trend": u_trnd,
                        "use_volume": u_vol
                    }

                    if len(trades) > 0:
                        trades_df = pd.DataFrame(trades)
                        trades_df["final"] = trades_df.apply(lambda row: "profit" if row["profit"] > 0 else "loss",
                                                             axis=1)

                        for key, val in current_hyperparams.items():
                            trades_df[key] = val

                        all_trades_master.append(trades_df)

                        wins = len(trades_df[trades_df["final"] == "profit"])
                        total_trades = len(trades_df)
                        win_rate = round(wins / total_trades, 2)
                        total_profit = trades_df["profit"].sum()

                        perf_record = {**current_hyperparams, "total_trades": total_trades, "win_rate": win_rate,
                                       "total_profit": total_profit}
                        performance_master.append(perf_record)
                    else:
                        perf_record = {**current_hyperparams, "total_trades": 0, "win_rate": 0.0, "total_profit": 0.0}
                        performance_master.append(perf_record)

                    # Update the progress bar by 1 after each combo completes
                    pbar.update(1)

# ==========================================
# 4. FINALIZE AND EXPORT DATAFRAMES
# ==========================================
if all_trades_master:
    final_trades_df = pd.concat(all_trades_master, ignore_index=True)
    cols = ['entry_time', 'entry_price', 'exit_time', 'exit_price', 'profit', 'profit_pct', 'final']
    other_cols = [c for c in final_trades_df.columns if c not in cols]
    final_trades_df = final_trades_df[cols + other_cols]

    final_trades_df.to_csv("all_hyperparameter_trades.csv", index=False)
    print("\n[+] Saved detailed trades to 'all_hyperparameter_trades.csv'")
else:
    print("\n[-] No trades were generated across any hyperparameter combination.")

final_performance_df = pd.DataFrame(performance_master)
final_performance_df = final_performance_df.sort_values(by="total_profit", ascending=False)
final_performance_df.to_csv("hyperparameter_performance.csv", index=False)
print("[+] Saved performance summary to 'hyperparameter_performance.csv'")