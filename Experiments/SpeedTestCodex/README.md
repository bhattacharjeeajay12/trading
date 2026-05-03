# SpeedTestCodex

Fresh implementation of the speed-analysis framework for one option instrument
and one trading day.

## Files

- `speed_analysis_config.py`: all editable inputs, mode selection, grid ranges,
  playback parameters, and report settings.
- `speed_analysis.py`: standalone runner for loading ticks, calculating speed
  and acceleration, labeling target-before-stop outcomes, optimizing parameters,
  and writing Excel reports.
- `requirements.txt`: local dependencies for this experiment.

## Input Columns

The Excel input must contain these columns:

```text
last_price, last_trade_time, total_buy_quantity, total_sell_quantity,
volume_traded,
bid_price_1, bid_qty_1, bid_price_2, bid_qty_2, bid_price_3, bid_qty_3,
bid_price_4, bid_qty_4, bid_price_5, bid_qty_5,
ask_price_1, ask_qty_1, ask_price_2, ask_qty_2, ask_price_3, ask_qty_3,
ask_price_4, ask_qty_4, ask_price_5, ask_qty_5
```

Column names are matched case-insensitively after trimming spaces.

## Modes

### Optimize

Set `MODE = "optimize"` in `speed_analysis_config.py`, then run:

```bash
python speed_analysis.py
```

This tests every combination in `OPTIMIZE_GRID` and ranks parameter sets by
prediction-quality metrics.

### Playback

Set `MODE = "playback"` and edit `PLAYBACK_PARAMS`, then run:

```bash
python speed_analysis.py
```

This runs one parameter set and writes a detailed signal-by-signal report.

You can also override paths and mode from the command line:

```bash
python speed_analysis.py --mode optimize --input path/to/ticks.xlsx --output path/to/report.xlsx
python speed_analysis.py --mode playback --input path/to/ticks.xlsx --output path/to/report.xlsx
```

## Output Sheets

- `cleaned_input`: validated, sorted input with liquidity helper columns.
- `features`: raw or windowed speed, acceleration, direction, and signal columns.
- `signals`: each generated signal with target/stop/timeout outcome.
- `optimization_summary`: ranked grid-search results.
- `playback_report`: detailed signal rows for playback mode.

## Notes

- `window_seconds = 0` means raw tick mode.
- Other window values group ticks into second-based windows using the last price
  as the window close.
- Speed is calculated using elapsed seconds, not row count.
- A successful momentum label means target is hit before stop-loss within the
  configured max holding time.
- One-day optimization is exploratory. Re-test promising parameters across more
  days before trusting them.
