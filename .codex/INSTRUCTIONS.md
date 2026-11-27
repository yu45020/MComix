# Codex workflow instructions

Follow `AGENTS.md` and its linked policies. The selected skill (AGENTS.md Workflows) supplies the
workflow sequence; this file adds the Codex mechanics.

## Checkout and recovery

- Inspect Git status, worktrees and available chat attachments; reuse only a checkout assigned to
  this work. Create isolation when the skill requires it.
- On resume, recover state from the branch, worktree, plan when present, commits and current chat. Preserve the
  original base revision and review history; no separate checkpoint file is required.

## Worktrees

- Create new worktrees with plain Git only under `<primary>/.codex_worktrees/`. `<primary>` is the
  parent of `git rev-parse --path-format=absolute --git-common-dir`, even from inside a worktree.
  Use the skill's branch and base with a unique checkout name:
  `git worktree add -b <branch> <primary>/.codex_worktrees/<name> <base>`.
  For an existing branch, use `git worktree add <path> <branch>` under the same directory.
  Do not use the app's `create_worktree` tool for new checkouts.
- Set the assigned checkout's absolute working directory explicitly for subsequent commands.
  Never switch the primary checkout to satisfy a merge condition (the `fix` skill's branch in the
  primary checkout is the exception). After committing integration records, use
  `git switch --detach` to release the integration branch.
- Remove only this chat's owned, clean, merged worktrees. Verify the resolved path, and verify
  both HEAD and the work branch's tip are ancestors of the destination. Use `git worktree remove`
  for manual worktrees or `archive_worktree` for existing managed ones; report cleanup failures
  without forcing removal. Delete only merged branches released from all worktrees. Preserve
  branch-only deliveries and unintegrated work.

## Native review

- Use the native `reviewer` role defined in `.codex/agents/reviewer.toml` with fresh context
  (`fork_turns: "none"`). Supply the checkout, exact diff or draft, criteria, relevant Decisions
  and design notes, check evidence and draft Open items.
- Retain the reviewer ID and round. Resume the same reviewer with `followup_task` when idle;
  a status message does not start a new turn. Follow `docs/agents/review.md` for findings and rounds.

## Command execution

- Retain command session IDs and wait for completion; empty output is not a result. Preserve each
  command's exit code and stop dependent operations after failure.
