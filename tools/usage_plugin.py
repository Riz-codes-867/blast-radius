"""pytest plugin for map building only: record, per test, which sqlglot Dialect / Tokenizer / Parser /
Generator / TypeAnnotator classes (with their whole MRO) the test actually uses. Class-level settings
are read through these objects, so this tells us which tests a class-level edit can reach."""
import json, os
_cur = [None]; _used = {}
def _note(obj):
    t = _cur[0]
    if t is None: return
    s = _used.setdefault(t, set())
    for k in (type(obj),) + ((type(obj.dialect),) if getattr(obj, 'dialect', None) is not None else ()):
        for c in k.__mro__:
            if c.__module__.startswith('sqlglot'): s.add(f'{c.__module__}:{c.__qualname__}')
def _wrap(cls, name):
    orig = getattr(cls, name)
    def w(self, *a, **k):
        _note(self); return orig(self, *a, **k)
    w.__wrapped__ = orig; setattr(cls, name, w)
def pytest_sessionstart(session):
    from sqlglot.tokens import Tokenizer
    from sqlglot.parser import Parser
    from sqlglot.generator import Generator
    from sqlglot.dialects.dialect import Dialect
    from sqlglot.optimizer.annotate_types import TypeAnnotator
    for cls, names in ((Tokenizer, ('tokenize',)), (Parser, ('parse', 'parse_into')), (Generator, ('generate',)),
                       (Dialect, ('parse', 'generate', 'tokenize', 'parse_into', 'transpile', 'normalize_identifier',
                                  'quote_identifier', 'case_sensitive', 'can_quote')),
                       (TypeAnnotator, ('annotate',))):
        for n in names:
            if hasattr(cls, n): _wrap(cls, n)
import pytest
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    _cur[0] = item.nodeid; yield; _cur[0] = None
def pytest_sessionfinish(session):
    json.dump({k: sorted(v) for k, v in _used.items()}, open(os.environ.get('USAGE_OUT', 'usage.json'), 'w'))
