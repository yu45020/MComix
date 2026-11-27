---
name: fix
description: Implement and review a standalone MComix feature or bug fix outside approved plan execution.
---

# Fix a standalone feature or bug

Follow `AGENTS.md` and its linked workflow documents; `.codex/INSTRUCTIONS.md` supplies the common
Codex mechanics. The main chat owns implementation, checks and review fixes.

1. **Scope.** State the behavior to change and how you'll know it works. Read the files the
   AGENTS.md Roadmap lists for the feature. If the change needs
   several tasks, propose a plan to the owner instead of starting plan execution.
2. **Checkout.** Inspect Git status and worktrees. Use the owner-assigned branch and checkout when
   they belong to this work; otherwise create an isolated worktree from `main` on
   `codex/fix-<short-name>` (or the branch the owner names). If the owner says no other agent is
   working in the repo, skip the worktree and create the branch in the primary checkout:
   `git status --short --branch` must first show `## main` and nothing else (otherwise create the
   worktree). Record the base revision with `git rev-parse` and use that checkout as the working
   directory for subsequent calls. A worktree has no `.venv`: `docs/agents/validation.md` (Running
   the check).
3. **Implement.** Build the direct solution under AGENTS.md Simplicity. A fix gets a regression
   test that fails on the old code (`docs/agents/tests.md`); run focused tests while iterating. For a UI
   change, run the app as `docs/agents/validation.md` (UI and manual checks) says.
4. **Simplify.** Read the full diff against the recorded base and trim unnecessary code and tests
   before validating.
5. **Validate.** Run the required checks and record evidence under `docs/agents/validation.md`,
   then commit the checked work.
6. **Review.** Use the common native review mechanics for `git diff <base>...<fix-branch>`,
   including draft Open items from step 7. Follow `docs/agents/review.md` for findings and
   verification, and `docs/agents/validation.md` for check timing. Name the review round and
   finding in each review-fix commit's body.
7. **Open items.** Add pending manual checks, owner decisions and unrelated follow-ups, with the
   fix branch as the ID, to `docs/TODO.md`. Commit them on the fix branch.
8. **Merge** into `main` only with the owner's approval, once the `docs/agents/merging.md`
   conditions hold. Run
   `git merge-base --is-ancestor main <fix-branch>` on its own. If `main` is not an ancestor, merge
   it into the fix branch and run the checks `docs/agents/validation.md` requires. Write a merge
   message to a temporary file: subject `Merge <fix-branch>`, one body line per review finding and
   its resolution (or `No review findings`), any change made after round two, and validation
   evidence. Ask the owner to approve the merge, naming any finding left open and any pending
   manual check. On approval, merge from the primary checkout with
   `git merge --no-ff -F <file> <fix-branch>` only when `git status --short --branch` there shows
   `## main` and nothing else; never switch the owner's primary checkout to make that hold. The
   exception is a fix branched in the primary checkout: with `git status --short` empty,
   `git switch main` there. If the merge conflicts, abort it. After a successful merge, clean up
   only this chat's owned worktree and merged branch under the common Codex mechanics. Otherwise
   preserve the branch and checkout, and give the owner the blocker and merge command. Never push.
9. **End.** Follow AGENTS.md End of a chat, then stop.
