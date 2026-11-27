# Publishing the public branch

The local `main` keeps the full working history. GitHub
(<https://github.com/yu45020/MComix>) gets a cleaned-up copy instead: the
local `public` branch, pushed as the remote's `main`. `public` starts at the
last upstream SourceForge commit, `5b5850fb`, and holds the fork's work as
squashed commits whose files match local `main`.

A squash keeps the files but records no link to `main`, so `git log --graph`
and the VS Code graph draw `public` and `main` as separate lines that meet
only at `5b5850fb`. That is expected.

Every sync replaces `public`'s files with `main`'s, so make every change on
`main` first. A commit made only on `public` is dropped by the next sync.

Run the commands in PowerShell from the repository root, with `main` (not
`public`) checked out: `git branch -f` refuses to move the checked-out
branch. PowerShell needs the quotes around `'main^{tree}'`. `git
commit-tree` writes a commit without touching the working folder or any
branch; `git branch -f` then moves `public` to it only if `commit-tree`
succeeded.

## Create `public`

Make one commit whose files are `main`'s and whose parent is the upstream
base, then point `public` at it:

```powershell
$c = git commit-tree 'main^{tree}' -p 5b5850fb -m "MComix fork: changes since upstream 5b5850fb"
if ($LASTEXITCODE -eq 0) { git branch -f public $c }
```

## Update `public`

Fetch, then add a commit on top of the published `main` that copies local
`main`'s files. Building on `origin/main` rather than the local `public`
keeps `public` one fast-forward ahead of GitHub, even if the local branch
was rebuilt or amended since the last push:

```powershell
git fetch origin
$c = git commit-tree 'main^{tree}' -p origin/main -m "Sync with main"
if ($LASTEXITCODE -eq 0) { git branch -f public $c }
```

Don't run `git merge main` on `public`, which would bring in all of `main`'s
history. Don't run `git merge --squash main` either: Git still treats
`5b5850fb` as the common base and remerges every change since it, so the
files changed since the last sync conflict.

To keep `public` at a single commit instead, rebuild it from `5b5850fb` with
the create command. This rewrites the published history, so the push then
needs `--force-with-lease`. Anyone who cloned the repository has to reset to
the new commit.

## Check before pushing

List what you are about to publish, and compare its files with `main`:

```powershell
git log --oneline origin/main..public
```

```powershell
git diff --stat main public
```

After an update the log shows the one `Sync with main` commit and the diff
prints nothing. A long log means `public` points at `main`'s history: don't
push; run the update again.

## Push

Push `public` as the remote's `main`. Never push the local `main`, which
would publish its full history.

```powershell
git push origin public:main
```

After a rebuild, overwrite the remote only if it is still at the commit you
last fetched:

```powershell
git push --force-with-lease origin public:main
```
