# <Plan title>

Status: **DRAFT — pending owner approval** · Updated: <YYYY-MM-DD>
Integration branch: `<plan>`

<!-- Shared plan template for Codex and Claude Code. Replace every <...>, delete these comments and
any section that would stay empty except Open items. Record what to build and why; how agents
execute tasks lives in docs/plans/README.md, AGENTS.md and the tools' skills, so don't copy
workflow rules here. -->

## Open items

<!-- One line per item with its task id (docs/plans/README.md Open items, Closeout). -->

## Goal and scope

- **Goal:** <the outcome in two or three sentences, and the owner request it serves>
- **In scope:** <...>
- **Out of scope:** <...>
- **Constraints:** <design notes and conventions that bind every task; files the agents may or may
  not read>

## Decisions

<!-- One per decision. Decided: settled with the owner, with the reason. Open: needs the owner,
with the recommended default and the alternatives. The plan is approved only when none is open. -->

### D1 — <topic> · Decided | Open

- <the decision or recommended default, and why>
- <alternatives, for an open decision>

## Execution

- **Order:** <ID>-1 → <ID>-2 → … (parallel safety lives in each task header)
- <anything the tasks share: fakes, fixtures, bundled files>

## Tasks

<!-- Default to one or two tasks and the integration task. Split further only for a reason the
draft names: parallel work the owner will run, a part the owner wants on a different model, or a
task that covers more than one independent feature or subsystem (a Decision that could ship on its
own is a sign). Recheck when an amendment adds a Decision to a task. Owns lists the files or areas
the task changes, so parallel-safe claims can be checked against overlap; `main.py`, `ui.py`,
`preferences.py` and the shared fakes in `test/_*.py` are touched by most features, so name them
when a task changes them. Status: pending | done. -->

### <ID>-1 — <title> · pending

- **Branch:** `<plan>-1-<name>` · **Depends on:** <none | <ID>-k> ·
  **Parallel-safe:** <no | with <ID>-k>
- **Read:** <D1, D2>; <design notes>; <dependency> handoff
- **Owns:** <files or areas>
- **Acceptance:** <behavior that must hold, testable> ·
  **Manual:** <checks made by running the app, and who makes them, or none>
- **Coverage:** <existing tests, fakes and fixtures to extend>
- **Handoff:** <written by the task, docs/plans/README.md format>

### <ID>-<n> — Integration and acceptance · pending

- **Branch:** `<plan>-<n>-integration` · **Depends on:** <all earlier tasks> · **Parallel-safe:** no
- **Read:** <Decisions>; <design notes>; <dependency handoffs>
- **Owns:** <integration tests and files that may need cross-task fixes; the AGENTS.md Roadmap and
  `ChangeLog.md` entries>
- **Acceptance:** <cross-task behaviors that must hold> ·
  **Manual:** <checks made by running the app, and who makes them, or none>
- **Coverage:** <existing interaction tests and fixtures; uncovered interactions to verify>
- **Measurements:** <only those this plan requests, or none>
- **Handoff:** <written by the task>

<!-- This task verifies integrated behavior; its review covers the whole plan diff. It then closes
the plan, which is merged into main unless a blocking Open item is left (docs/plans/README.md
Closeout). -->
