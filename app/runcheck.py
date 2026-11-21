"""Run the given sqlglot tests (unittest ids on stdin) against the patched copy on PYTHONPATH.
Stops at the first failure. Prints {"passed": bool}. Any error at all counts as not passed."""
import json, os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    ids = json.loads(sys.stdin.read())
    try:
        suite = unittest.TestSuite(unittest.TestLoader().loadTestsFromName(i) for i in ids)
        import syncpool
        for name in ('tests.test_optimizer', 'tests.test_executor'):
            m = sys.modules.get(name)
            if m is not None and hasattr(m, 'ProcessPoolExecutor'):
                m.ProcessPoolExecutor = syncpool.SyncExecutor
        with open(os.devnull, 'w') as devnull:
            r = unittest.TextTestRunner(stream=devnull, failfast=True, verbosity=0).run(suite)
        ok = r.wasSuccessful() and r.testsRun == len(ids)
    except BaseException:
        ok = False
    sys.stdout.write(json.dumps({'passed': bool(ok)}) + '\n')


if __name__ == '__main__':
    main()
