# Merging into `main`

Every merge into `main` needs the owner's approval; the tools' skills hold the steps. Approval
given with the request ("fix this and merge it") counts. Without it, ask once the conditions
below hold, naming anything that doesn't; without an answer, leave the branch and give the owner
the merge command.

- A standalone fix is ready to merge, with `--no-ff`, when all of these hold:
  - review cleared within two rounds with no finding left open ([review.md](review.md));
  - the checks [validation.md](validation.md) requires passed on the commit being merged, or
    every later commit is documentation-only;
  - no manual check is pending;
  - the owner's primary checkout is on `main` with a clean `git status`.
- A plan's integration branch is ready to merge, with `--no-ff`, when all of these hold:
  - the plan has no blocking Open item ([plan workflow](../plans/README.md) Closeout);
  - the full check exited 0 on the commit being merged, or every later commit is
    documentation-only or a clean merge of a task or `<plan>-close-fixes` branch into the
    integration branch;
  - the owner's primary checkout is on `main` with a clean `git status`.

For either: a manual step that doesn't test the change, such as restarting the app, is not a
pending manual check. If `main` moved past the branch, merge it in first and run the checks
[validation.md](validation.md) asks after that merge. The last message gives the merge sha; the
owner undoes the merge with `git revert -m 1 <sha>`.
