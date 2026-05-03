from pathlib import Path


# -----------------------------
# Runtime mode
# -----------------------------
# Supported values:
# - "optimize": test parameter combinations and rank them by prediction quality.
# - "playback": run one selected parameter set and export detailed diagnostics.
MODE = "optimize"


# -----------------------------
# File paths
# -----------------------------
# Point INPUT_EXCEL_FILE to one option-instrument Excel file for one trading day.
INPUT_EXCEL_FILE = Path("input_ticks.xlsx")
OUTPUT_EXCEL_FILE = Path("speed_analysis_output.xlsx")


# -----------------------------
# Required input schema
# -----------------------------
REQUIRED_COLUMNS = [
    "last_price",
    "last_trade_time",
    "total_buy_quantity",
    "total_sell_quantity",
    "volume_traded",
    "bid_price_1",
    "bid_qty_1",
    "bid_price_2",
    "bid_qty_2",
    "bid_price_3",
    "bid_qty_3",
    "bid_price_4",
    "bid_qty_4",
    "bid_price_5",
    "bid_qty_5",
    "ask_price_1",
    "ask_qty_1",
    "ask_price_2",
    "ask_qty_2",
    "ask_price_3",
    "ask_qty_3",
    "ask_price_4",
    "ask_qty_4",
    "ask_price_5",
    "ask_qty_5",
]


# -----------------------------
# Optimization grid
# -----------------------------
# window_seconds = 0 means raw tick mode. Other values mean second-based windows.
OPTIMIZE_GRID = {
    "window_seconds": [0, 5, 10, 15, 30],
    "speed_threshold": [0.05, 0.10, 0.20, 0.30],
    "acceleration_threshold": [0.00, 0.02, 0.05],
    "target_points": [1.0, 2.0, 3.0],
    "stop_loss_points": [1.0, 2.0],
    "max_holding_seconds": [30, 60, 120],
}


# -----------------------------
# Playback parameters
# -----------------------------
# Used only when MODE = "playback".
PLAYBACK_PARAMS = {
    "window_seconds": 5,
    "speed_threshold": 0.10,
    "acceleration_threshold": 0.02,
    "target_points": 2.0,
    "stop_loss_points": 1.0,
    "max_holding_seconds": 60,
}


# -----------------------------
# Signal and report settings
# -----------------------------
# Tiny speed values are treated as no direction to avoid classifying noise.
DIRECTION_SPEED_EPSILON = 0.000001

# Require acceleration to have the same sign as speed when acceleration threshold
# is greater than zero. This keeps "fast but decelerating" ticks out of signals.
REQUIRE_ACCELERATION_ALIGNMENT = True

# Minimum number of generated signals required before a parameter set is ranked.
MIN_SIGNALS_FOR_RANKING = 3

# Large raw files can make Excel exports heavy. Set to None for all rows.
MAX_ROWS_PER_EXCEL_SHEET = 500_000
