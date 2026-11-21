"""Patch -> touched statements, using the pinned sqlglot source."""
import ast, io, re, tokenize
SRC = 'src'  # set by selector.py

def parse_patch(patch):
    """Return {path: [(old_start, old_lines, new_lines), ...]} hunks with raw line lists."""
    out = {}
    for part in re.split(r'^diff --git ', patch, flags=re.M)[1:]:
        m = re.search(r'^\+\+\+ (?:b/)?(\S+)', part, re.M)
        mo = re.search(r'^--- (?:a/)?(\S+)', part, re.M)
        path = m.group(1) if m and m.group(1) != '/dev/null' else (mo.group(1) if mo else None)
        hunks = []
        for h in re.finditer(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@[^\n]*\n((?:(?:[ +\-\\][^\n]*)?\n?)*)', part, re.M):
            body = [l for l in h.group(5).split('\n') if l != '' and not l.startswith('\\')]
            hunks.append((int(h.group(1)), body))
        out[path] = hunks
    return out

def touched_old_lines(hunks):
    """Old-file line numbers removed, plus the old lines on both sides of each insertion."""
    lines = set()
    for start, body in hunks:
        o = start
        for l in body:
            if l.startswith('-'): lines.add(o); o += 1
            elif l.startswith('+'): lines.add(o - 1); lines.add(o)
            else: o += 1
    return {l for l in lines if l > 0}

def apply_hunks(old_text, hunks):
    old = old_text.split('\n'); new = []; i = 0
    for start, body in hunks:
        new.extend(old[i:start - 1]); i = start - 1
        for l in body:
            if l.startswith('+'): new.append(l[1:])
            elif l.startswith('-'): i += 1
            else: new.append(old[i]); i += 1
    new.extend(old[i:])
    return '\n'.join(new)

def code_tokens(text):
    """Token stream without comments/blank-line tokens: equal streams => same program."""
    out = []
    for t in tokenize.generate_tokens(io.StringIO(text).readline):
        if t.type in (tokenize.COMMENT, tokenize.NL, tokenize.ENCODING): continue
        out.append((t.type, t.string))
    return out

class FileIndex:
    """For one pinned source file: each line's statement, header span, and whether it runs inside a call."""
    COMPOUND = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.Try,
                ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef) + ((ast.Match,) if hasattr(ast, 'Match') else ()) \
                + ((ast.TryStar,) if hasattr(ast, 'TryStar') else ())
    def __init__(self, text):
        self.tree = ast.parse(text)
        self.stmt_of = {}   # line -> innermost statement start
        self.span_of = {}   # line -> (first, last) lines of that statement's header (whole stmt if simple)
        self.in_func = {}   # line -> True when it only runs when some function/lambda is called
        self._walk(self.tree, False)
        for n in ast.walk(self.tree):
            if isinstance(n, ast.Lambda):
                for l in range(n.lineno, n.end_lineno + 1):
                    self.in_func[l] = True; self.span_of[l] = (n.lineno, n.end_lineno)
    def _walk(self, node, infunc):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.stmt):
                first = min([child.lineno] + [d.lineno for d in getattr(child, 'decorator_list', [])])
                if isinstance(child, self.COMPOUND) and getattr(child, 'body', None):
                    last = max(child.lineno, child.body[0].lineno - 1)
                else:
                    last = child.end_lineno
                for l in range(first, last + 1):
                    self.stmt_of[l] = child.lineno; self.span_of[l] = (first, last); self.in_func[l] = infunc
                self._walk(child, infunc or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)))
    def defined_names(self, line):
        """Names assigned/defined by the innermost statement covering `line`."""
        best = None
        for node in ast.walk(self.tree):
            if isinstance(node, ast.stmt) and node.lineno <= line <= getattr(node, 'end_lineno', node.lineno):
                if best is None or (node.end_lineno - node.lineno) < (best.end_lineno - best.lineno):
                    best = node
        names = set()
        if best is None: return names
        if isinstance(best, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(best.name)
        elif isinstance(best, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            tg = best.targets if isinstance(best, ast.Assign) else [best.target]
            for t in tg:
                for n in ast.walk(t):
                    if isinstance(n, ast.Name): names.add(n.id)
                    if isinstance(n, ast.Attribute): names.add(n.attr)
        return names

def name_uses(text):
    """name -> statement start lines that read it (Name or .attr), for one file."""
    tree = ast.parse(text); idx = FileIndex.__new__(FileIndex)
    idx.tree = tree; idx.stmt_of = {}; idx.span_of = {}; idx.in_func = {}; idx._walk(tree, False)
    uses = {}
    for n in ast.walk(tree):
        nm = n.id if isinstance(n, ast.Name) else (n.attr if isinstance(n, ast.Attribute) else None)
        if nm and hasattr(n, 'lineno'):
            uses.setdefault(nm, set()).add(idx.stmt_of.get(n.lineno, n.lineno))
    return uses
