"""pytest plugin: record which non-Python repo files each test opens (fixtures)."""
import json, os, sys
ROOT = os.getcwd()
_cur = [None]
_map = {}
def _hook(event, args):
    if event == "open" and _cur[0] is not None and args and isinstance(args[0], (str, bytes, os.PathLike)):
        try:
            p = os.path.abspath(os.fsdecode(args[0]))
        except Exception:
            return
        if p.startswith(ROOT + os.sep) and not p.endswith((".py", ".pyc")):
            _map.setdefault(_cur[0], set()).add(os.path.relpath(p, ROOT))
sys.addaudithook(_hook)
import pytest
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    _cur[0] = item.nodeid
    yield
    _cur[0] = None
def pytest_sessionfinish(session):
    out = os.environ.get("OPENMAP_OUT", "openmap.json")
    with open(out, "w") as f:
        json.dump({k: sorted(v) for k, v in _map.items()}, f)
