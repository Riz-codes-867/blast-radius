"""pytest plugin for map building only: run ProcessPoolExecutor work in-process
so per-test coverage sees it. Arguments and results are round-tripped through
pickle, exactly as a real process boundary would (that exercises sqlglot.serde)."""
import pickle
from concurrent.futures import Future
class SyncExecutor:
    def __init__(self, *a, initializer=None, initargs=(), **k):
        if initializer is not None:
            initializer(*pickle.loads(pickle.dumps(initargs)))
    def submit(self, fn, *args, **kwargs):
        f = Future()
        try:
            fn, args, kwargs = pickle.loads(pickle.dumps((fn, args, kwargs)))
            f.set_result(pickle.loads(pickle.dumps(fn(*args, **kwargs))))
        except BaseException as e: f.set_exception(e)
        return f
    def map(self, fn, *its, **k): return [self.submit(fn, *a).result() for a in zip(*its)]
    def shutdown(self, *a, **k): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False
def pytest_collection_finish(session):
    import sys
    for name in ("tests.test_optimizer", "tests.test_executor"):
        m = sys.modules.get(name)
        if m is not None and hasattr(m, "ProcessPoolExecutor"):
            m.ProcessPoolExecutor = SyncExecutor
