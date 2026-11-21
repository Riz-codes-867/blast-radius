# Blast Radius — coverage-guided test selection for sqlglot

Builders Buildathon 2026, RWX "Blast Radius" track. Solo entry by Syed Rizwan Ahmed.

For each patch against sqlglot `eb11aec4be8cb2122afe713a4816662c3f0fb2fa`, `app/selector.py` prints the
tests the patch can break, split across workers to minimise the simulated CI time.

## How it decides

1. **Which tests.** A per-test coverage map of the pinned commit (`app/data/map.json.gz`) says which tests
   execute each statement. For every changed line we take the tests that run its statement (all lines of a
   multi-line statement header, since Python 3.13 reports the inner lines). Special cases:
   - *Comment/whitespace-only edits*: if the token stream minus comments is unchanged, the program is unchanged → no tests.
   - *Import-time lines* (class or module constants such as `WEEK_OFFSET = -1`): coverage cannot say which test
     depends on them, so we follow the names they define to every statement that reads them, then narrow to tests
     that exercise the enclosing class: tests that used an object of that class or a subclass at run time
     (recorded by `tools/usage_plugin.py`, so a Postgres setting also reaches Redshift tests), same-dialect files,
     code naming the class, and the class's own methods.
   - *Function code that only runs at start-up* (decorators, table builders, metaclasses): the file's or dialect
     family's tests plus tests that run the function's callers.
   - *Names read directly by test code*: some tests inspect module data themselves (e.g. `test_typing` compares
     `EXPRESSION_METADATA` tables), which coverage of sqlglot cannot see; edits to a definition also select the tests
     whose own code reads that name.
   - *Lambdas* inside class-level tables run when called, so they map like function code.
   - *Fixture files*: tests that opened that file during the test run (recorded with an audit hook); unknown
     fixtures fall back to tests that read its directory.
   - *Test files*: all tests in that file and in files importing it.
   - *Edited imports*: if a sqlglot import in the patched file no longer resolves against the pinned source,
     every test runs (an ImportError fails whole test files); valid import edits add the import-order tests.
   - *Files outside `sqlglot/` and `tests/`* (e.g. `sqlglotc/setup.py`): the test files that name them.
   - *Syntax errors after the patch, unknown helpers, or any internal error* → run every test (never misses).
2. **How to split.** Longest test first onto the least-loaded worker, for 1–8 workers; keep the cheapest under
   the official cost (busiest worker + 2 s per worker).
3. **How much compute.** Selection is lookups only (~1–2 s for a batch). Then, only when it pays: if a code-only
   patch's selected tests are cheap compared with the simulated time they would cost, we run them on a patched
   copy of sqlglot inside the container (`app/runcheck.py`, unittest, stop at first failure). If every one passes,
   nothing can be failing among them, so the commit runs nothing (cost 0). If any fails, we keep the full
   selection. Tests above 5 CPU-seconds are never run; they are dropped only when all the cheaper tests that
   cover the same change pass. Worker-pool work runs in-process (`app/syncpool.py`) so we do not pay for spawned
   interpreters, every check has a hard CPU cap (it may never cost more than it could save), and the whole batch
   has a wall-clock budget. CPU estimates come from per-test measurements in the same harness (`app/data/calib.json`).

## Building the map (offline, from the pinned commit only)

`tools/build_maps.sh <kit>` clones sqlglot at the pinned commit, runs its test suite under coverage.py with one
coverage context per test, and records fixture-file opens. The suite sends work to spawned worker processes,
which per-test coverage cannot see, so `tools/syncpool_plugin.py` runs that work in-process **through pickle**
(as a real process boundary would). `tools/prewarm_plugin.py` imports every module and exercises every
dialect once before the first test, so one-time work (lazy dialect imports, metaclass table building, per-class
caches) is recorded as start-up work instead of being credited to whichever test triggered it first; edits to
such start-up code select the file's (or dialect family's) tests plus the callers' tests. Nothing is derived from patches or answers; no patch→test table exists.

## Acknowledgments

- [sqlglot](https://github.com/tobymao/sqlglot) (MIT) — the pinned source (`app/src/`) and its test suite (`app/testroot/tests/`) are vendored with its license.
- duckdb, pandas, numpy — installed from PyPI at build time, pinned to the organizers' `requirements.lock`, because sqlglot's tests import them.
- [coverage.py](https://github.com/nedbat/coveragepy) and [pytest](https://pytest.org) — used offline to build the map.
- Starter kit, scorer and scheduling idea (longest-processing-time first) from the Blast Radius starter by RWX / Builders.
- Developed with help from Claude (Anthropic) as an AI pair-programmer; AI use is allowed by the track rules.

## How it was validated

- The 30 released samples (labels used only for scoring and error analysis).
- Our own mutations of the pinned source (and/or swaps, flipped constants, off-by-one numbers), each labeled by
  running the full test suite the way the organizers did. They exposed two blind spots that the samples did not:
  class-level settings read without running any dialect-specific code, and one-time start-up code.
