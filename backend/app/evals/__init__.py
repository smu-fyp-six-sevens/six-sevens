"""
Sensibility checks on every agent run (CLOUD_ARCHITECTURE_PLAN.md §13): did the agent take the expected path
(trajectory), and is its answer sensible (output). Code only, read from the run's trace, recorded back on it.
Not an accuracy measure. Each agent's checks are in its own module; runner.py runs them at the end of a run.
"""
