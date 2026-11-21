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
     that exercise the enclosing class (same-dialect files, code naming the class, the class's own methods).
   - *Function code that only runs at start-up* (decorators, table builders, metaclasses): the file's or dialect
     family's tests plus tests that run the function's callers.
   - *Lambdas* inside class-level tables run when called, so they map like function code.
   - *Fixture files*: tests that opened that file during the test run (recorded with an audit hook); unknown
     fixtures fall back to tests that read its directory.
   - *Test files*: all tests in that file and in files importing it.
   - *Syntax errors after the patch, unknown helpers, or any internal error* → run every test (never misses).
2. **How to split.** Longest test first onto the least-loaded worker, for 1–8 workers; keep the cheapest under
   the official cost (busiest worker + 2 s per worker).
3. **How much compute.** Run time does lookups only (~0.8 s for 30 commits); all heavy work happened offline.

## Building the map (offline, from the pinned commit only)

`tools/build_maps.sh <kit>` clones sqlglot at the pinned commit, runs its test suite under coverage.py with one
coverage context per test, and records fixture-file opens. The suite sends work to spawned worker processes,
which per-test coverage cannot see, so `tools/syncpool_plugin.py` runs that work in-process **through pickle**
(as a real process boundary would). `tools/prewarm_plugin.py` imports every module and exercises every
dialect once before the first test, so one-time work (lazy dialect imports, metaclass table building, per-class
caches) is recorded as start-up work instead of being credited to whichever test triggered it first; edits to
such start-up code select the file's (or dialect family's) tests plus the callers' tests. Nothing is derived from patches or answers; no patch→test table exists.

## Acknowledgments

- [sqlglot](https://github.com/tobymao/sqlglot) (MIT) — the pinned source is vendored in `app/src/` with its license.
- [coverage.py](https://github.com/nedbat/coveragepy) and [pytest](https://pytest.org) — used offline to build the map.
- Starter kit, scorer and scheduling idea (longest-processing-time first) from the Blast Radius starter by RWX / Builders.
- Developed with help from Claude (Anthropic) as an AI pair-programmer; AI use is allowed by the track rules.
