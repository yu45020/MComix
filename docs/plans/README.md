# Plan workflow

How every agent drafts and executes a plan. The rules that also apply outside plans are in
AGENTS.md and `docs/agents/` (validation, review, merging); each tool's skills define the steps.

While a plan runs, `docs/plans/<plan>.md` is its state: scope, Decisions, acceptance criteria,
dependencies, task status, handoffs and Open items. Its merge into `main` deletes it (Closeout).

## Planning and coordination

- Draft every plan from [TEMPLATE.md](TEMPLATE.md), so each task header carries its branch,
  dependencies, parallel safety and owned files.
- A draft becomes executable when the owner approves it and its status line reads
  `Status: **APPROVED** · Updated: <date>` on `main`. The owner makes that edit, or an agent the
  owner tells to (an owner-approved doc edit on `main`).
- Use cohesive tasks and useful interface handoffs, not a task per coding step. Plans describe
  acceptance criteria and decisions, not implementation recipes.
- State a fact a task needs that is slow to piece together from the source (every call site a rule
  covers, say) in its acceptance criteria or a Decision.
- The owner schedules tasks and assigns each to its own chat, which owns that task's status,
  implementation, review fixes and integration, then stops.
- Only tasks marked parallel-safe may run concurrently, each with its own worktree and assignment.
  Serialize integration and shared plan edits; reconcile an advanced integration branch when merging.

## Branches and task loop

- Start the integration branch `<plan>` from `main`; task branches `<plan>-<k>-<name>` start from
  `<plan>`.
- Start only approved, dependency-ready tasks. Merge only after independent review clears
  ([review.md](../agents/review.md)) and the required validation passes
  ([validation.md](../agents/validation.md)).
- Each task's review covers `git diff <plan>...<task branch>`.
- A plan's integration task (the last task, which verifies the plan's workflows together) is
  reviewed against the whole plan diff, `git diff main...<task branch>`, not only its task diff, so
  cross-task edge states get one look. It is the plan's only whole-plan review, so its reviewer also
  gets the task review history (`git log --merges --format=%B main..<plan>`). The integration
  task's branch starts from `<plan>` with `main` merged in, so its tests and review see the other
  work on `main`. After its merge into `<plan>`, the integration task closes the plan (Closeout
  below).
- The plan file is deleted at closeout, so the integration task first moves what outlives the plan.
  A plan Decision, or a handoff Decision or Gotcha, that still constrains later work goes where that
  work will read it: `docs/requirements/` for behavior that must hold, a comment at the code for a
  constraint or quirk. Its reviewer checks that none is left only in the plan.
- A finished task is merged into `<plan>` with `--no-ff`, without asking, with the review findings
  and their resolutions in the merge commit body (the integration review reads them) and its
  validation evidence in [validation.md](../agents/validation.md) form. It is marked done with its
  handoff and Open items (below). Then release the task checkout and delete the merged task branch
  (each tool's skill has the steps).

## Handoffs, Open items and reports

Two readers, two places. The next agent reads handoffs; the owner reads only the Open items.

- **Handoff:** in the task's section, at most about 15 lines, for the next task's agent. Describe
  the code as it ended up, not how it got there. Leave out empty fields, restated acceptance
  criteria, things not done, test counts and step-by-step history.

  ```markdown
  - **Handoff:**
    - **Interfaces:** what later tasks call or rely on: functions, classes, preferences, files.
    - **Decisions:** choices made beyond the plan, one line each with the reason.
    - **Gotchas:** traps the next task would hit.
    - **Tests:** where the coverage lives and which fakes or fixtures to extend.
    - **Measurements:** only those the plan requested.
    - **Evidence:** `<validated revision>` · validation: <command/check scope> exit 0
      · review: <rounds>, <N fixed, M rebutted, K asked, L deferred; leave out zeros>
      · manual: <criterion> checked (how).
      Finalize this line after review.
  ```

- **Open items:** one plan section listing everything left for the owner, one line each with its
  task id: manual acceptance checks still pending, owner decisions, unresolved findings and
  follow-ups (unrelated work found along the way). Add a task's lines when it is marked done on the
  integration branch, so parallel tasks don't conflict (create the section if the plan lacks it);
  remove a line when it is done. Pending items go here, not in handoffs.

## Closeout

The integration task closes its plan once it is merged into `<plan>`:

- The Open items that don't block the merge (follow-ups, deferred ideas, flaky tests) move to
  `docs/TODO.md`, tagged with the plan name, so there is one copy.
- Plans and handoffs are not kept after the plan is done: the merge into `main` deletes
  `docs/plans/<plan>.md` in the merge commit (`git merge --no-ff --no-commit <plan>`,
  `git rm docs/plans/<plan>.md`, then commit). The merge commit's second parent still has it:
  `git show <merge>^2:docs/plans/<plan>.md`, where `<merge>` is
  `git log -1 --format=%H --grep="^Merge <plan>$" main`.

These block the merge into `main` and stay in the plan: a pending manual acceptance check
([merging.md](../agents/merging.md) says what isn't one), an owner decision (including one a
requested measurement needs), a finding still open after round two, a code change made after round
two, and a merge of `main` that conflicts in code or fails its checks. With none left, the plan is
ready to merge into `main` ([merging.md](../agents/merging.md)). Otherwise the integration task
stops, and a later closeout chat clears them, moves what is left to `docs/TODO.md` and merges.
The owner may defer a blocking item, which makes it a follow-up.
