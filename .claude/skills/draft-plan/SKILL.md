---
name: draft-plan
description: Draft an implementation plan (docs/plans/<plan>.md) from docs/plans/TEMPLATE.md for a feature or a batch of fixes settled in this conversation, or for items from docs/TODO.md. Use when the owner asks to draft or write up a plan.
argument-hint: "[<plan-name>]"
---

# Draft plan $ARGUMENTS

The plan is for `/task` sessions (and Codex `task` chats) that start cold: each reads only
its own task section, the Decisions it cites and its dependencies' handoffs. Write it so that is
enough. Drafting doesn't authorize starting the plan.

1. **Find the source.**
   - The design, feature or fixes settled in this conversation, or items the owner picks from
     `docs/TODO.md`. Name the plan from the argument, or propose a short kebab-case name. If nothing
     is settled here, ask the owner for the design instead of inventing one.
   - If `docs/plans/<plan>.md` exists and is approved, report it and any conflicts instead of
     rewriting it. If it is a draft, update only what is needed and keep the owner's decisions.
2. **Gather inputs** by heading and range, not whole files: the files the AGENTS.md Roadmap lists
   for the feature (enough to place interfaces and size tasks), and for a TODO item the merged plan
   it names, read from history as `docs/plans/README.md` Closeout says.
3. **Settle decisions before writing.** Record what the owner already agreed in this conversation as
   **Decided**, with the reason; don't ask again. List only the genuinely open choices, each with your
   recommended default, and ask the owner with `AskUserQuestion` (at most four per call).
4. **Write the draft** from `docs/plans/TEMPLATE.md`: status `DRAFT — pending owner approval`, the
   integration branch (the plan name), goal and scope, Decisions, Execution, and tasks with every
   header field filled. State acceptance criteria and decisions, not implementation recipes. Delete
   the `docs/TODO.md` items the plan takes over.
5. **Check the draft** against `docs/agents/review.md` Plans before committing, and fix what fails.
6. **Review the draft.** Start a `reviewer` on the draft with its path, the Decisions and the design
   notes it relies on. Handle each finding (`docs/agents/review.md` Handling findings), and resume
   the same reviewer once with SendMessage (rounds and when round 2 is skipped:
   `docs/agents/review.md`).
7. **Commit** the draft, after `--quick` (`docs/agents/validation.md`).
   - In the owner's primary checkout (`git rev-parse --path-format=absolute --git-dir --git-common-dir`
     prints the same path twice): commit on `main`, which needs the owner's approval (AGENTS.md
     Sessions and Git). Ask for that approval in the same `AskUserQuestion` call as the step 3
     questions. It covers the draft and its revisions in this chat until the owner approves the
     plan. Without it, leave the draft uncommitted.
   - In a worktree: `git switch -c draft-<plan>` (`git switch draft-<plan>` if it exists) and commit
     there without asking. If `git merge-base --is-ancestor main HEAD` fails, `git merge main` and
     resolve conflicts there. Then `git switch --detach`, so the owner can delete the branch. The
     last message gives the owner `git merge draft-<plan>`, run on `main`, then
     `git branch -d draft-<plan>`.

   End per AGENTS.md (End of a chat): the draft's path and any open decisions.
