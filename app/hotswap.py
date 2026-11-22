"""Fast path for checks: the selector process imports the pinned sqlglot once; each check forks it, replaces
the code of the edited functions in place, and runs the selected tests in the child.

Only used when every edited line sits inside a function body defined at module or class level, so the
change is fully described by new code objects. Anything else (class/module settings, signatures,
decorators, new or removed functions, lambdas outside functions) returns None and the caller falls back to
a fresh interpreter on a patched copy of the source."""
import ast, io, os, sys, types, unittest

_loaded = [False]


def preload(src, testroot):
    if _loaded[0]: return
    os.environ['SKIP_INTEGRATION'] = '0'
    for p in (str(testroot), str(src)):
        if p not in sys.path: sys.path.insert(0, p)
    import importlib, pkgutil, sqlglot
    for info in pkgutil.walk_packages(sqlglot.__path__, prefix='sqlglot.'):
        if info.name.endswith('__main__'): continue
        try: importlib.import_module(info.name)
        except Exception: pass
    _loaded[0] = True


def _outermost_def(tree, line):
    """Qualified name of the outermost function (module- or class-level) whose body contains `line`."""
    def walk(node, prefix):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, ast.ClassDef) and ch.lineno <= line <= ch.end_lineno:
                return walk(ch, prefix + ch.name + '.')
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)) and ch.lineno <= line <= ch.end_lineno:
                if line < ch.body[0].lineno: return None          # signature / decorator line
                return prefix + ch.name
        return None
    return walk(tree, '')


def swap_plan(path, old_text, new_text, touched, fi):
    """{qualname: code object} for the edited functions, or None if the edit is not body-only."""
    old_tree, new_tree = ast.parse(old_text), ast.parse(new_text)
    def shape(tree):
        out = []
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out.append((type(n).__name__, n.name, ast.dump(n.args) if hasattr(n, 'args') else '',
                            [ast.dump(d) for d in getattr(n, 'decorator_list', [])]))
        return out
    if shape(old_tree) != shape(new_tree): return None     # functions/classes/signatures/decorators all unchanged
    same_lines = old_text.count('\n') == new_text.count('\n')
    lambdas = [n for n in ast.walk(old_tree) if isinstance(n, ast.Lambda)]
    names, lam_lines = set(), set()
    for L in touched:
        if L not in fi.span_of: continue
        if not fi.in_func.get(L): return None
        q = _outermost_def(old_tree, L)
        if q is not None:
            names.add(q); continue
        # a lambda in a class- or module-level table: matched by its (unchanged) first line
        lam = [n for n in lambdas if n.lineno <= L <= n.end_lineno]
        if not lam or not same_lines: return None
        outer = min(lam, key=lambda n: n.lineno)
        if sum(1 for n in lambdas if n.lineno == outer.lineno) != 1: return None
        lam_lines.add(outer.lineno)
    if not names and not lam_lines: return None
    code = compile(new_text, '<patched>', 'exec')
    found, found_lam = {}, {}
    def collect(co):
        for c in co.co_consts:
            if isinstance(c, types.CodeType):
                found.setdefault(c.co_qualname, c)
                if c.co_name == '<lambda>': found_lam.setdefault(c.co_firstlineno, []).append(c)
                collect(c)
    collect(code)
    plan = {}
    for q in names:
        if q not in found: return None
        plan[q] = found[q]
    for ln in lam_lines:
        cands = [c for c in found_lam.get(ln, []) if '<lambda>' == c.co_qualname.split('.')[-1] and '<locals>' not in c.co_qualname]
        if len(cands) != 1: return None
        plan[('<lambda>', ln)] = cands[0]
    return plan


def _live_functions(module):
    """qualname -> function objects defined in this module (unwrapping staticmethod/classmethod/property/wraps).
    Lambdas stored in class/module tables are also listed under ('<lambda>', first line)."""
    out, seen = {}, set()
    def add(f):
        while True:
            if isinstance(f, (staticmethod, classmethod)): f = f.__func__; continue
            if isinstance(f, types.FunctionType):
                if getattr(f, '__module__', None) == module.__name__:
                    out.setdefault(f.__code__.co_qualname, []).append(f)
                    if f.__code__.co_name == '<lambda>':
                        out.setdefault(('<lambda>', f.__code__.co_firstlineno), []).append(f)
                w = getattr(f, '__wrapped__', None)
                if w is not None and w is not f: f = w; continue
            break
    def visit(ns, depth):
        for v in list(ns.values()):
            if id(v) in seen: continue
            seen.add(id(v))
            if isinstance(v, property):
                for f in (v.fget, v.fset, v.fdel):
                    if f is not None: add(f)
            elif isinstance(v, type) and v.__module__ == module.__name__ and depth < 6:
                visit(dict(vars(v)), depth + 1)
            elif isinstance(v, dict) and depth < 6:
                visit({i: x for i, x in enumerate(list(v.values()) + list(v.keys()))}, depth + 1)
            elif isinstance(v, (list, tuple, set, frozenset)) and depth < 6:
                visit({i: x for i, x in enumerate(v)}, depth + 1)
            else:
                add(v)
    visit(dict(vars(module)), 0)
    return out


def apply(plans):
    """In the child: install new code objects. Raises if anything does not match exactly."""
    import importlib
    for path, plan in plans.items():
        mod = importlib.import_module(path[:-3].replace('/', '.').removesuffix('.__init__'))
        live = _live_functions(mod)
        for q, co in plan.items():
            fs = live.get(q)
            if not fs: raise LookupError(q)
            if isinstance(q, tuple):    # lambdas: every live object must come from this exact line of this file
                fs = [f for f in fs if f.__code__.co_qualname.split('.')[-1] == '<lambda>']
                if not fs: raise LookupError(q)
            for f in fs:
                if f.__code__.co_freevars != co.co_freevars: raise ValueError(q)
                f.__code__ = co.replace(co_filename=f.__code__.co_filename)


def run_in_fork(plans, ids, cpu_limit):
    """Fork, swap code, run the tests (stop at first failure). True only if every test ran and passed."""
    import resource
    sys.stdout.flush(); sys.stderr.flush()
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(r); ok = False
        try:
            null = os.open(os.devnull, os.O_WRONLY)
            os.dup2(null, 1); os.dup2(null, 2)          # tests must never write to our stdout
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit + 1))
            apply(plans)
            suite = unittest.TestSuite(unittest.TestLoader().loadTestsFromName(i) for i in ids)
            import syncpool
            for n in ('tests.test_optimizer', 'tests.test_executor'):
                m = sys.modules.get(n)
                if m is not None and hasattr(m, 'ProcessPoolExecutor'): m.ProcessPoolExecutor = syncpool.SyncExecutor
            res = unittest.TextTestRunner(stream=io.StringIO(), failfast=True, verbosity=0).run(suite)
            ok = res.wasSuccessful() and res.testsRun == len(ids)
        except BaseException:
            ok = False
        try: os.write(w, b'1' if ok else b'0')
        finally: os._exit(0)
    os.close(w)
    try: got = os.read(r, 1)
    finally:
        os.close(r); os.waitpid(pid, 0)
    return got == b'1'
