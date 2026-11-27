---
name: close-plan
description: Clear the blocking Open items a plan's integration task left, then merge the plan into main with the owner's approval. Use only when the owner asks to close a plan.
argument-hint: "<plan>"
disable-model-invocation: true
---

# Close $ARGUMENTS

The integration task closes its plan itself (`/task` Close) unless blocking Open items
(`docs/plans/README.md` Closeout) stopped it. This chat clears them and merges. Don't start
unassigned plan tasks; report them for the owner to assign with `/task`.

1. **Checkout.** Work in your own worktree (`EnterWorktree` if this is the owner's primary
   checkout), switched to the integration branch. A worktree has no `.venv`:
   `docs/agents/validation.md` (Running the check). State lives in the branches and the plan.
2. **Inspect.** Read the plan by heading: status line, task statuses and Open items. Check
   `git status`, `git branch --list` and `git worktree list`. Every task must be `done` and merged;
   otherwise report what is missing and stop.
3. **Clear** each blocking Open item:
   - an owner decision or a pending manual check: ask the owner, batching the questions in one
     `AskUserQuestion` call, and record the answer in the plan's Decisions or the manual result in
     the integration task's Evidence line. A deferred item becomes a follow-up;
   - a code change (an open finding, a failed check, a conflict with `main`, or what an answer
     needs): make it on a `<plan>-close-fixes` branch from the integration branch and run the full
     check. Start a fresh `reviewer` on `git diff <integration>...<plan>-close-fixes` with the items
     it clears, the Decisions and design notes they touch and the check evidence. Handle findings
     (`docs/agents/review.md` Handling findings), rerun the checks `docs/agents/validation.md` asks,
     resume the same reviewer with SendMessage for round 2 (rounds and when it is skipped:
     `docs/agents/review.md`), then merge as in `/task` Merge step 3.
4. **Record.** Move the plan's non-blocking Open items to `docs/TODO.md` (`docs/plans/README.md`
   Closeout); unresolved blockers stay in the plan. Commit.
5. **Merge** as in `/task` Close steps 2–4, deleting the close-fixes branch at release. With a
   blocker left, don't merge: end naming it.
6. **End** per AGENTS.md (End of a chat), then stop.
