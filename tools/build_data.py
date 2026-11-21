"""Build app/data/map.json.gz from per-test coverage + fixture opens on the pinned sqlglot.

Everything here is derived from the pinned commit only (coverage, imports, fixtures);
nothing is derived from any patch or answer.
"""
import ast, collections, glob, gzip, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import core

linemap_json, openmap_json, tests_json, src, out = sys.argv[1:6]
usage_json = sys.argv[6] if len(sys.argv) > 6 else None
M = json.load(open(linemap_json))['lines']
T = json.load(open(tests_json))['tests']
ids = [t['id'] for t in T]; num_of_id = {t['id']: t['number'] for t in T}

sets, set_id = [], {}
def sid(nums):
    key = tuple(sorted(nums))
    if key not in set_id:
        set_id[key] = len(sets); sets.append(list(key))
    return set_id[key]

lines = {f: {l: sid(s) for l, s in d.items()} for f, d in M.items()}
# lines inside functions that only ever ran while modules were being imported (no test was running)
IMP = json.load(open(linemap_json))['import']
import_only = {}
for f, ls in IMP.items():
    p = os.path.join(src, f)
    if not os.path.exists(p): continue
    fi = core.FileIndex(open(p).read())
    keep = sorted(l for l in ls if fi.in_func.get(l) and str(l) not in M.get(f, {}))
    if keep: import_only[f] = keep
file_tests = {f: sid(set().union(*map(set, d.values()))) for f, d in M.items()}

# name -> sets of tests that run a statement reading that name (across sqlglot)
uses = collections.defaultdict(set)
for p in sorted(glob.glob(os.path.join(src, 'sqlglot', '**', '*.py'), recursive=True)):
    rel = os.path.relpath(p, src)
    d = M.get(rel, {})
    fi = core.FileIndex(open(p).read())
    for nm, stmts in core.name_uses(open(p).read()).items():
        for s in stmts:
            a, b = fi.span_of.get(s, (s, s))
            ts = set()
            for l in range(a, b + 1): ts |= set(d.get(str(l), ()))
            if ts: uses[nm].add((rel, s, sid(ts)))
uses = {nm: sorted(v) for nm, v in uses.items()}

# fixture file -> tests that opened it; fixture directory -> tests that opened anything in it
OM = json.load(open(openmap_json))
fix, fixdir = collections.defaultdict(set), collections.defaultdict(set)
for nid, fs in OM.items():
    if nid in num_of_id:
        for f in fs:
            fix[f].add(num_of_id[nid]); fixdir[os.path.dirname(f)].add(num_of_id[nid])

# test module -> test files that import it (e.g. tests/dialects/test_dialect.py's Validator)
importers = collections.defaultdict(set)
for p in sorted(glob.glob(os.path.join(src, 'tests', '**', '*.py'), recursive=True)):
    rel = os.path.relpath(p, src)
    for n in ast.walk(ast.parse(open(p).read())):
        mods = [n.module] if isinstance(n, ast.ImportFrom) and n.module else ([a.name for a in n.names] if isinstance(n, ast.Import) else [])
        for m in mods:
            if m and m.startswith('tests'):
                importers[m.replace('.', '/') + '.py'].add(rel)
# class (module:qualname, whole MRO) -> tests that used an object of that class
usage = {}
if usage_json:
    acc = collections.defaultdict(set)
    for nid, classes in json.load(open(usage_json)).items():
        if nid in num_of_id:
            for c in classes: acc[c].add(num_of_id[nid])
    usage = {c: sid(v) for c, v in acc.items()}
data = {'usage': usage, 'import_only': import_only, 'importers': {k: sorted(v) for k, v in importers.items()}, 'ids': ids, 'sets': sets, 'lines': lines, 'file_tests': file_tests, 'uses': uses,
        'fixtures': {f: sid(s) for f, s in fix.items()}, 'fixture_dirs': {d: sid(s) for d, s in fixdir.items()}}
with gzip.open(out, 'wt', compresslevel=9) as f: json.dump(data, f, separators=(',', ':'))
print('sets', len(sets), 'names', len(uses), 'bytes', os.path.getsize(out))
