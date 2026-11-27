---
name: draft-plan
description: Write or revise an MComix plan when requested, using a settled design or owner-selected TODO items.
---

# Draft an implementation plan

Follow `AGENTS.md`, `docs/plans/README.md` and the shared workflow documents;
`.codex/INSTRUCTIONS.md` supplies the common Codex mechanics. Use `docs/plans/TEMPLATE.md` so both
Codex `task` and Claude `/task` sessions can start without this conversation. Drafting authorizes
neither implementation nor plan approval.

1. **Find the source.** Use the design, feature or fixes settled in this conversation, or the
   owner's selected `docs/TODO.md` items. Preserve agreed decisions and exclusions without
   reopening them. Use the owner's plan name or propose a short kebab-case name. If no design is
   settled, ask for the missing design before inventing tasks. Revise an existing draft only
   where needed; preserve an approved plan and report conflicts instead of rewriting it.
2. **Gather inputs.** Read the files the AGENTS.md Roadmap
   lists for the feature, their tests and dependency handoffs by heading and range. For TODO
   items, read the merged plans they cite from history, as `docs/plans/README.md` Closeout says.
   Inspect enough code to establish feasible
   interfaces, ownership, task size and existing coverage.
3. **Settle material choices.** Record existing owner decisions as Decided with their reasons;
   do not ask again. Group genuinely unresolved scope, behavior or interface questions, with a
   recommendation and alternatives, using available user-input tools. Continue independent
   planning while answers are pending; silence is not agreement. If the owner requests a draft
   with open choices, mark them Open and keep the plan in draft status. Explicit owner approval
   is required before execution.
4. **Write the shared draft.** Verify checkout ownership, branch and Git state before editing;
   use an owner-assigned writing checkout when provided and preserve unrelated work. Fill the
   template's draft status, integration branch, goal, scope, Decisions, Execution and task fields.
   The integration branch is the plan name. Describe acceptance criteria and constraints rather
   than implementation recipes. Define dependencies, owned files, coverage and manual checks. Put
   shared interface decisions where downstream tasks can read them before dependency handoffs
   exist. Size tasks as the template requires, with an integration task that also closes the plan;
   do not add a separate closeout task. Remove only TODO items the plan takes over. Leave
   implementation Handoffs pending.
5. **Check agreement and cold-start usability.** First compare the draft with what the owner
   agreed. Ask about contradictions or additions that change architecture, an interface or a
   dependency before review, as `docs/agents/review.md` Plans requires; do not silently choose a
   side. Then apply its Plans checklist: each task must run with its own section, cited Decisions
   and dependency handoffs. Check parallel ownership, shared files, integration coverage and
   manual criteria; specify only requested measurements. Remove empty optional sections and
   unintended scaffold placeholders, retaining required sections and fields reserved for
   implementers as pending.
6. **Review.** Run documentation-only checks under `docs/agents/validation.md`. Start a fresh
   native `reviewer` using the common mechanics, with the draft path, agreed scope and exclusions.
   Follow `docs/agents/review.md` for findings and verification. Do not run application tests
   solely for drafting a plan.
7. **Commit and report.** Use the owner-assigned branch when provided and follow shared
   documentation validation and commit rules. In a worktree with no assigned branch, commit on
   `draft-<plan>` without asking, merge `main` into it if `main` is not an ancestor, release the
   branch with `git switch --detach`, and give the owner `git merge draft-<plan>`, run on `main`,
   then `git branch -d draft-<plan>`. Committing on `main` requires owner authorization:
   reuse existing authorization, or ask when the reviewed draft is ready. Without it, leave the
   draft uncommitted and report that. Permission to commit is separate from approval to execute
   the plan. Do not switch a dirty checkout, commit unrelated changes, start implementation or
   merge a plan as part of drafting. End under the shared reporting policy with the draft path
   and unresolved owner decisions.
