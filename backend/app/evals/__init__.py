"""
Sensibility checks on every agent run (HOW_IT_WORKS.md §3): did the agent take the expected path
(trajectory), and is its answer sensible (output). Code only, read from the run's trace, recorded back on it.
Not an accuracy measure. Each agent's checks are in its own module; runner.py runs them at the end of a run.
"""
