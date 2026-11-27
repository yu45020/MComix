@AGENTS.md

## Claude Code mechanics

- The owner starts the owner-only skills as `/task <plan> <task-id>` and
  `/close-plan <plan>`.
- Change repo files with Edit and Write, never with shell redirects, `sed -i`
  or scripts. Use the shell to read and run tools; shell writes are for
  scratch files.
- Commit-message files (AGENTS.md Sessions and Git) go in the scratchpad. A
  subject plus the attribution trailer may use two `-m` flags instead.
