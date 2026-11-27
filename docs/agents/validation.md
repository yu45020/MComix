# Validation

The session that writes the code runs the checks. AGENTS.md Commands describes `tools/check.py`,
its modes and its timeout; `.codex/INSTRUCTIONS.md` adds Codex's command mechanics.

## Running the check

- A worktree has no `.venv`: run the check with the primary checkout's Python,
  `<primary>/.venv/python.exe tools/check.py`, from the worktree's root. `<primary>` is the parent
  of `git rev-parse --path-format=absolute --git-common-dir`. Write the path with forward slashes,
  which PowerShell and Git Bash both accept. It tests the worktree's source,
  because `mcomix` isn't installed into the environment. Never create a second conda prefix.
- Run `check.py` calls in one checkout one at a time: runs of one mode share a log.
- Each run writes its full output to `.tmp/check/full.log`, `quick.log` or `tests.log` and prints
  the log path, each phase's `PASS` or `FAIL` and up to 60 lines per failed phase. Read the log
  only when the run fails or you need something only it has.
- The script stops pytest after 300 s, killing its process tree, and prints
  `TIMEOUT: pytest after 300 s`.

## Which check a change needs

- A documentation-only commit (only `.md` files, `.codex/agents/*.toml` and `.gitignore`):
  `--quick`.
- While iterating on code: focused tests with `--tests <pytest args>`. Choose them by searching
  `test/` for the strings and symbols the diff changes, not only the test file named after the
  module. Include every test that uses a changed shared fake (`test/_*.py`) or `conftest.py`
  helper.
- Right before each commit, after the last edit: `--quick`.
- Before review, for a plan task, a standalone fix and a closeout's `<plan>-close-fixes` branch,
  unless the diff is documentation-only: the full check on the commit under review. Rerun it after review fixes that change code, and
  after merging `main` into the branch when its new commits change code.
- Merges: a clean merge of a task or `<plan>-close-fixes` branch into its plan's integration branch needs no check (the
  plan's integration task checks the tasks together). A conflict resolution that changes code
  needs the full check.
- When the full check fails only on a test the diff doesn't touch, run that test alone. If it
  passes, rerun the full check once and record the test as flaky in an Open item. If it fails
  alone, or the full check fails on it again, it is a real failure.

## Evidence

- Evidence is the tested commit, the command, the exit code and the script's `PASS`/`FAIL` lines;
  pytest's line carries its counts. A nonzero exit, a timeout or an interrupted run is a failure,
  never "probably fine". `--quick` alone never proves a code change.
- In a commit or merge body it is at most four lines: no environment dumps or log paths.
- No benchmarks or timing runs unless the owner or the plan asks for a measurement; a plan's
  result goes in the handoff.
- A standalone fix and a closeout have no handoff: what the rules put in a handoff goes in the
  reviewer prompt instead.

## UI and manual checks

- The tests cover little of the UI. For a UI change, run the app, look at what changed, and say
  how you checked it in the reviewer prompt. If you can't run or see the app, record a pending
  manual check as an Open item instead.
- Run the app from PowerShell with the clean environment of AGENTS.md Environment and with
  `APPDATA` and `XDG_DATA_HOME` both set to a scratch folder. Preferences, bookmarks and the
  library live in `%APPDATA%\MComix`, which the owner's installed MComix shares, and GTK keeps
  the recent-files list in its user data folder.
- In a worktree, set that snippet's `$v` to `<primary>\.venv` and start the app from the
  worktree's root with `& "$v\python.exe" mcomixstarter.py [path]`; `start.bat` needs a `.venv`
  beside it.
- Stop the app you started by its PID.
