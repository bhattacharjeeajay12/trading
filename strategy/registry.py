"""
Strategy registry.

Every strategy listed in `strategies_list` (strategy/config.py) must map to a
concrete class here. The pipeline uses this dict to instantiate the strategies
that are marked `is_deployed=True`.

To add a new strategy:
    1. Subclass `Strategy` in a new file under strategy/.
    2. Set `self.name` to a unique string and implement `run(df)` that sets
       `self.signal` to 1 (BUY) or 0 (no signal).
    3. Add an entry to `strategies_list` in strategy/config.py declaring its
       aggregator and parameters.
    4. Import the class here and register it in `STRATEGY_REGISTRY` below.
"""

from strategy.MomentBasedStrategy import MomentBasedStrategy

STRATEGY_REGISTRY: dict = {
    "MomentBasedStrategy": MomentBasedStrategy,
}
