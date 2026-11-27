# Working with coding agents

This repository ships the instructions, skills and safety settings its
maintainer uses with [Claude Code](https://docs.anthropic.com/en/docs/claude-code)
and [OpenAI Codex](https://developers.openai.com/codex). If you have no setup
of your own, you can use them as they are. They are optional: they don't
affect the app or its tests, and contributors who don't use agents can ignore
them.

The workflow they encode is small, reviewed changes: an agent works on a
branch, runs the checks, commits, has an independent reviewer agent check the
diff, and merges into your local `main` only when you approve. Agents never
push; publishing is yours.

## What's included

| Path | Used by | Purpose |
| --- | --- | --- |
| [AGENTS.md](../AGENTS.md) | both | Shared rules: environment, commands, code map (Roadmap), conventions, git rules, when to stop and ask |
| [docs/agents/](agents/) | both | Rules read at a step: [tests](agents/tests.md), [validation](agents/validation.md) (which check to run), [review](agents/review.md), [merging](agents/merging.md), [upscaling tools](agents/upscale-tooling.md) |
| [docs/requirements/](requirements/README.md) | both | Behavior each feature must keep; agents read it before changing that feature |
| [docs/plans/](plans/README.md) | both | Plan workflow and template for multi-task work; plans in progress live here and are deleted when merged |
| [docs/review/](review/) | both | Briefs for a whole-codebase audit and a test-suite trim, with ready prompts |
| [docs/TODO.md](TODO.md) | both | Open items agents leave for you: decisions, manual checks, follow-ups |
| [CLAUDE.md](../CLAUDE.md) | Claude Code | Imports AGENTS.md and adds Claude Code mechanics |
| [.claude/skills/](../.claude/skills/) | Claude Code | The `fix`, `task`, `draft-plan` and `close-plan` skills |
| [.claude/agents/reviewer.md](../.claude/agents/reviewer.md) | Claude Code | Read-only reviewer subagent |
| [.claude/settings.json](../.claude/settings.json) | Claude Code | Runs the checks and routine git commands without prompting; denies `git push`; registers the hook |
| [.claude/hooks/guard-git.mjs](../.claude/hooks/guard-git.mjs) | Claude Code | Blocks `git push` and package installs or removals in any shell command |
| [.claude/rules/tests.md](../.claude/rules/tests.md) | Claude Code | Reminds the agent of the test rules when it touches `test/` |
| [.codex/INSTRUCTIONS.md](../.codex/INSTRUCTIONS.md) | Codex | Codex mechanics: worktrees, recovery, native review |
| [.agents/skills/](../.agents/skills/) | Codex | The same four skills, written for Codex |
| [.codex/agents/reviewer.toml](../.codex/agents/reviewer.toml) | Codex | Read-only reviewer role |

## Before you start

- Set up the development environment first ([CONTRIBUTING.md](../CONTRIBUTING.md)):
  agents run `.venv\python.exe tools/check.py`, and the instructions assume
  Windows, PowerShell and the conda-forge `.venv`.
- Where AGENTS.md and the skills say **the owner**, they mean you, the person
  directing the agent in your clone. Read the top of [AGENTS.md](../AGENTS.md)
  for what the owner decides, its Rules for what agents may do (they never push
  and ask before installing anything), and "End of a chat" for when they stop
  and ask.

## Claude Code

Open the repository folder in Claude Code (terminal, desktop app or IDE).
`CLAUDE.md` loads `AGENTS.md` automatically, and the project settings, skills,
reviewer and hook apply without further setup. The hook runs with `node`, so
install [Node.js](https://nodejs.org/) and keep it on `PATH`; without it, the
settings still deny `git push`, but package installs aren't blocked.

| Command | Use it to |
| --- | --- |
| `/fix <what to change>` | Make one standalone feature or bug fix. You can also just describe the change; the agent picks this skill. |
| `/draft-plan [<plan-name>]` | Turn a design you have settled in the chat, or items from `docs/TODO.md`, into a plan in `docs/plans/`. |
| `/task <plan> <task-id>` | Implement one task of an approved plan. Only you start it. |
| `/close-plan <plan>` | Clear what stopped a plan's merge into `main`. Only you start it. |

The reviewer subagent runs on Opus. If your account has no access to it,
change `model:` in `.claude/agents/reviewer.md` in your clone. Keep personal
permission or model overrides in `.claude/settings.local.json`, which git
ignores.

## Codex

Open the repository in Codex. It reads `AGENTS.md` itself, and AGENTS.md sends
it to `.codex/INSTRUCTIONS.md` before any workflow step. Ask for a workflow by
naming its skill, for example "use the fix skill to make the library window
remember its size". The skills are in `.agents/skills/`, and Codex reviews with
the `reviewer` role from `.codex/agents/reviewer.toml`.

Codex creates its worktrees under `.codex_worktrees/`, which git ignores.
Codex has no equivalent of the Claude Code hook, so the rules against pushing
and installing packages rest on AGENTS.md alone; keep Codex's own approval
settings on.

## The workflows

### A standalone fix (`fix`)

1. You describe the change.
2. The agent reads the code map in AGENTS.md and the requirements for that
   feature, then works on a fix branch (`fix-<name>`, or `codex/fix-<name>`
   under Codex), in its own worktree unless you tell it no other agent is
   working in the repository.
3. It adds a regression test for a bug, runs the checks
   [validation.md](agents/validation.md) asks for, and runs the app for a UI
   change.
4. It commits, then starts a fresh reviewer on the exact diff. It handles each
   finding (fixes it, rebuts it with evidence, or defers it to an Open item)
   and has the same reviewer verify that in a second round. A finding still
   open after that comes to you.
5. It records what's left for you (manual checks, decisions, follow-ups) in
   `docs/TODO.md`.
6. It asks you to approve the merge into `main`. Without your approval it
   leaves the branch and gives you the merge command and the file holding the
   merge message.

### A plan (`draft-plan`, `task`, `close-plan`)

For work too large for one fix:

1. Settle the design in a chat, then run `draft-plan`. It asks you the open
   decisions, writes the plan (tasks, each with its branch, dependencies and
   the files it owns), and has a reviewer check the draft. Working in a
   worktree, it commits the draft on a `draft-<plan>` branch and gives you the
   command to merge it into `main`.
2. Approve the plan by setting its status line on `main` to
   `Status: **APPROVED** · Updated: <date>`, or tell an agent to.
3. Start each task in its own chat with `task <plan> <task-id>`. Tasks marked
   parallel-safe can run at the same time. Each task is reviewed, then merged
   into the plan's integration branch, and leaves a handoff in the plan for the
   tasks after it.
4. The last task tests the plan's work together, moves the leftover items to
   `docs/TODO.md`, and asks you to merge into `main`. The merge deletes the
   plan file; git history keeps it.
5. If something blocks that merge (a pending manual check, a decision, an open
   finding), run `close-plan <plan>` later to clear it and merge.

### Audits and test trims

[docs/review/codebase-audit.md](review/codebase-audit.md) and
[docs/review/test-trim.md](review/test-trim.md) each end with "How to run": the
steps, what you do at each, and the prompt to paste into a new chat.

## Contributing back

- For anything larger than a bug fix, open an issue first, as
  [CONTRIBUTING.md](../CONTRIBUTING.md) asks.
- When the agent asks to merge into `main`, decline if you want to send the
  work as a pull request: the branch stays, and the agent names the file
  holding the merge message, which lists the review findings and the check
  results. Review the diff, push the branch to your fork and open a pull
  request with that message as its description.
- Before pushing, look at the lines the agent added to `docs/TODO.md`: keep
  those the maintainer needs (a pending manual check, say) and drop the ones
  that only concern your clone.

## Using another agent

Many coding agents read `AGENTS.md`. Together with `docs/agents/` and
`docs/plans/README.md`, it holds the rules, which don't depend on a tool. The
skills do: they call tools only Claude Code or Codex has, such as their
worktree commands and reviewer subagents. To follow a skill with another
agent, give it the steps and replace those calls with plain
`git worktree add` and a reviewer in a separate chat.
