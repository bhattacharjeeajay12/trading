class DataDecisionPipeline:
    """
    This class has a hardcoded plan though which each tick is passed. The DataDecisionPipeline act as the orchestrator between these modules DataKeeper, DataAggregator, Strategies and StopLoss.
    """

    def __init__(self, tick_serializable):
        self.tick_serializable = tick_serializable