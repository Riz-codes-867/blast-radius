"""pytest plugin for map building only: before any test runs, import every sqlglot module and
exercise every dialect once. One-time work (lazy dialect imports, metaclass table building,
per-class caches) then runs outside any test, instead of being credited to whichever test
happened to trigger it first."""
import importlib, pkgutil
def pytest_sessionstart(session):
    import sqlglot
    for info in pkgutil.walk_packages(sqlglot.__path__, prefix="sqlglot."):
        if info.name.endswith("__main__"): continue
        try: importlib.import_module(info.name)
        except Exception: pass
    from sqlglot.dialects import DIALECTS
    from sqlglot.dialects.dialect import Dialect
    for name in DIALECTS:
        try:
            d = Dialect.get_or_raise(name.lower())
            d.generate(d.parse_one("SELECT a FROM t WHERE b = 1"))
        except Exception: pass
