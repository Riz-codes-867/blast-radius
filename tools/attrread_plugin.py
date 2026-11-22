"""pytest plugin for map building: for test classes that load files in setUpClass, record which class
attributes each test actually reads at run time (so a fixture loaded into one attribute is credited only
to tests that really read that attribute, not to every test that could reach it through a shared helper)."""
import json, os
_cur = [None]; _reads = {}
_hooked = set()
def _hook_class(cls):
    if cls in _hooked: return
    _hooked.add(cls)
    names = {k for k, v in vars(cls).items() if not callable(v) and not k.startswith('__')}
    orig = cls.__getattribute__
    def ga(self, name, _orig=orig, _names=names):
        if name in _names and _cur[0] is not None:
            _reads.setdefault(_cur[0], set()).add(name)
        return _orig(self, name)
    cls.__getattribute__ = ga
import pytest
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    _cur[0] = item.nodeid
    yield
    _cur[0] = None
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    cls = getattr(item, 'cls', None)
    if cls is not None and 'setUpClass' in vars(cls): _hook_class(cls)
    yield
def pytest_sessionfinish(session):
    json.dump({k: sorted(v) for k, v in _reads.items()}, open(os.environ.get('ATTRREAD_OUT', 'attrreads.json'), 'w'))
