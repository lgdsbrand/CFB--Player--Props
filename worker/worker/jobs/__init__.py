"""Job entrypoints, each runnable as `python -m worker.jobs.<name>`."""

from worker import time_limit

# Here rather than in each job's main(), so no job can be added without it.
# A no-op off Render and under pytest; see worker.time_limit.
time_limit.arm()
