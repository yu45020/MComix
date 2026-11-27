# Review

Every plan task and standalone fix gets an independent review of its exact diff, and a drafted
plan gets one of its text. A closeout gets one for its code changes; a documentation-only
closeout gets none: the integration task's review covered the plan. Reviewers report findings
and never implement fixes; the chat that owns the work writes its code, runs its checks and
handles the findings.

## What reviewers check

Apply Every review, plus each section below that fits what the target contains; a mixed diff gets
all that fit. The reviewer chooses from the target, not the author. An audit follows its brief.

| The target contains | Section |
| --- | --- |
| Anything [validation.md](validation.md) doesn't call documentation-only: code, tests, configuration, dependencies | Code |
| A drafted plan | Plans |
| Any other documentation or agent instructions | Docs |

A task's handoff, Open items lines and status in its plan are the task's record, not a plan to
review.

## Every review

- **Intent:** the change does what the request, criteria or Decisions ask, and no more.
- **Rules:** the AGENTS.md Conventions and Rules hold, the Roadmap is updated when a file
  gained or lost a role, and `docs/requirements/` when a behavior it lists changed.
- **Complexity,** with the same weight as a bug (AGENTS.md Simplicity): name what could be deleted
  while keeping the criteria and the validation. For code, give the diff's net lines.
- **Evidence,** for a diff: reuse the author's check evidence, which is what
  [validation.md](validation.md) asks for the diff. It is valid if it covers the reviewed commit or
  every later commit is documentation-only (`git diff --stat <evidence>..<tip>`). Run a narrow
  test only to prove a concrete finding, never the full check. Report as a finding missing or
  stale evidence. The author's summary and handoff are claims to verify.
- **Manual checks and measurements,** for a diff: a UI change and each manual criterion have
  recorded evidence of how they were checked, or a visibly pending manual check in the drafted
  Open items; a measurement the plan requested has its result in the handoff.

## Code

- **Coverage:** a fix has a regression test that fails on the old code, and every automated
  acceptance criterion has a test, existing or new, that would fail if the feature broke.
- **Tests:** the [tests.md](tests.md) rules hold, including its bar for adding a test.
- **Asking for a test:** only for a failure you can show, not for style or unrequested
  measurements.
- **Bugs:** correctness bugs, tracing one scenario for each edge state the diff touches.

### Edge states

- Threads and GTK: a worker's result arriving after its widget or dialog was destroyed, after the
  file was closed or replaced, or after a newer request; GTK calls made off the main thread.
- Files and archives: missing, unreadable, password-protected or replaced while open; cancelling
  an extraction part-way; closing the app with busy workers.
- Names and paths: non-ASCII archive member names in a legacy encoding, case-only differences and
  long paths on Windows.
- Optional parts that are absent: 7z, PyMuPDF or mutool not installed, no GPU or ONNX
  runtime.
- Child processes (`spawn`): code that must be importable and picklable, and a child that dies or
  hangs.

## Plans

- **Goal:** every agreed item has an owning task, and each task supports the goal.
- **What the owner agreed:** the drafter compares the draft with the conversation and, before the
  review, asks the owner about each contradiction and each addition that changes the architecture, an
  interface or a dependency. Don't change the draft to match on your own: reading the code may have
  shown the draft is right.
- **Fit:** the files, interfaces and behavior the plan relies on exist as it says, and it agrees with
  the design notes and Decisions.
- **Cold start:** read each task with only its section, the Decisions it cites and its dependencies'
  handoffs. Its **Read** names everything it needs, including global constraints and Execution notes.
  List the questions its implementer would have to ask (edge behavior, including the Edge states its
  area reaches, preference names and defaults, UI placement); each is a Decision, or Open with a
  default.
- **Tasks:** split and owning files as the Tasks comment in [TEMPLATE.md](../plans/TEMPLATE.md) says;
  dependencies are acyclic and task order agrees with them; a task relies on others only through
  their handoff interfaces.
- **Parallel work:** **Parallel-safe** pairs have disjoint **Owns**, counting shared fakes and
  fixtures, and neither depends on the other.
- **Acceptance:** every criterion is observable and backed by coverage to extend or add, or is listed
  under **Manual** with who checks it and the expected result.
- **Duplication:** nothing repeats workflow rules from AGENTS.md or the skills.

## Docs

- **Accuracy:** paths, links, commands, names, labels and counts match the code and the files they
  cite.
- **One source:** each rule is stated in one file and linked from the others; changed text doesn't
  leave another file contradicting it.
- **Walk it:** for a changed rule or workflow, follow one real scenario as its reader would. Do they
  reach the right action with what the text gives them?
- **Agent instructions:** a workflow rule shared by the `.claude/` and `.agents/` or `.codex/` files
  says the same in each.

## Report

Name the sections you applied, then give a numbered list, most severe first: file:line, the failure
scenario (or maintenance cost) and a fix; "No findings" if none. For a plan or doc, the scenario says who
misreads it and what they then do wrong. In round two, mark each finding resolved or still open,
review the changes made since round one, and open no unrelated topics. A rebuttal or deferral is
resolved only if it meets Handling findings; a finding whose fix added more than it needed is still
open.

## Handling findings

For the chat that owns the work. An Open item is a line in the plan's Open items, or in
`docs/TODO.md` for work outside a plan. Resolve each finding one of these four ways, and name the
way, with a rebuttal's evidence, in the merge or commit body:

- **Fix** it.
- **Rebut** it, citing evidence the reviewer can read (file:line, a design note, a Decision, the owner's
  request quoted, a test or command output) that shows the finding is wrong: a stated fact is false
  or its failure can't happen. Low severity, a rare trigger, disagreeing or calling it out of scope
  doesn't show that.
- **Ask the owner** when a fix would change a Decided item or what the owner agreed, when Decided
  items conflict, when one leaves open a choice the fix must make, or when you can't resolve a real
  issue, such as a fix that costs more than the problem. Don't fix it. Write an Open item with the
  conflict, the evidence and the options, with your recommendation. It stays open and blocks the
  merge until the owner decides; then apply the answer. A plan draft asks in the chat instead.
- **Defer** a real issue the change neither caused, worsened nor was asked to fix to an Open item.
  A deferred finding doesn't count as open. Any other issue can't be deferred, whatever its
  severity.

A reviewer that fails or returns no report counts as not run, never as no findings.

- **Rounds:** at most two: the review and one verification by the same reviewer. With no findings
  in round one there is no round two. Never start a new reviewer to reset the count. A finding
  still open after round two goes to the owner as above and can't then be deferred. Report the
  round and open findings if the reviewer can't be resumed.
- **After round two:** a change is unreviewed. A documentation-only change may land if the merge
  body names it. For a code change in a plan task or closeout, ask the owner, or leave the reviewed
  code as it is and record the finding as an Open item for the integration task's review; in the integration task
  or a closeout, the change is a blocking Open item. A
  standalone fix with a code change after round two has a finding left open: name it to the owner
  when asking for the merge.
