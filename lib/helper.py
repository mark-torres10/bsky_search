"""Helper functions."""

from functools import wraps
from memory_profiler import memory_usage
import time
import threading


def track_performance(func):
    """Tracks both the runtime and memory usage of a function."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        mem_before = memory_usage(-1, interval=0.1, timeout=1)

        result = func(*args, **kwargs)

        end_time = time.time()
        mem_after = memory_usage(-1, interval=0.1, timeout=1)

        execution_time_seconds = round(end_time - start_time)
        execution_time_minutes = execution_time_seconds // 60
        execution_time_leftover_seconds = execution_time_seconds - (
            60 * execution_time_minutes
        )
        print(
            f"Execution time for {func.__name__}: {execution_time_minutes} minutes, {execution_time_leftover_seconds} seconds"
        )  # noqa
        print(
            f"Memory usage for {func.__name__}: {max(mem_after) - min(mem_before)} MB"
        )  # noqa
        return result

    return wrapper

class ThreadSafeCounter:
    def __init__(self):
        self.counter = 0
        self.lock = threading.Lock()

    def increment(self):
        with self.lock:
            self.counter += 1
            return self.counter

    def reset(self):
        with self.lock:
            self.counter = 0

    def get_value(self):
        with self.lock:
            return self.counter
