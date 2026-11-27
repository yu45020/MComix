---
name: task
description: Implement one owner-assigned task of an approved plan (docs/plans/<plan>.md) in the main chat, have an independent reviewer subagent check it, and merge it into the plan's integration branch; the plan's integration task then closes the plan and merges it into main with the owner's approval. Use only when the owner assigns a task.
argument-hint: "<plan> <task-id>, e.g. lazy-thumbnails LT-2"
disable-model-invocation: true
---

# Task $ARGUMENTS

You implement, test, review and merge this one task, then stop (`docs/plans/README.md`). Don't
pick another task. Only the plan's integration task closes the plan (Close below).

## Start or resume

1. **Recover state.** `<primary>` is the parent of
   `git rev-parse --path-format=absolute --git-common-dir`. The task branch name is in the task
   section, readable with `git show <integration>:docs/plans/<plan>.md`. The integration branch is
   the plan's `Integration branch:` line. If it doesn't exist yet, read the plan from `main` and
   create it with `git branch <integration> main` (no switch). The task's state is its branch, its
   worktree and the plan; there is no checkpoint file.
   - If `git worktree list` shows the task branch checked out in another worktree, another session
     has started the task (or died in it): stop and report that worktree's path to the owner, who
     either resumes that chat or commits or discards its work, runs `git worktree unlock <path>`
     (an `EnterWorktree` worktree is locked) and `git worktree remove <path>`, and reruns `/task`.
2. **Checkout.** If `git rev-parse --path-format=absolute --git-dir --git-common-dir` prints the same
   path twice, you are in the owner's primary checkout: call `EnterWorktree` first. A worktree has
   no `.venv`: `docs/agents/validation.md` (Running the check).
   - New task: `git switch -c <task-branch> <integration>` and record the base commit. For the
     plan's integration task, then run `git merge main` when `git log <integration>..main` lists
     commits (`docs/plans/README.md` Branches and task loop); `<integration>` gets them when the
     task merges.
   - Resume after the merge (the plan on `<integration>` already shows the integration task
     `done`, but its Close didn't finish): `git switch <integration>` and continue with Close.
   - Resume (the task branch exists): `git switch <task-branch>` here. Never restart a task with
     commits or reset an open review: `git log <base>..<task-branch>` shows the review rounds and
     findings through the review-fix commits (Review step 2). If the old reviewer is lost, report the
     round and open findings to the owner (`docs/agents/review.md`).
3. **Read the plan by heading** in your worktree (`grep -n "^#" docs/plans/<plan>.md`, then targeted
   ranges): status line, Open items, this task's section, the Decisions it cites and its
   dependencies' handoffs. Stop and report if the plan is a draft, a dependency isn't `done`, or an
   open owner decision blocks the task. Read the design notes the task lists and the files the
   AGENTS.md Roadmap lists for the feature, and grep for the rest.

## Implement

- State the direct approach in two or three lines (AGENTS.md Simplicity), then implement.
- Every automated acceptance criterion needs meaningful coverage: a test, existing or new, that would
  fail if the feature broke. Record evidence or "pending" for manual criteria.
- UI tasks: run the app as `docs/agents/validation.md` (UI and manual checks) says.
- The plan's integration task: move the Decisions and Gotchas that outlive the plan out of it
  (`docs/plans/README.md` Branches and task loop).
- Commit each checked unit. Read your full diff against the base and simplify it, then run the
  checks `docs/agents/validation.md` asks before review.
- Write this task's **Handoff** in its plan section using the `docs/plans/README.md` template, and
  keep a draft of its Open items lines (pending manual checks, decisions, follow-ups) for the review
  prompt. Touch only this task's section of the plan (Merge step 1 is the exception). Commit it.

## Review

1. Start `subagent_type: reviewer` with: the diff `git diff <base>...<task-branch>` (for the plan's
   integration task, the whole-plan diff `git diff main...<task-branch>`, `docs/plans/README.md`)
   and the worktree path, the task section and Decisions, the design notes it lists, your check
   evidence and the drafted Open items lines (pending manual checks live there, outside the diff).
   For the integration task, add the task review history
   (`git log --merges --format=%B main..<integration>`) and ask it to focus on cross-task issues and to check that
   no Decision or Gotcha later work needs is left only in the plan.
   Note its agent ID and the round. Copy every hash from `git rev-parse` output (AGENTS.md Sessions
   and Git). The owner may also run `/code-review ultra` on the integration task's branch
   (owner-launched, billed separately); treat its findings like a reviewer's.
2. Handle each finding (`docs/agents/review.md` Handling findings), batch the fixes and rerun the
   checks `docs/agents/validation.md` asks after review fixes. Each review-fix commit's body names
   the round and the finding it fixes.
3. Resume the same reviewer with SendMessage for round 2, with one line per finding and the new
   evidence. Rounds and when round 2 is skipped: `docs/agents/review.md`.

## Merge

1. If a drafted Open item is a decision for the owner, ask it before merging and record the answer
   in the plan's Decisions on the task branch. An answer that needs a code change follows
   `docs/agents/review.md` (After round two).
2. With a clean worktree, `git switch <integration>`. If git says it's checked out elsewhere,
   another task is merging: report that to the owner rather than forcing it.
3. Write the message (subject `Merge <task-branch>`, one body line per review finding and its
   resolution, or `No review findings`, plus a line naming any change made after round two and the
   evidence line) to a scratchpad file, then `git merge --no-ff -F <file> <task-branch>`. Resolve
   conflicts only in this task's scope, then `git commit --no-edit`. If resolving them changed
   code, run the full check; after a clean merge, don't (`docs/agents/validation.md`).
4. In the plan, mark the task `done`, finalize the handoff's Evidence line and add its lines to Open
   items. Commit.
5. For the plan's integration task, continue with Close instead. Otherwise `git switch --detach`,
   then delete the task branch. If you used `EnterWorktree`, leave with `ExitWorktree` `remove`,
   which also deletes its starting branch; it reports the merged commits as not on `main`, so pass
   `discard_changes: true` once `git branch --contains HEAD` lists the integration branch.
   Otherwise the app owns the worktree and its branch.

## Close (the integration task only)

Still on `<integration>` in your worktree after Merge step 4 (`docs/plans/README.md` Closeout):

1. **Record.** Move the non-blocking Open items to `docs/TODO.md`, as Closeout describes.
   Commit.
2. **Catch up with `main`.** Run `git merge-base --is-ancestor main <integration>` on its own. If
   it fails, `git merge main`; resolve documentation conflicts, then `git commit --no-edit`, and
   run the checks `docs/agents/validation.md` says. A code conflict (`git merge --abort`) or a
   failing check becomes a blocking Open item; commit it.
3. **Release.** `git switch --detach`, then delete the task branch. If you used `EnterWorktree`,
   once `git branch --contains HEAD` lists `<integration>`, leave with `ExitWorktree` `remove` and
   `discard_changes: true`; the session is back in the primary checkout.
4. **Merge into `main`** if no blocking Open item is left and the `docs/agents/merging.md`
   conditions hold: `git status --short --branch` shows `## main` and nothing else, and
   `git merge-base --is-ancestor main <integration>` succeeds on its own. Ask the owner to approve
   the merge unless they already have. On approval, write the message (subject
   `Merge <integration>`, a body line naming the plan file it removes and the evidence line) to a
   scratchpad file, run `git merge --no-ff --no-commit <integration>`,
   `git rm -q docs/plans/<plan>.md` and `git commit -F <file>`, then `git branch -d <integration>`.
   If a condition fails, the owner doesn't approve, or the app owns the worktree, give the owner
   those commands and keep the branch. Never push.

With a blocking item left, skip step 2 and step 4, and end naming the items; the owner clears them
with `/close-plan`.

End per AGENTS.md (End of a chat), then stop. If the task stops before merging, name the blocker;
the task branch and plan keep the state.
