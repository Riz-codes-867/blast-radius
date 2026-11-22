"""pytest plugin for map building: record which non-Python repo files each test opens (fixtures).
Files opened during a test's setup phase (setUpClass / setUp run there) are shared by its whole class,
so they are credited to every test of that class, not just the one that happened to run first."""
import json, os, sys
ROOT = os.getcwd()
_cur = [None]; _phase = [None]
_map = {}; _class_setup = {}
def _hook(event, args):
    if event == "open" and _cur[0] is not None and args and isinstance(args[0], (str, bytes, os.PathLike)):
        try:
            p = os.path.abspath(os.fsdecode(args[0]))
        except Exception:
            return
        if p.startswith(ROOT + os.sep) and not p.endswith((".py", ".pyc")):
            rel = os.path.relpath(p, ROOT)
            _map.setdefault(_cur[0], set()).add(rel)
            if _phase[0] == "setup":
                _class_setup.setdefault(_cur[0].rsplit("::", 1)[0], set()).add(rel)
sys.addaudithook(_hook)
import pytest
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    _cur[0] = item.nodeid
    yield
    _cur[0] = None
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_setup(item):
    _phase[0] = "setup"; yield; _phase[0] = None
def pytest_collection_modifyitems(session, config, items):
    global _ALL
    _ALL = [i.nodeid for i in items]
def pytest_sessionfinish(session):
    # reads made during setup are reported per class; build_data decides which of the class's tests get them
    direct = {k: sorted(v - _class_setup.get(k.rsplit("::", 1)[0], set())) for k, v in _map.items()}
    out = os.environ.get("OPENMAP_OUT", "openmap.json")
    with open(out, "w") as f:
        json.dump({"tests": direct, "class_setup": {k: sorted(v) for k, v in _class_setup.items()},
                   "all": _ALL}, f)
