"""Blast Radius selector: choose the tests a sqlglot patch can break, then split them across workers.

Decision 1 (which tests): map each changed line of the pinned source to the tests that execute it,
using a per-test coverage map built ahead of time from the pinned commit (see tools/ and README).
Decision 2 (how to split): longest-test-first onto the least-loaded worker, for every worker count
1..8, keeping the cheapest under the official cost (busiest worker + 2 s per worker).
Decision 3 (how much compute): only lookups at run time; parsing touches just the edited files.
Any unexpected situation falls back to running every test, which can never miss.
"""
import ast, gzip, json, os, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import core

DATA = Path(os.environ.get('BR_DATA', '/data'))
SRC = HERE / 'src'
core.SRC = str(SRC)


def log(*a): print(*a, file=sys.stderr)


PROBE = set()
try:
    PROBE = set(json.loads((HERE / 'probe.json').read_text()))
except Exception:
    pass


class Selector:
    def __init__(self, tests):
        self.tests = tests
        self.ms = {t['number']: round(t['seconds'] * 1000) for t in tests}
        self.by_file = {}
        for t in tests: self.by_file.setdefault(t['file'], set()).add(t['number'])
        with gzip.open(HERE / 'data' / 'map.json.gz', 'rt') as f: d = json.load(f)
        num_of_id = {t['id']: t['number'] for t in tests}
        remap = [num_of_id.get(i) for i in d['ids']]          # build-time number -> runtime number
        self.sets = [frozenset(remap[n] for n in s if remap[n] is not None) for s in d['sets']]
        self.lines, self.file_sets, self.uses = d['lines'], d['file_tests'], d['uses']
        self.fixtures, self.fixture_dirs, self.importers = d['fixtures'], d['fixture_dirs'], d['importers']
        self.usage = d.get('usage', {})
        self.test_names = d.get('test_names', {})
        self.root_refs, self.defs = d.get('root_refs', {}), {k: set(v) for k, v in d.get('defs', {}).items()}
        self.import_sensitive = {t['number'] for t in tests if t['file'] in ('tests/test_dialect_imports.py', 'tests/test_dialect_entry_points.py') or t['id'].endswith('::test_lazy_load')}
        self.import_only = {f: set(v) for f, v in d.get('import_only', {}).items()}
        self.stems = {}
        for f in self.lines: self.stems.setdefault(os.path.basename(f), []).append(f)
        self._idx = {}

    # ---- map lookups
    def at(self, path, line):
        s = self.lines.get(path, {}).get(str(line)); return self.sets[s] if s is not None else frozenset()
    def file_tests(self, path):
        s = self.file_sets.get(path); return self.sets[s] if s is not None else frozenset()
    def users(self, name, exclude=None):
        out = set()
        for f, l, s in self.uses.get(name, ()):
            if (f, l) != exclude: out |= self.sets[s]
        return out
    def index(self, path):
        if path not in self._idx: self._idx[path] = core.FileIndex((SRC / path).read_text())
        return self._idx[path]
    def all_tests(self): return set(self.ms)

    def family(self, path):
        """Tests of the dialect a file belongs to: same-named files' test-time coverage plus tests/dialects/test_<name>.py."""
        stem = os.path.basename(path)[:-3]; out = set()
        for f in self.stems.get(os.path.basename(path), ()): out |= self.file_tests(f)
        out |= self.by_file.get(f'tests/dialects/test_{stem}.py', set())
        return out

    def involvement(self, path, line):
        """Tests that exercise the class(es) around a class-level line."""
        fi = self.index(path)
        chain = [n for n in ast.walk(fi.tree) if isinstance(n, ast.ClassDef) and n.lineno <= line <= n.end_lineno]
        if not chain: return None
        inv = set()
        if any(k in path for k in ('/dialects/', '/parsers/', '/generators/', '/typing/', '/tokenizers/')):
            inv |= self.family(path)
        module = path[:-3].replace('/', '.')
        qual = '.'.join(c.name for c in sorted(chain, key=lambda c: c.lineno))
        for i in range(1, len(chain) + 1):   # tests that used this class (or a subclass) at run time
            q = '.'.join(c.name for c in sorted(chain, key=lambda c: c.lineno)[:i])
            u = self.usage.get(f'{module}:{q}')
            if u is not None: inv |= self.sets[u]
        for c in chain:
            inv |= self.users(c.name)
            for l in range(c.lineno, c.end_lineno + 1):
                if fi.in_func.get(l): inv |= self.at(path, l)
        return inv

    def module_file(self, mod):
        p = mod.replace('.', '/')
        for c in (p + '.py', p + '/__init__.py'):
            if (SRC / c).exists(): return c
        return None

    def imports_resolve(self, path, tree):
        """Every sqlglot import in the patched file names a module/attribute that exists in the pinned source."""
        pkg = path[:-3].replace('/', '.')
        if path.endswith('__init__.py'): pkg = pkg[: -len('.__init__')]
        else: pkg = pkg.rsplit('.', 1)[0]
        guarded = [(t.lineno, t.end_lineno) for t in ast.walk(tree) if isinstance(t, ast.Try)]
        ours = lambda m: m == 'sqlglot' or m.startswith('sqlglot.')
        for n in ast.walk(tree):
            if isinstance(n, (ast.Import, ast.ImportFrom)) and any(a <= n.lineno <= b for a, b in guarded):
                continue   # optional imports inside try/except
            if isinstance(n, ast.ImportFrom):
                mod = n.module or ''
                if n.level:
                    base = pkg.split('.')
                    base = base[: len(base) - (n.level - 1)] if n.level > 1 else base
                    mod = '.'.join(base + ([mod] if mod else []))
                if not ours(mod): continue
                f = self.module_file(mod)
                if f is None: return False
                for a in n.names:
                    if a.name != '*' and '*' not in self.defs.get(f, ()) and a.name not in self.defs.get(f, ()) and self.module_file(mod + '.' + a.name) is None:
                        return False
            elif isinstance(n, ast.Import):
                for a in n.names:
                    if ours(a.name) and self.module_file(a.name) is None: return False
        return True

    # ---- decision 1
    def python_source(self, path, hunks):
        old = (SRC / path).read_text()
        new = core.apply_hunks(old, hunks)
        try:
            new_tree = ast.parse(new)
        except SyntaxError:
            log(path, 'syntax error after patch -> all tests'); return self.all_tests()
        if core.code_tokens(old) == core.code_tokens(new):
            log(path, 'comments/whitespace only -> nothing')
            return self.all_tests() if 'comment' in PROBE else set()
        if not self.imports_resolve(path, new_tree):
            log(path, 'an import no longer resolves -> all tests'); return self.all_tests()
        fi, S = self.index(path), set()
        touched = sorted(core.touched_old_lines(hunks))
        if any(isinstance(n, (ast.Import, ast.ImportFrom)) and not fi.in_func.get(n.lineno)
               and any(n.lineno <= L <= n.end_lineno for L in touched) for n in ast.walk(fi.tree)):
            S |= self.import_sensitive    # import-order / lazy-loading tests
        for L in touched:
            if L not in fi.span_of: continue
            a, b = fi.span_of[L]
            got = set()
            for l in range(a, b + 1): got |= self.at(path, l)
            if fi.in_func.get(L) and not got and any(l in self.import_only.get(path, ()) for l in range(a, b + 1)):
                # function code that only runs while modules load (decorators, table builders):
                # whatever it builds is used later, so take the file's tests and the callers' tests
                got = set(self.file_tests(path)) or set(self.family(path))
                for fn in [n for n in ast.walk(fi.tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.lineno <= L <= n.end_lineno]:
                    got |= self.users(fn.name)
                log(path, L, 'import-time function code ->', len(got))
                if 'importfn' in PROBE: return self.all_tests()
            if not fi.in_func.get(L) and 'module' in PROBE: return self.all_tests()
            if fi.in_func.get(L) and 'func' in PROBE: return self.all_tests()
            if not fi.in_func.get(L):   # runs at import: follow the names it defines to their readers
                names, users = fi.defined_names(L), set()
                for nm in names: users |= self.users(nm, exclude=(path, fi.stmt_of[L]))
                inv = self.involvement(path, L) if users else None
                if inv is not None and users & inv: users &= inv
                got |= users
                for nm in names:                       # tests whose own code reads the name
                    t = self.test_names.get(nm)
                    if t is not None: got |= self.sets[t]
                if not got:
                    got = (self.involvement(path, L) or set()) | set(self.file_tests(path)) | self.family(path)
                    if not got: got = self.all_tests()
                    log(path, L, 'import-time line, no readers ->', len(got))
            S |= got
        return S

    def select(self, patch):
        S = set()
        for path, hunks in core.parse_patch(patch).items():
            if path is None: return self.all_tests()
            if path.startswith('sqlglot/') and path.endswith('.py'):
                if (SRC / path).exists(): S |= self.python_source(path, hunks)
                else:
                    log(path, 'new module -> covered by edits that import it')
                    if 'outside' in PROBE: return self.all_tests()
            elif path.startswith('tests/') and path.endswith('.py'):
                if 'testfile' in PROBE: return self.all_tests()
                files = {path} | set(self.importers.get(path, ()))
                for f in files: S |= self.by_file.get(f, set())
                if not self.by_file.get(path) and path not in self.importers:
                    S |= self.all_tests(); log(path, 'unknown test helper -> all tests')
            elif path.startswith('tests/'):
                if 'fixture' in PROBE: return self.all_tests()
                got = self.fixtures.get(path)
                if got is None:
                    got = self.fixture_dirs.get(os.path.dirname(path))
                    log(path, 'fixture not in map -> tests reading its directory')
                S |= self.sets[got] if got is not None else self.all_tests()
            elif path.startswith('sqlglot/'):
                if 'outside' in PROBE: return self.all_tests()
                S |= self.file_tests(path) or self.all_tests()
            else:   # outside sqlglot/ and tests/: only test files that name this path read it
                if 'outside' in PROBE: return self.all_tests()
                for f in self.root_refs.get(path.split('/')[0], ()): S |= self.by_file.get(f, set())
        return S

    # ---- decision 2
    def cost(self, workers):
        workers = [w for w in workers if w]
        return 0 if not workers else max(sum(self.ms[n] for n in w) for w in workers) + 2000 * len(workers)
    def lpt(self, nums, k):
        W, L = [[] for _ in range(k)], [0] * k
        for n in sorted(nums, key=lambda n: (-self.ms[n], n)):
            i = L.index(min(L)); W[i].append(n); L[i] += self.ms[n]
        return [w for w in W if w]
    def schedule(self, nums):
        if not nums: return []
        best = None
        for k in range(1, 9):
            w = self.lpt(nums, k); c = self.cost(w)
            if best is None or c < best[0]: best = (c, w)
        return best[1]


def main():
    tests = json.loads((DATA / 'tests.json').read_text())['tests']
    sel = None
    try:
        sel = Selector(tests)
    except Exception as e:
        log('map failed to load, running everything:', repr(e))
    every = list(range(len(tests)))
    for patch in sorted((DATA / 'commits').glob('*.patch')):
        try:
            chosen = sel.select(patch.read_text(errors='replace')) if sel else set(every)
            chosen = {n for n in chosen if n in (sel.ms if sel else chosen)}
        except Exception as e:
            log(patch.stem, 'error, running everything:', repr(e)); chosen = set(every)
        workers = sel.schedule(sorted(chosen)) if sel else [every]
        print(json.dumps({'commit': patch.stem, 'workers': workers}), flush=False)
    sys.stdout.flush()


if __name__ == '__main__':
    main()
