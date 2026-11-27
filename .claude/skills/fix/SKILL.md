---
name: fix
description: Implement a standalone feature or bug fix in MComix (not part of a plan), in the main chat, then have an independent reviewer subagent check it. Use when the owner asks for a cohesive change outside plan execution.
argument-hint: "<what to change>"
---

# Fix: $ARGUMENTS

1. **Scope.** State the behavior to change and how you'll know it works. Read the files the
   AGENTS.md Roadmap lists for the feature. If the change needs
   several tasks, propose a plan to the owner instead.
2. **Checkout.** If `git rev-parse --path-format=absolute --git-dir --git-common-dir` prints the same
   path twice, you are in the primary checkout: call `EnterWorktree`. If the owner says no other
   agent is working in the repo, skip `EnterWorktree` and branch where you are;
   `git status --short --branch` must first show `## main` and nothing else (otherwise call
   `EnterWorktree`). Then `git switch -c fix-<short-name> main` (or the branch the owner names) and
   record the base commit. A worktree has no `.venv`: `docs/agents/validation.md` (Running the
   check).
3. **Implement** the direct approach (AGENTS.md Simplicity). A fix gets a regression test that
   fails on the old code (`docs/agents/tests.md`); while iterating, run focused tests for what you
   changed. For a UI change, run the app as `docs/agents/validation.md` (UI and manual checks) says.
4. **Simplify.** Read your own diff against the base and trim it before validating.
5. **Validate** per `docs/agents/validation.md` and AGENTS.md Commands: the full check before review
   (a documentation-only fix needs only `--quick`). Commit.
6. **Review.** Start a `reviewer` subagent with: the request and acceptance criteria, the checkout
   path, the diff `git diff <base>...fix-<short-name>`, your check evidence
   (`docs/agents/validation.md` Evidence) and a draft of the step 7 Open items lines. Handle each
   finding (`docs/agents/review.md` Handling findings), re-validate, then resume the same reviewer
   with SendMessage for round 2. Each review-fix commit's body names the round and the finding it
   fixes. Rounds and when round 2 is skipped: `docs/agents/review.md`.
7. **Open items.** Add what's left for the owner (manual checks, decisions, follow-ups), with
   `fix-<short-name>` as the id, to `docs/TODO.md`, and commit it on the fix branch.
8. **Merge** into `main` only with the owner's approval, once the `docs/agents/merging.md`
   conditions hold.
   - Run `git merge-base --is-ancestor main fix-<short-name>` on its own; exit 0 means `main` is
     already in the branch. Otherwise `git merge main` on the fix branch, resolve any conflicts
     there and run the checks `docs/agents/validation.md` says.
   - Write the merge message (subject `Merge fix-<short-name>`, one body line per finding and its
     resolution, or `No review findings`, plus a line naming any change made after round two and
     the evidence line) to a scratchpad file.
   - Ask the owner to approve the merge, naming any finding left open and any pending manual
     check. Without approval, leave the branch (and its worktree) and give the owner the
     `git merge --no-ff -F <file> fix-<short-name>` command.
   - On approval, get to the primary checkout on `main`. If you used `EnterWorktree`: with
     `git status --short` empty, `git switch --detach`, and `git branch --contains HEAD` listing
     `fix-<short-name>`, leave with `ExitWorktree` `remove` and `discard_changes: true`; it deletes
     the worktree and its starting branch, and `fix-<short-name>` stays. If you branched in the
     primary checkout: with `git status --short` empty, `git switch main`. Otherwise the app owns
     the worktree and `main` is out of reach: give the owner the merge command and leave the
     branch.
   - There, `git status --short --branch` must show `## main` and nothing else, and
     `git merge-base --is-ancestor main fix-<short-name>` must still succeed. Then run
     `git merge --no-ff -F <file> fix-<short-name>` and `git branch -d fix-<short-name>`. If a
     condition fails, or the merge stops on a conflict (`git merge --abort`), give the owner the
     merge command and keep the branch.

   Never push.
9. **End** per AGENTS.md (End of a chat): the merge sha, or what kept the fix unmerged, and the
   Open items added.

For an extra bug sweep on a risky change, the built-in `/code-review` can run alongside the reviewer;
it doesn't know the acceptance criteria, so it never replaces the reviewer.
