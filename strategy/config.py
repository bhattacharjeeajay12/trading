"""
Static configuration for strategies, their data-aggregation requirements, and
stop-loss defaults.

Two registries are declared here:

1. `aggregators_list` -- Catalogue of available data-aggregation mechanisms.
   Each key MUST also be registered in `AGGREGATOR_REGISTRY` (AggregatedData.py)
   so the pipeline can instantiate it.

2. `strategies_list` -- Catalogue of strategies. Each strategy entry declares:
       - `aggregator`         -> which aggregator it consumes (key in `aggregators_list`)
       - `aggregator_params`  -> params passed to that aggregator's constructor
       - `strategy_params`    -> params consumed by the strategy itself
       - `is_deployed`        -> True to include at runtime, False to disable
   Each key MUST also be registered in `STRATEGY_REGISTRY` (strategy/registry.py).

Evaluation order:
   At runtime, deployed strategies are evaluated in the *insertion order* of
   this dict on every tick. The FIRST strategy to return signal==1 places the
   trade; remaining strategies are skipped for that tick. Re-order the keys
   below to change priority.
"""

aggregators_list = {
    "OHLCAggregator": {
        "description": (
            "Time-bucketed OHLC candles. bucket_size_sec = 60 // fraction. "
            "Produced columns: bucket_time, open, high, low, close."
        ),
        "default_params": {"fraction": 6},
    },
    # Future:
    # "TickWindowAggregator": {
    #     "description": "Rolling window of last N raw ticks.",
    #     "default_params": {"window_ticks": 100},
    # },
}


strategies_list = {
    "MomentBasedStrategy": {
        "strategy_name": "MomentBasedStrategy",
        "aggregator": "OHLCAggregator",
        "aggregator_params": {"fraction": 6},
        "strategy_params": {
            "window": 3,
            "price_pct_threshold": 0.00001,
        },
        "is_deployed": True,
    },
    # Example of how a future strategy with a *different* aggregator is added:
    # "VolumeSpikeStrategy": {
    #     "strategy_name": "VolumeSpikeStrategy",
    #     "aggregator": "TickWindowAggregator",
    #     "aggregator_params": {"window_ticks": 100},
    #     "strategy_params": {"volume_multiplier": 3.0},
    #     "is_deployed": False,
    # },
}


stoploss_dict = {
    "stop_loss_pct": 0.98,
    "new_stop_loss_pct": 0.99,
}
