"""Turn per-test coverage into {file: {line: [test numbers]}} plus import-time lines."""
import sqlite3, json, collections, sys
from coverage.numbits import numbits_to_nums
covdb, tests_json, out = sys.argv[1:4]
T = json.load(open(tests_json))['tests']
key = {}
for t in T:
    f, rest = t['id'].split('::', 1)
    key[f[:-3].replace('/', '.') + '.' + rest.replace('::', '.')] = t['number']
c = sqlite3.connect(covdb)
ctx = dict(c.execute("select id, context from context"))
files = dict(c.execute("select id, path from file"))
M = collections.defaultdict(lambda: collections.defaultdict(set)); IMP = collections.defaultdict(set)
for fid, cid, nb in c.execute("select file_id, context_id, numbits from line_bits"):
    p = 'sqlglot/' + files[fid].split('/sqlglot/sqlglot/', 1)[1]
    name = ctx[cid]
    for ln in numbits_to_nums(nb):
        if name in key: M[p][ln].add(key[name])
        else: IMP[p].add(ln)
json.dump({'lines': {f: {str(l): sorted(s) for l, s in d.items()} for f, d in M.items()},
           'import': {f: sorted(s) for f, s in IMP.items()}}, open(out, 'w'))
print('files', len(M), 'tests seen', len({n for d in M.values() for s in d.values() for n in s}))
