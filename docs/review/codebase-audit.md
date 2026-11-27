# Codebase audit

A codebase review in two read-only passes, each followed by a fix plan, then an outcome step. Agents
run the passes; the owner reads only the Owner summary of the findings file and approves the plans.
The brief is reused for every audit run.

## Runs

Each run has an **id**, the year and month it starts (`2026-12`, then `2026-12b` for a later run
that month) unless the owner names another, and a **scope**:

- **Full:** every slice, every file.
- **Since `<commit>`:** only the code changed since that commit (Slices, "Since scope"), usually the
  previous run's pass 2 audit commit. This is the default once a run has finished; for the first
  run the owner may name any commit.

The owner starts a run. A useful trigger is `git diff --shortstat <previous pass 2 audit commit>..main -- mcomix`
passing a few thousand changed lines, or three or four plans merged since the last run.

A run's names:

| Item | Name |
| --- | --- |
| Pass 1 and pass 2 branches | `audit-<id>-complexity`, `audit-<id>-bugs` |
| Fix plans | `audit-<id>-deletes`, `audit-<id>-fixes` |
| Findings file | `docs/review/audit-<id>/findings.md` |

Past runs, with their Outcome (below). A run's deleted findings file is read from history:
`git show <commit>^:docs/review/audit-<id>/findings.md`, where `<commit>` is
`git log -1 --format=%H -- docs/review/audit-<id>/findings.md`.

- **b6d6a** · full · audit commits `b6d6a4d9` / `09ac0f30` · fix plans `audit-b6d6a-deletes`
  (merged as `aa1c3f1c`) and `audit-b6d6a-fixes` (merged as `74f8f291`).
  - **Size:** `mcomix/` 24,639 → 22,850 → 23,074 lines (−1,565); `test/` 14,997 → 15,023 →
    16,323 lines (+1,326); test functions 543 → 536 → 598.
  - **Findings:** pass 1, 119 confirmed (92 delete, 27 decision): 116 closed, 1 dropped as moot,
    2 left as `docs/TODO.md` items. Pass 2, 66 (3 mismatch, 62 bug, 1 test-add): all closed; the 4
    unproven bugs were each proven by a failing test.
  - **Finders:** one finder per slice. Verifier rejections: pass 1, 3 of 122; pass 2, 6 of 72.
  - **Owner time:** pass 1, running S3–S7 in parallel and the 27 decisions in the delete plan's
    chat (24 approved, 2 deferred, 1 moot). Between the passes, the brief change
    `fix-audit-brief-sources`; whether pass 1 should class old-data removals as deletes was left
    as a `docs/TODO.md` item. Pass 2, running it as pass 2 of b6d6a rather than a new run, the 3
    mismatches and the clipboard form (one choice covering two findings) in the fix plan's chat,
    and three more during tasks (finished books, a vanished engine file, navigation while a book
    loads). Manual checks at both closeouts. No session stopped or ran over a usage window.

## Order and why

1. **Complexity.** Every piece of code must trace to a source that needs it, or be a candidate for
   removal.
2. **Delete plan.** Confirmed deletions, and the rewrites the owner approves, are implemented and
   merged into `main` before the bug hunt, so pass 2 reads less code and never reports bugs in code
   that is about to disappear. With no confirmed or unproven delete and no approved decision, there
   is no delete plan, and pass 2 starts once the owner merges the pass 1 branch.
3. **Mismatches, bugs and tests,** on the smaller `main`. Each slice's code and
   tests are read together, once.
4. **Fix plan** from the confirmed pass 2 findings.
5. **Outcome,** once the fix plan is merged into `main`: what the run changed, measured the same way
   every run, so the next run can compare.

## What is audited

Both passes read all the code, whether the fork wrote it or upstream did: the fork never merges
upstream changes in and keeps no code for data an older version or upstream wrote (AGENTS.md), so
nothing is kept only to match upstream or to read old data.
`mcomix/_vendor/`, `mcomix/messages/` and `mcomix/images/` are never audited.

## Sources

What code traces to in pass 1, and what pass 2 compares the code with:

- AGENTS.md: the Roadmap's flows, feature table and recipes, the Conventions and the Rules.
- What the slice's "Read first" names (Slices), including its `docs/requirements/` files.
- The edge states in `docs/agents/review.md` ("Edge states").
- For code none of these names: plan Decisions and acceptance criteria, and the history. Find the
  merge with `git log --merges`, or `git log` / `git blame` on the lines in question. A standalone
  fix records its reason in the merge commit body; a plan's merge body names the plan file. In
  pass 1, `ChangeLog.md` is an index of that history: an entry records a change as it was made,
  so one that a later change overrode traces nothing, and pass 2 never compares the code with it.

A plan Decision that describes an earlier state of the code is not a finding: later work may
legitimately differ from the plan that introduced it.

## Branches and sessions

Each pass has an **audit commit**, the tip of `main` when the pass starts, and a branch created
there (Runs). Session start and end follow [test-trim.md](test-trim.md) "Branches and sessions",
with the pass's branch in place of the findings branch; the chats that draft the fix plans follow
them too. The owner approves a pass by merging its branch into `main` with `--no-ff`.

## Setup (first session of each pass)

- **Baseline:** run the full check ([validation.md](../agents/validation.md)) at the audit commit
  and record its evidence in the findings file. A failing baseline goes to the owner before any
  review starts.
- **Slices:** list the modules
  (`git ls-tree -r --name-only <audit commit> -- mcomix tools mcomixstarter.py`, less
  the never-audited folders), give each to its slice by the table's first-match rule, and record in
  the findings header the modules no path matches. Edit the table on the pass branch only when a
  package or a module family was added, removed or renamed.
- **Tests:** pass 2 assigns every test file to the slice whose code it tests, recorded in the
  findings header ([test-trim.md](test-trim.md) "Groups" is a starting point).
- **Pass 2 leads:** record per slice in the findings header the delete plan's Decisions, the
  "Pass 2 leads" of the findings' Owner summary and the `docs/TODO.md` items that name a bug or a
  missing test in the slice, marked as leads to check. Pass 1 decisions the owner deferred are not
  pass 2 findings; they stay in `docs/TODO.md`.
- Pass 1 creates `docs/review/audit-<id>/findings.md` (format below), with the run's scope, models
  and effort in its header.

## Slices

Pass 1 reviews one slice at a time; pass 2 runs its finders together (Per slice, every pass). Paths
are relative to `mcomix/`, except S7; prefix them with `mcomix/` in a git command. A file belongs to
the first slice, in table order, whose path it matches (so `bookmark_dialog.py` is in S2); a module
that matches none (`i18n.py`, `log.py` and the like) goes to S5. Every slice
reads the AGENTS.md Roadmap; "Read first" adds what else the slice reads.

| Slice | Paths | Read first |
| --- | --- | --- |
| S1 Archives and external tools | `archive/`, `archive_tools.py`, `archive_extractor.py`, `archive_packer.py`, `process.py` | the Roadmap's "Open a file" flow, `docs/requirements/archive-formats.md`, `docs/requirements/archive-reading.md` |
| S2 Files, navigation and stored state | `file_handler.py`, `file_provider.py`, `recent.py`, `last_read_page.py`, `bookmark_*.py`, `atomic_file.py`, `preferences.py`, `constants.py`, `tools.py`, `portability.py` | the Roadmap's "Open a file" flow, `docs/requirements/archive-reading.md`, `docs/requirements/config-files.md`, `docs/requirements/opening-books.md` |
| S3 Pages and rendering | `image_handler.py`, `image_tools.py`, `render_cache.py`, `webp_animation.py`, `zoom.py`, `layout.py`, `box.py`, `transform.py`, `scrolling.py`, `enhance_backend.py`, `histogram.py`, `lens.py` | the Roadmap's "Draw a page" flow, `docs/requirements/animated-webp.md`, `docs/requirements/archive-reading.md` |
| S4 Upscaling | `upscale.py`, `upscale_backends/`, `upscale_models/` (its README; the models are binary) | `docs/requirements/neural-upscaling.md` |
| S5 Main window, input and threads | `main.py`, `run.py`, `__main__.py`, `event.py`, `keybindings*.py`, `ui.py`, `callback.py`, `worker_thread.py`, `cursor_handler.py`, `status.py`, `osd.py`, `slideshow.py`, `pageselect.py`, `clipboard.py` | the Roadmap's "Threads → GTK" flow, every file in `docs/requirements/` |
| S6 Dialogs, thumbnails and library | `*_dialog.py`, `preferences_page.py`, `preferences_section.py`, `properties_page.py`, `edit_*.py`, `thumbbar.py`, `thumbnail_*.py`, `library/`, `openwith*.py`, `dialog_handler.py` | every file in `docs/requirements/` |
| S7 Tooling | repo-root `tools/`, `mcomixstarter.py` | `docs/agents/upscale-tooling.md`, `docs/agents/validation.md` |

**Since scope.** List the files changed since the named commit,
`git diff --name-status <commit> <audit commit> -- mcomix tools mcomixstarter.py test`, and give
each to its slice:
code by the slice paths, tests by the findings header's assignment. A slice's review set is:

- its changed files;
- its files that call a function the diff changed or removed, or that held a call the diff removed,
  since a removed caller can leave dead code in an unchanged slice. A file that a changed file
  merely calls is not added: `main.py` and `preferences.py` touch every slice.

A slice with an empty review set is skipped, and its findings section says so. The finders read the
review set whole and may open any other file to trace a finding. A slice gets a full review when at
least half its files are in the review set, or when the owner names it in the prompt. Record each
slice's review set in the findings header.

## Per slice, every pass

1. **Find.** Start a `reviewer` in audit mode with this brief's path (Audit mode), the pass, the
   worktree path and audit commit, the slice paths (in a Since run, the slice's review set),
   in pass 2 the slice's tests and leads, the slice's "Read
   first" files, and the primary checkout's Python path for the flake8 hint. When the owner's
   prompt asks for a second finder on a slice, start a second
   `reviewer` with the same prompt in a fresh context, and label the two finders A and B.
2. **Merge.** The main chat merges the reports, drops duplicates, and notes for each finding which
   finder found it where there were two.
3. **Verify.** Another `reviewer`, which hasn't seen the slice, gets the same inputs as the finders
   plus the merged list, verifies it and tags each finding confirmed, rejected or unproven, reading
   the cited code and its surroundings, and corrects a finding's kind where the Pass questions give
   it another. This is a separate role, not a second review round.
4. **Record.** Add confirmed and unproven findings to the findings file; give only a count for the
   rejected ones. In pass 1, add the bugs and mismatches the finders noticed to the Owner summary's
   "Pass 2 leads". In pass 2, add the finders' trim notes to `docs/TODO.md`, each line starting
   with the pass branch's name. Commit after each slice, so a stopped session loses at most one
   slice.

Pass 2 runs step 1 for every slice at once, unless the prompt asks for batches: then S1–S3, then
S4–S7. Once the finders are done, take the slices through steps 2–4 one at a time, in table order.
A stopped session loses the unrecorded slices whose finders ran; on resume, rerun the finders of
every slice with no section for the pass.

There is no limit on unproven findings; the following plan decides what to do with them.

## Audit mode (reviewer instructions)

When the prompt names a pass and a slice from this brief instead of a diff, review the slice's files
at the audit commit as they stand, using that pass's questions in place of the reviewer checklist.
Read whole files, not excerpts. In
pass 1, list apart, one line each, any bug or mismatch you notice: it becomes a pass 2 lead. Every
finding also names the source it relies on (Sources), or says "none" (bugs and complexity need
none). The reviewer's Report rules apply, except that you also
report uncertain findings, marked uncertain, and that a mismatch's fix gives both options, changing
the code and changing the text, without choosing. When the prompt asks you to verify findings
instead, tag each one confirmed, rejected or unproven, with the evidence (a traced scenario, a code
reference or a narrow test). There is no round 2 in audit mode.

## Pass questions

**Pass 1: complexity**

- **Trace:** for each module, class, public function, parameter or option, cache and layer, name
  the source that needs it (Sources) or a traced caller that does. Untraced code is a candidate
  for removal.
- **Rewrite test:** for each suspect module, sketch the simplest version that meets
  the same behavior, with an estimated size. A much smaller sketch means the extra complexity isn't
  justified.
- **Mechanical hints**, to point at suspects and never findings on their own:
  `<the Python path the prompt gives> -m flake8 --select C901 --max-complexity 15 <files>`. Also look for
  abstractions with a single caller, parameters only ever passed their default, pass-through
  wrappers, duplicated helpers, and code nothing calls (search for its callers).
- Classify each finding as **delete** (no behavior change; the existing tests still pass, except
  tests of the deleted code itself) or **decision** (changes behavior or a recorded decision, such
  as a rewrite; the owner decides). Removing a feature the Roadmap lists is always a decision:
  only the owner knows whether they use it. Removing code that only reads or migrates data an
  older version or upstream wrote is a delete: AGENTS.md already settles it.
- Restyling alone is never a finding.

**Pass 2: mismatches, bugs and tests**

- **Mismatches:** where the Roadmap or a "Read first" file states behavior, compare it with the
  code. A **mismatch** finding is a difference in intended behavior:
  the code does something other than the text says, or the text describes behavior no code
  implements (search outside the slice before reporting). Quote the words that differ, cite the
  code and give one scenario that shows the difference. Don't say which side is right: the owner
  decides whether the code or the text changes. A failure in some scenario of behavior the code
  otherwise implements is a bug, not a mismatch.
- **Bugs:** correctness bugs, including every edge state in `docs/agents/review.md` ("Edge states")
  the slice reaches, each with a traced scenario. Breaches of the AGENTS.md
  Conventions and Rules are bug findings as they stand, and so are breaches of the
  `docs/agents/tests.md` rules (real data, timing assertions, writes inside the repo) in the
  tests. A breach of style alone (quotes, type hints, comment wording) is not a finding.
- **Tests:** a reachable edge state that loses or corrupts the user's files or settings, with a
  traced scenario and no test that would fail, is a **test-add** finding. A bug finding needs no
  test-add: its fix brings a regression test (tests.md "Adding a test").
- **Old data is not a finding.** A failure that only a file or value written by an older version
  or by upstream MComix can cause is neither a bug nor a test-add (AGENTS.md).
- **Trims are not findings.** A test, step or case you notice that tests.md "Removing a test" calls
  a trim goes in a separate list, one line each with its trim rule, for `docs/TODO.md`.

## Findings file

`docs/review/audit-<id>/findings.md`, created on the pass 1 branch and extended on the pass 2 branch:

- **Header:** the run's scope, then per pass its audit commit, baseline evidence, the models and
  effort used, the slice and test assignments from Setup, pass 2's leads
  per slice, and in a Since run each slice's review set.
- **Owner summary** (what the owner reads): counts per pass and slice (confirmed, unproven,
  rejected), every **decision** finding and every **mismatch** finding, and after pass 1 the
  **Pass 2 leads**.
- **Findings,** one section per pass and slice. Each finding is one entry:
  `P<pass>-<slice>-<nn>` · kind (delete, decision, mismatch, bug, test-add) · confirmed or
  unproven · finder (A, B or both, on slices with two finders) ·
  `file:line` · what is wrong · scenario or source · suggested fix.

## Fix plans

Both plans are drafted with the `draft-plan` skill on their pass's branch, with the findings file as
the settled design; commit the draft on that branch, not on `main` (this overrides the skill's
commit step and its commit approval question). Group findings by area into one task per area, split
further only as the Tasks comment of `docs/plans/TEMPLATE.md` allows; each task lists the finding
ids it closes. An unproven finding enters its task as "prove it first, or drop it with the reason".

The drafting chat asks the owner the decision and mismatch findings in the skill's decision step,
batched. Each answer is recorded as a Decided item with its task; a finding the owner defers
becomes a `docs/TODO.md` line starting with the pass branch's name, so the draft has no Open
decision. The owner then approves the findings and the plan together by merging the pass branch and
setting the plan's status to approved ([plan workflow](../plans/README.md)); the plan follows the
normal task workflow. A task finds each finding's code by name, since `main` may have moved since the
audit commit, and proves again or drops a finding whose code changed after it.

- **`audit-<id>-deletes`** (after pass 1): confirmed delete findings, plus the decision findings the
  owner approves. Unproven
  delete findings prove themselves by a trace showing no source and no caller. Pass 2 starts only
  after this plan's integration branch is merged into `main`.
- **`audit-<id>-fixes`** (after pass 2): the pass 2 findings. An
  unproven bug enters as "prove with a failing test first"; that test stays as the fix's regression
  test. A test-add first extends or parametrizes an existing test (tests.md). A settled mismatch
  becomes a task that changes the code or the text.

## Outcome

Once the fix plan's integration branch is merged into `main`, one chat records the run's outcome
under Past runs, on a documentation branch merged like a standalone fix. Each item is a line or
two:

- **Size**, at the pass 1 audit commit, the pass 2 audit commit and the fix plan's merge into
  `main`: lines in `mcomix/` and `test/`, from
  `git grep -c -e "" <commit> -- "mcomix/*.py" ":!mcomix/_vendor/*"` and
  `git grep -c -e "" <commit> -- "test/*.py"`, and test functions, from
  `git grep -c -E "^\s*def test_" <commit> -- "test/*.py"`. Sum each output.
- **Findings:** per pass, how many were closed, dropped (with the plan's reason) or left as
  `docs/TODO.md` items.
- **Finders:** the verifier's rejections per pass and, on slices with two finders, the confirmed
  findings only A found, only B found and both found. Next run's owner uses them to keep or drop
  the verifier and a second finder.
- **Owner time:** the decisions the owner settled, and any session that stopped or ran over a usage
  window.

The same commit deletes the findings file: findings aren't kept once the run is done. The pass
branches' merges into `main` still have it.

## How to run

One chat per step, in order. Set the chat's model and effort in the app before sending the prompt;
reviewers use the model and effort of the reviewer definition unless the prompt says otherwise, and
the findings header records what each pass used. If a chat stops partway, start a new one with the
same prompt; the resume rule picks up at the slices with no section.

| Step | Owner's part |
| --- | --- |
| Pass 1 Complexity | Act on a failing baseline |
| Delete plan | Answer the decision findings in the chat, then merge the pass 1 branch and approve the plan |
| Delete plan tasks, normal `task` workflow | Approve the integration merge into `main` |
| Pass 2 Mismatches, bugs and tests | Act on a failing baseline |
| Fix plan | Answer the mismatch findings in the chat, then merge the pass 2 branch and approve the plan |
| Fix plan tasks, normal `task` workflow | Approve the integration merge into `main` |
| Outcome | Approve its merge into `main` |

Prompts, one per chat. Replace `<id>` and `<scope>` (full, or since a commit). To ask for a full
review of some slices in a Since run, a second finder on a slice, or pass 2 batches, add a line to
the prompt.

```text
Run pass 1 (complexity) of codebase audit run <id>, scope <scope>, in docs/review/codebase-audit.md,
including its setup. Report findings only; fix nothing. Stop and ask me only where the brief says
to. Finish with the brief's Session end and report in three to five lines: slices done, and counts
of delete and decision findings (confirmed / unproven / rejected).
```

```text
Draft the audit-<id>-deletes plan of the codebase audit in docs/review/codebase-audit.md on the
audit-<id>-complexity branch, as its Fix plans section says. Don't merge audit-<id>-complexity; I
approve by merging it. Finish with the brief's Session end and report in three to five lines:
number of tasks, the areas they cover, and the decisions I deferred.
```

```text
Run pass 2 (mismatches, bugs and tests) of codebase audit run <id>, scope <scope>, in
docs/review/codebase-audit.md, including its setup. Report findings only; fix nothing. Stop and ask
me only where the brief says to. Finish with the brief's Session end and report in three to five
lines: slices done, confirmed / unproven / rejected counts, and the number of mismatch, bug and
test-add findings.
```

```text
Draft the audit-<id>-fixes plan of the codebase audit in docs/review/codebase-audit.md on the
audit-<id>-bugs branch, as its Fix plans section says. Don't merge audit-<id>-bugs; I approve by
merging it. Finish with the brief's Session end and report in three to five lines: number of tasks,
the areas they cover, and the decisions I deferred.
```

```text
Write the Outcome of codebase audit run <id> in docs/review/codebase-audit.md, as its Outcome
section says. Report in three to five lines: the size change, the findings closed and dropped, and
what the finder counts suggest for the next run.
```

In a full run, expect pass 2 to be the longest (seven slices of code and tests) and possibly to
need a second chat. Start it in a fresh usage window: hitting the limit while finders run discards
their work. Each pass reviews its fixed audit commit, so work merged into `main` meanwhile doesn't
affect it.
