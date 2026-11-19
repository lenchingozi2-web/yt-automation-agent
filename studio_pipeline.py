"""Bounded, ordered scene encoding; footage selection stays on the main thread."""
from __future__ import annotations

import os
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager


def render_parallelism():
    cores = os.cpu_count() or 2
    try:
        requested = int(os.getenv("STUDIO_RENDER_PARALLELISM", "2" if cores >= 4 else "1"))
    except ValueError:
        requested = 1
    return min(max(1, requested), max(1, min(2, cores // 2)))


class OrderedScenePipeline:
    def __init__(self, executor, workers):
        self.executor = executor
        self.capacity = workers + 1
        self.pending = deque()

    def submit(self, render, metadata, *args, **kwargs):
        if len(self.pending) >= self.capacity:
            raise RuntimeError("Scene pipeline capacity exceeded")
        self.pending.append((self.executor.submit(render, *args, **kwargs), metadata))

    def full(self):
        return len(self.pending) >= self.capacity

    def pop(self):
        future, metadata = self.pending.popleft()
        return future.result(), metadata

    def drain(self):
        while self.pending:
            yield self.pop()


@contextmanager
def scene_pipeline(workers):
    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="studio-scene")
    pipeline = OrderedScenePipeline(executor, workers)
    try:
        yield pipeline
    finally:
        # Never leave FFmpeg writing files after a render attempt has failed.
        executor.shutdown(wait=True, cancel_futures=True)
