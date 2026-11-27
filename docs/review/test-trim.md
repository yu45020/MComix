# Test trim

A test-only review that makes the test suite smaller without losing coverage of behavior
the app relies on. One read-only finding pass, then a trim plan. It changes no production code and
adds no tests: a missing test or a bug found along the way becomes a `docs/TODO.md` Open item. The
owner reads only the Owner summary of the findings file and approves the plan. The brief is reused
for every trim run.

## Runs

Each run has an **id**, the year and month it starts (`2026-11`, then `2026-11b`, `2026-11c` for
later runs that month), and a **scope**:

- **Full:** every test file: the `.py` files in `git ls-tree --name-only <audit commit> test/`.
- **Since `<commit>`:** the test files added or changed between that commit and the audit
  commit (`git diff --name-only --diff-filter=AM <commit> <audit commit> -- "test/*.py"`), usually
  since the latest Past run's audit commit. When that lists most of the test files, run Full
  instead.

The owner starts a run on demand, for example after a large plan, or to catch tests added since
the last run.

| Item | Name |
| --- | --- |
| Findings branch | `trim-<id>-findings` |
| Findings file | `docs/review/trim-<id>/findings.md` |
| Plan | `trim-<id>` |

Past runs, with their outcome. A run's deleted findings file is read from history:
`git show <commit>^:docs/review/trim-<id>/findings.md`, where `<commit>` is
`git log -1 --format=%H -- docs/review/trim-<id>/findings.md`.

| Id | Scope | Audit commit | Outcome |
| --- | --- | --- | --- |
| 2026-11 | Full | `196672f4` | 102 of 104 findings closed; 14,867 → 14,148 test lines, 574 → 543 test functions. Dropped, so their tests stay: the Pillow-source term of `webp_animation._render_memory` is covered only by its WebP memory test; the max-size 0 step of the WebP display-size test is what lets its 2048 step fail |

## Branches and sessions

- The **audit commit** is the tip of `main` when the first session starts; a resumed session reads
  it from the findings header. All reviewers read the audit commit, so `file:line` references stay
  valid during the finding pass. Only documentation is committed on the findings branch.
- **Session start:** work in your own worktree on `trim-<id>-findings`: created from `main` in the
  first session, switched to later. Claude Code calls `EnterWorktree`, then
  `git switch -c trim-<id>-findings main` or `git switch trim-<id>-findings`; Codex follows
  `.codex/INSTRUCTIONS.md` (Worktrees). If `git worktree list` shows the branch checked out
  elsewhere (a stopped session), ask the owner to release that worktree first. Resume at the first
  group that has no section in the findings file.
- **Session end:** commit, `git switch --detach` and leave the worktree. Claude Code leaves with
  `ExitWorktree` `remove` and `discard_changes: true` once `git branch --contains HEAD` lists the
  findings branch. Keep the findings branch, so the next session can take it.

Stop and ask the owner only when the baseline fails or the branch is checked out in another
worktree.

## Setup (first session)

- **Baseline:** run the full check ([validation.md](../agents/validation.md)) at the audit commit
  and record its evidence in the findings header. A failing baseline goes to the owner before any
  review starts.
- **Scope and groups:** record the scope in the findings header, assign every test file in scope to
  a group (below) and record the assignment with each group's size: lines from
  `git grep -c -e "" <audit commit> -- <files>` and test functions from
  `git grep -c -E "^\s*def test_" <audit commit> -- <files>`, each summed per group. A group with
  no file in scope is skipped.

## Groups

Paths are relative to `test/`. A test file belongs to the first group, in table order, whose
pattern it matches; one that matches none goes to T6. `__init__.py`, `conftest.py` and the
shared scaffolding in `_*.py` hold no tests and belong to no group. Neither do
`library_types.py` and `open_last_page.py`: pytest doesn't collect them, so their tests never run
and can't be a kept test.

| Group | Files |
| --- | --- |
| T1 Archives and external tools | `test_archive*.py`, `test_external_*.py`, `test_zip_*.py`, `test_pdf_*.py`, `test_password_*.py`, `test_process*.py`, `test_unrar_*.py` |
| T2 Pages and rendering | `test_page_*.py`, `test_image_tools.py`, `test_render_cache.py`, `test_redraw_*.py`, `test_materialize_*.py`, `test_rotation_*.py`, `test_box_*.py`, `test_zoom_*.py`, `test_scrolling.py` |
| T3 Upscaling | `test_upscale*.py`, `test_span*.py`, `test_export_span.py` |
| T4 Files, navigation and lifecycle | `test_file_*.py`, `test_tools.py`, `test_navigation_*.py`, `test_reading_position.py`, `test_recent_history.py`, `test_bookmark_*.py`, `test_shutdown_*.py`, `test_storage_*.py`, `test_atomic_config.py`, `test_startup_console.py` |
| T5 UI, thumbnails and library | `test_thumbnail_*.py`, `test_library_*.py`, `test_chooser_*.py`, `test_dialog_*.py`, `test_edit_*.py`, `test_shortcut_*.py`, `test_sidebar_*.py`, `test_openwith_*.py`, `test_callback*.py`, `test_worker_thread.py` |
| T6 Other | any other test file |

A finding may span groups (a T5 dialog test duplicated by a T4 handler test); the group of the test
to be cut records it.

## Finding pass

1. **Find.** Start one `reviewer` per group in scope at once, each with: "Audit: follow the
   Reviewer instructions of docs/review/test-trim.md in place of the review.md checklist", the
   group's files in scope, the audit commit and the worktree path.
2. **Merge.** Once the finders are done, take the groups one at a time in table order. Drop
   duplicates, and check every kept test against all findings, in any group. When finding B cuts
   the test that finding A keeps, drop B, so A's kept test stays.
3. **Record** the group's section in the findings file, and its Open item lines in `docs/TODO.md`.
   Commit after each group.

A stopped session loses the unrecorded groups whose finders ran; on resume, rerun the finders of
every group with no section. There is no verifier: each trim task proves its kept tests, and its
review checks that proof before the task merges (Trim plan).

### Reviewer instructions

When the prompt names this brief and a group, read the group's files in scope whole at the audit
commit, and open any other test or source file to trace a finding. Report only trim findings, by
the trim rules of [tests.md](../agents/tests.md) "Removing a test". Each finding names the behavior,
edge state or fixed bug the cut test covers, and the **kept test** that still asserts it and would
fail if the behavior broke, or the trace showing nothing needs it. A finding without one is not
reported. A Since run cuts only tests in scope.

- Rule 5: for a fixed bug, find its fix in the history (`git log -S` or `git blame` on the test)
  and say whether the code can still reintroduce it.
- Rule 6: name the fake or helper.

Each finding: `T<group>-<nn>` · trim rule (1–6) · `file:line` of the test or step · what it covers ·
kept test or trace · estimated lines removed. Missing coverage or bugs you notice are listed apart,
one line each, for `docs/TODO.md`.

## Findings file

`docs/review/trim-<id>/findings.md`:

- **Header:** audit commit, baseline evidence, group sizes and file assignments, models and effort.
- **Owner summary** (what the owner reads): per group, findings and estimated lines removed; the
  total estimated size of the files in scope after the trim; the Open items added.
- **Findings,** one section per group.

## Trim plan

Draft `trim-<id>` with the `draft-plan` skill on the findings branch, with the findings file as the
settled design; commit the draft on that branch, not on `main` (this overrides the skill's commit
step and its commit approval question). One task per group, split further only as the Tasks comment
of `docs/plans/TEMPLATE.md` allows, each listing the finding ids it closes.

Every task except the integration task has these acceptance criteria:

- the closed findings' tests, steps or cases are removed, and each named kept test asserts the
  behavior its cut test covered;
- no kept test named by any finding of the plan is removed;
- net test lines are negative, with the count in the handoff;
- the fakes and helpers the cuts leave unused are deleted;
- no production file changes, and no new test except a `parametrize` case folding removed ones.

A task finds each finding's tests by name, since `main` may have moved since the audit commit. A
finding whose cut or kept test changed after it (`git diff <audit commit> HEAD -- <file>`) is
proved again or dropped with the reason.

The task chat proves each kept test before cutting: by reading what it asserts, or by breaking the
behavior locally, seeing the kept test fail and discarding the break, which is never committed. The
handoff says how; the reviewer checks it. A finding whose kept test would not fail keeps its cut
test, and the handoff records the dropped finding.

The integration task's acceptance: no kept test named by any finding was removed; the run's row in
this brief's Past runs gives the findings closed, the findings dropped with the reason, and the
size of the files in scope at the audit commit and at the integration tip (counted as in Setup);
and the findings file is deleted, since findings aren't kept once the run is done.

## How to run

One chat per step. Set the chat's model and effort in the app before sending the prompt. If a chat
stops partway, start a new one with the same prompt.

| Step | Owner's part |
| --- | --- |
| Finding pass | Act on a failing baseline |
| Trim plan | Merge `trim-<id>-findings` into `main` to approve the findings and plan, and set the plan's status to approved ([plan workflow](../plans/README.md)) |
| Plan tasks, normal `task` workflow | Approve the integration merge into `main` |

```text
Run the finding pass of test trim run <id>, scope <full | since commit>, in
docs/review/test-trim.md, including its setup. Report findings only; change no tests. Stop and ask
me only where the brief says to. Finish with the brief's Session end and report in three to five
lines: groups done, finding count and estimated lines removed per group.
```

```text
Draft the trim-<id> plan in docs/review/test-trim.md on the trim-<id>-findings branch, as its Trim
plan section says. Don't merge trim-<id>-findings; I approve by merging it. Finish with the brief's
Session end and report in three to five lines: number of tasks and the estimated lines removed.
```
