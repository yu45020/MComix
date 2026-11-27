---
name: close-plan
description: Clear an MComix plan's remaining merge blockers when the owner asks to close it.
---

# Close out a plan

Follow `AGENTS.md`, `docs/plans/README.md` and the shared workflow documents;
`.codex/INSTRUCTIONS.md` supplies the common Codex mechanics. The integration task closes its plan
itself (`.agents/skills/task/SKILL.md`, Close) unless blocking Open items stopped it. This chat
clears those blockers and finishes the merge; it does not complete unfinished plan tasks.

1. **Inspect and recover.** Read the plan from its integration branch by heading: status, task
   statuses and Open items. Inspect Git status, branches, worktrees, commits and any existing
   closeout review. Verify every task is done and integrated;
   otherwise report the missing work for owner assignment and stop. Use the common Codex
   mechanics to recover an interrupted closeout.
2. **Checkout.** Reuse this closeout's assigned checkout or create an isolated worktree on the
   integration branch under the common Codex mechanics. Preserve another session's checkout and
   branch ownership; report an occupied integration branch rather than forcing a switch. Use the
   closeout checkout for subsequent calls. A worktree has no `.venv`:
   `docs/agents/validation.md` (Running the check). Record the starting integration revision with
   `git rev-parse` before editing; keep the original closeout base on resume.
3. **Clear blockers.** The main chat makes changes and runs checks.
   - Group questions for owner decisions and pending manual checks. Record answers in the plan's
     Decisions or manual results in the integration task's Evidence line. Convert a blocking item
     to a follow-up only when the owner explicitly defers it.
   - Make code changes on `<plan>-close-fixes` from the integration branch, preserving an existing
     closeout branch on resume. Run the full check and commit checked work. Use the common native
     review mechanics with the blockers being cleared and
     `git diff <integration>...<plan>-close-fixes`. Include any earlier closeout documentation
     changes since the recorded base in the same review. Follow `docs/agents/review.md` for
     findings and verification, and `docs/agents/validation.md` for check timing. Name the round
     and finding in review-fix commit bodies. Merge the cleared fixes into the integration branch
     with `--no-ff`, using the task skill's Merge message and conflict/validation rules. Leave task
     statuses unchanged; release the closeout checkout in step 5.
4. **Record.** Remove resolved Open items and move only non-blocking follow-ups to `docs/TODO.md`,
   tagged with the plan name, as `docs/plans/README.md` Closeout requires. Keep unresolved blockers
   in the plan. Commit the records, naming any documentation change after round two in the merge
   message under the shared review rules. A documentation-only closeout needs `--quick` and no
   review (`docs/agents/review.md`); reuse the plan's existing validation evidence.
5. **Merge and release.** Follow the task skill's Close section for catching up with `main`, the
   required checks, release, the merge conditions and the owner's approval. Never switch the
   owner's primary checkout to satisfy a merge condition. Delete the close-fixes branch only after
   verifying its work is integrated, and clean up only this chat's owned resources under the
   common Codex mechanics. Record non-blocking cleanup failures discovered during final release in
   `docs/TODO.md`, tagged with the plan name; keep unresolved blockers in the plan.
   Preserve the integration branch until its merge into `main` succeeds. If merging is blocked,
   give the owner the blocker and the task skill's three merge commands (`--no-commit` merge,
   `git rm` of the plan file, `git commit -F`). Never push.
6. **End.** Follow AGENTS.md End of a chat, then stop.
