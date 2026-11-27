---
name: task
description: Implement and review one owner-assigned task of an approved MComix plan; its integration task also closes the plan.
---

# Implement one plan task

Follow `AGENTS.md`, `docs/plans/README.md` and the shared workflow documents;
`.codex/INSTRUCTIONS.md` supplies the common Codex mechanics. Implement, test, review and integrate
only the assigned task, then stop. Only the plan's integration task performs Close below.

## Start or resume

1. **Resolve the assignment.** The plan name and task ID are sufficient; do not ask the owner to
   paste information the plan records. Read the plan from its integration branch, or from `main`
   if that branch does not exist. Use headings and targeted ranges for the plan's approval status,
   `Integration branch:` line, assigned task section, cited Decisions and dependency handoffs.
   Read the design notes the task lists and the files the AGENTS.md Roadmap lists for the feature.
   Verify the plan is approved, dependencies are done and integrated, and no owner decision blocks
   the task. Do not choose an unassigned task.
2. **Recover state.** Use the common Codex mechanics before creating anything. If the task branch
   is checked out in another session's worktree, preserve it and report the path.
3. **Checkout.** Use the task's recorded `Branch:`. If the integration branch is absent, create it
   from `main` with `git branch <integration> main` without switching the primary checkout. If the
   plan on the integration branch already shows the integration task done but its Close didn't
   finish, check out the integration branch and continue with Close. Otherwise create a new task branch
   and isolated worktree from the integration branch, or resume the assigned task checkout. Take
   revisions from `git rev-parse`; keep the original base on resume. Use the task checkout for
   subsequent calls. A worktree has no `.venv`: `docs/agents/validation.md` (Running the check).
   For a new integration-task branch, merge current `main` into it before implementation and
   validation when `git log <integration>..main` lists commits. Its merge later brings those
   commits into the integration branch.

## Implement

- State the direct approach briefly, then implement and test in the main chat under AGENTS.md
  Simplicity. Every automated acceptance criterion needs a test, existing or new, that would fail
  if the feature broke. Follow `docs/agents/validation.md` for checks and commits. For a UI change,
  run the app as its UI and manual checks section says and record the evidence.
- The plan's integration task: move the Decisions and Gotchas that outlive the plan out of it
  (`docs/plans/README.md` Branches and task loop).
- Read the full diff against the recorded base and trim unnecessary code and tests before
  validating. Commit checked units.
- Write this task's Handoff in its plan section using `docs/plans/README.md`, and commit it.
  Touch only this task's section until owner decisions or integration require shared plan edits.
  Keep draft Open items for the reviewer in the current chat; publish them during Merge so
  parallel tasks do not compete for the shared Open items section.

## Review

1. Use the common native review mechanics with the task section and draft Open items. Ordinary
   tasks use `git diff <base>...<task-branch>`. The integration task uses the whole-plan diff,
   `git diff main...<task-branch>`, plus task review history from
   `git log --merges --format=%B main..<integration>`; ask it to focus on cross-task behavior and to check
   that no Decision or Gotcha later work needs is left only in the plan.
2. Follow `docs/agents/review.md` for findings and verification, and `docs/agents/validation.md`
   for check timing. Each review-fix commit's body names the round and finding.

## Merge

1. Resolve drafted owner decisions before integration and record answers in the plan's Decisions
   on the task branch. An answer requiring code changes follows `docs/agents/review.md`.
2. Serialize integration and shared plan edits. With a clean task checkout, switch it to the
   integration branch. If that branch is checked out elsewhere, preserve the task and report the
   blocker; never force the switch or switch the owner's primary checkout. Reconcile an advanced
   integration branch, resolving conflicts only in this task's scope.
3. Write a merge message to a temporary file: subject `Merge <task-branch>`, one body line per
   review finding and its resolution (or `No review findings`), any change made after round two,
   and validation evidence. Run `git merge --no-ff -F <file> <task-branch>`. A code-changing conflict
   resolution needs the full check (`docs/agents/validation.md`); a clean integration merge needs
   no additional tests.
4. Mark this task done, finalize its Handoff Evidence line with the validated revision, and add
   its Open items on the integration branch. Commit. For an ordinary task, release the integration
   branch and clean up only this chat's owned task worktree and merged task branch under the
   common Codex mechanics. For the integration task, continue with Close first.

## Close (the integration task only)

Still on the integration branch after Merge:

1. Move non-blocking Open items to `docs/TODO.md` as `docs/plans/README.md` Closeout requires.
   Commit.
2. With no blocking Open item left, run `git merge-base --is-ancestor main <integration>` on its
   own. If `main` is not an ancestor, merge it in, resolve documentation conflicts and run the
   checks `docs/agents/validation.md` requires. Abort a merge that conflicts in code; record that
   conflict or a failing check as a blocking Open item and commit the record.
3. Release the integration branch and clean up only this chat's owned task resources under the
   common Codex mechanics. Retain the integration branch while its merge into `main` is pending.
4. With no blocking item left and the `docs/agents/merging.md` conditions satisfied, ask the owner
   to approve the merge unless they already have. On approval, merge from the primary checkout
   with `git merge --no-ff --no-commit <integration>`, `git rm -q docs/plans/<plan>.md` and
   `git commit -F <file>`, so the merge deletes the plan (`docs/plans/README.md` Closeout). The
   message's subject is `Merge <integration>`; its body names the plan file it removes and
   validation evidence. Never switch the
   owner's primary checkout to satisfy a merge condition. If the merge conflicts, abort it.
   Delete the integration branch only after a successful merge. Otherwise preserve it and give
   the owner the blocker and the three merge commands (`--no-commit` merge, `git rm`,
   `git commit -F`). The owner assigns `close-plan` to clear
   blocking Open items. Never push.

If cleanup fails after finalizing a task's records, preserve the remaining resources and keep the
completed task marked done. Record the remaining owner follow-up in the plan's Open items on the
integration branch and commit the record before ending the chat.

End under AGENTS.md End of a chat, then stop.
