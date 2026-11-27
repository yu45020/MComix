# Shared agent instructions

These repository rules apply to every coding agent. Claude Code reads
`CLAUDE.md`, which imports this file and adds its own mechanics. Codex reads
`.codex/INSTRUCTIONS.md` before any workflow step.

MComix: GTK 3 comic/image viewer in Python (PyGObject). Package in `mcomix/`,
run from source via `mcomixstarter.py`. Hard fork of the SourceForge
upstream, shaped to the owner's use: upstream changes are never merged or
rebased in, at most cherry-picked. No code is kept to read or migrate data
and settings that an older version or upstream wrote.

The owner decides scope, user-visible behavior, architectural commitments,
new dependencies and exceptions to agreed constraints. Within those
decisions, agents choose implementation details, investigate failures,
simplify code and complete the required checks without asking about routine
engineering choices. Escalate a choice that materially changes the agreed
outcome or constraints; the stop conditions under "End of a chat" still apply.

## When you…

Read the linked file at that step; these rules hold as written there.

| When you… | Read |
| --- | --- |
| write, change or remove tests | [docs/agents/tests.md](docs/agents/tests.md) |
| run tests or checks, commit or hand off | [docs/agents/validation.md](docs/agents/validation.md) |
| review, or handle review findings | [docs/agents/review.md](docs/agents/review.md) |
| merge into `main` | [docs/agents/merging.md](docs/agents/merging.md) |
| draft or work on a plan | [docs/plans/README.md](docs/plans/README.md) |
| export, benchmark or verify an upscaling model (`tools/`) | [docs/agents/upscale-tooling.md](docs/agents/upscale-tooling.md) |

## Workflows

Each tool has the same four skills, which hold the steps:

| Request | Skill |
| --- | --- |
| Standalone feature or fix | `fix` |
| One task of an approved plan | `task` |
| Clear the Open items that blocked a plan's merge into `main` | `close-plan` |
| Draft or revise a plan from a settled design | `draft-plan` |

Only the owner starts `task` and `close-plan`; suggest them when a request
matches. A request to discuss or review doesn't authorize implementation.

An Open item is a line in the plan's Open items, or in `docs/TODO.md` for
work outside a plan.

## Environment (Windows)

- `.venv` is a **conda prefix**, not a stdlib venv; `environment.yml` holds
  its channel rule and the commands that create and refresh it. Add a
  compiled package only from conda-forge (`mamba install -p ./.venv
  --override-channels -c conda-forge <pkg>`); pip is fine for pure-Python ones.
- `onnxruntime-windowsml` and `windowsml` (pinned in `pyproject.toml`) and
  Pillow are the pip-only exceptions; never also install conda-forge
  `onnxruntime`, `onnxruntime-directml` (same module, they clash) or `pillow`,
  directly or as another conda package's dependency (built without libavif,
  so AVIF pages are skipped).
- Shells: Git Bash strips backslashes, so write paths there with forward
  slashes; in PowerShell, quote revision expressions such as `'HEAD^{tree}'`.
- `start.bat` and `tools/check.py` drop MSYS2's `GI_TYPELIB_PATH` and `PATH`
  entries themselves (they make PyGObject load the wrong typelibs and DLLs),
  so any shell can start them. `tools/check.py` also adds the 7-Zip
  (`7-Zip-Zstandard`, then `7-Zip`) folders under `C:\Program Files` that
  exist, so the real-tool archive tests run, and gives pytest a
  per-checkout `--basetemp` under `%TEMP%\mcomix-check-<hash>`; run those
  tests through `tools/check.py --tests`. Run any other Python from
  PowerShell, not Git Bash, with this clean environment, which leaves the
  archive tools out:

  ```powershell
  $v = "$PWD\.venv"; Remove-Item Env:GI_TYPELIB_PATH -ErrorAction SilentlyContinue
  $env:PATH = "$v;$v\Library\bin;$v\Scripts;$env:SystemRoot\system32;$env:SystemRoot"
  & "$v\python.exe" ...
  ```

## Commands (clean environment, repo root)

- Run: `start.bat [file]` or `.venv\python.exe mcomixstarter.py [path]`
- Check: `.venv/python.exe tools/check.py` runs lint and the whole suite
  (about a minute); `--quick` runs lint only; `--tests <pytest args>` runs
  focused tests. Unless it runs `--quick`, give the command a timeout of at
  least 330 s: the script stops pytest after 300 s, and a shorter tool
  timeout would cut it first. In a worktree, run it with the primary
  checkout's `.venv\python.exe`. Its lint is flake8's pyflakes codes
  on `mcomix` and `test`; the style codes and mypy (`.venv\python.exe -m mypy
  mcomix`) fail on inherited code and are not gates.
- Tests: any failure is a regression (many tests skip without 7z or
  an optional package such as PyMuPDF, ONNX Runtime or torch; that's
  expected). Tests supply fixture passwords; `test/conftest.py` rejects
  unexpected password prompts.

When to run which check: [docs/agents/validation.md](docs/agents/validation.md).

## Roadmap

Before changing a feature, read every file listed for it below (paths are
under `mcomix/` unless noted), then grep for callers of what you touch.
`main.py` (`MainWindow`) owns most components as attributes
(`window.filehandler`, `window.imagehandler`, `window.thumbnailsidebar`, ...),
so check how it wires them. Update this map when files gain or lose a role.
Where a row names a file in `docs/requirements/`, it lists the behavior the
feature must keep: read it too, and see its
[README](docs/requirements/README.md) before changing that behavior.

### Main flows

- **Open a file:** `file_handler.py` (`FileHandler.open_file`) →
  `file_provider.py` (sibling files/folders) → `archive_tools.py` (detect
  type, pick backend) → `archive_extractor.py` (list thread; on-demand read
  or background extraction) → `image_handler.py` (page cache, pixbufs) →
  `main.py` (`_draw_image`).
- **Draw a page:** `main.py` `_draw_image` uses `zoom.py` (fit/zoom sizes),
  `layout.py` + `box.py` (page boxes, double page, manga order),
  `transform.py` (rotate/flip), `image_tools.py` (scaling, decoding,
  `load_pixbuf`), `scrolling.py` (smart scroll), `upscale.py` (optional
  neural upscaling: `UpscaleService.render` replaces the resize when a cached
  result exists).
- **Threads → GTK:** `callback.py` (`Callback` runs listeners in the main
  thread; `x += handler` weakly subscribes Python methods; destroyed
  dialog/widget owners explicitly detach), `worker_thread.py`.

### Feature → files

| Feature | Files |
| --- | --- |
| Archive formats | `archive/archive_base.py` (base API: `iter_contents`, `extract`, `read`, `supports_random_access`, `stored_name`), one backend per format in `archive/` (`sevenzip_external.py` also reads RAR, LHA and the ZIPs `zipfile` can't), registry in `archive_tools.py`; external tools via `process.py`; passwords `archive/password.py`; nested archives `archive/archive_recursive.py`; PDF in `archive/pdf_*.py` and `archive/native_pdf/` (one spawned child per archive); requirements `docs/requirements/archive-formats.md`, `docs/requirements/archive-reading.md` |
| Page loading / caching | `image_handler.py` (page cache, decoding, redraw delivery), `render_cache.py` (scaled pages, smart-background colours), `file_handler.py` (`get_image_source`, `read_file`, `materialize`), `archive_extractor.py`, `worker_thread.py`; requirements `docs/requirements/archive-reading.md` |
| Image decode / filters | `image_tools.py`, `enhance_backend.py` + `enhance_dialog.py`, `histogram.py`, `lens.py` (magnifier) |
| Animated WebP | `image_tools.py` (static poster, metadata), `webp_animation.py` (decoding, pixel budget, playback), `image_handler.py` (book/page lifetime), `main.py` (layout, frame replacement, memory notice), `preferences.py` + `preferences_dialog.py` (cache limit, enlargement); thumbnails and backgrounds use the poster, neural upscaling skips playback; requirements `docs/requirements/animated-webp.md` |
| Neural upscaling | `upscale.py` (service: eligibility, inference and resize workers; owned by `ImageHandler`, used in `main.py` `_draw_image`), `upscale_backends/` (`onnx_sr.py`: ONNX Runtime adapter, tiling; `windows_ml.py`: providers, TensorRT-RTX engine cache built in a spawned child, DirectML fallback; GPU only, never falls back to the CPU), `upscale_models/` (bundled fp16 models and attribution; default `SPANF_YC_2x`), `tools/export_onnx.py` (checkpoint → ONNX, dev-only), `tools/bench_upscale.py`; requirements `docs/requirements/neural-upscaling.md` |
| Zoom, fit, double page, manga | `zoom.py`, `layout.py`, `box.py`, `transform.py`, `main.py`, `constants.py` (modes, axes), `tools.py` (rotation composition) |
| Navigation, scrolling | `main.py` (`flip_page`, `set_page`, `next_book`, shutdown), `file_provider.py` + `file_handler.py` (sibling navigation, path identity), `tools.py` (natural sorting), `scrolling.py`, `pageselect.py`, `slideshow.py`; requirements `docs/requirements/opening-books.md` |
| Keyboard / mouse | `event.py` (handlers; `manager.register` calls, whose `bindings` are the default keys), `keybindings.py` (action table: titles and groups), `keybindings_editor.py`, `cursor_handler.py`; requirements `docs/requirements/config-files.md` |
| Menus, toolbar, actions | `ui.py` (action entries + menu XML), handlers in `main.py` |
| Preferences | `preferences.py` (defaults, load/save), `atomic_file.py` (atomic config writes, shared with bookmarks, keybindings and the fileinfo pickle), `preferences_dialog.py`, `preferences_page.py`, `preferences_section.py`; requirements `docs/requirements/config-files.md` |
| Thumbnails | `thumbbar.py` (sidebar), `thumbnail_view.py`, `thumbnail_tools.py` (cover and freedesktop thumbnails, disk cache); library covers in `library/book_area.py`; requirements `docs/requirements/archive-reading.md` |
| Library | `library/` (`backend.py` sqlite, `backend_types.py` (book and collection types, search filter, watch scan), `main_dialog.py`, `book_area.py`, `collection_area.py`, `control_area.py`, `watchlist.py`, `pixbuf_cache.py` (cover LRU)), `file_chooser_library_dialog.py`; requirements `docs/requirements/config-files.md` (shared database) |
| Bookmarks, recent, last page | `bookmark_backend.py`, `bookmark_dialog.py`, `bookmark_menu*.py`, `recent.py` (via `Gtk.RecentManager`), `last_read_page.py`, fileinfo pickle in `file_handler.py`; requirements `docs/requirements/config-files.md`, `docs/requirements/opening-books.md` |
| Archive editing / packing | `edit_dialog.py`, `edit_image_area.py`, `edit_comment_area.py`, `archive_packer.py`; `file_handler.py` (hands over temporary files on close); requirements `docs/requirements/archive-reading.md` |
| Dialogs | `file_chooser_base_dialog.py` (preview worker), `file_chooser_*_dialog.py`, `properties_dialog.py` + `properties_page.py`, `comment_dialog.py`, `about_dialog.py`, `message_dialog.py`, `dialog_handler.py` |
| Status bar, OSD, title | `status.py`, `osd.py`, `main.py` (`update_title`, `_update_page_information`) |
| Open with... | `openwith.py`, `openwith_menu.py`; requirements `docs/requirements/archive-formats.md`, `docs/requirements/archive-reading.md` |
| Clipboard, extract/delete page | `clipboard.py`, `main.py` (`extract_page`, `delete`); requirements `docs/requirements/archive-reading.md` |
| Startup, CLI args | `mcomixstarter.py`, `__main__.py`, `run.py` (no GTK imports at top); `main.py` (which file and page open at start), `file_handler.py` (resume prompt); requirements `docs/requirements/opening-books.md` |
| Paths, config files | `constants.py`, `tools.py`, `portability.py`; requirements `docs/requirements/config-files.md` |
| i18n, strings | `i18n.py`, `strings.py`, `messages/` (`.po`/`.mo`) |
| Icons | `icons.py` (stock icons; title-bar button CSS with vector icons from `images/titlebar/`), `images/` |

### Recipes

- **New setting:** default in `preferences.py`; widget in
  `preferences_dialog.py`; read via `prefs['name']`.
- **New action with a shortcut:** entry in `ui.py` (and menu XML there),
  title and group in `keybindings.py` `BINDING_INFO`, `manager.register(...)`
  with the default keys as its `bindings` in `event.py`, handler on
  `MainWindow` in `main.py`. A shortcut runs only while its same-named
  action is sensitive (`ui.py` `set_sensitivities`).
- **New archive format:** subclass in `archive/` (from `archive_base.py`),
  register its MIME/type and availability check in `archive_tools.py` and
  `constants.py`; add fixtures in `test/files/archives/` and cases in
  `test/test_archives.py`.

### Tests (`test/`)

One file per area, named after it (`test_<area>.py`, e.g. `test_archives.py`,
`test_page_cache.py`, `test_upscale.py`); find a feature's tests with
`grep -l <module> test/`. Shared fakes live in `test/_*.py`; fixtures in
`test/files/`. The rules for adding, changing and removing tests are in
[docs/agents/tests.md](docs/agents/tests.md).

## Conventions

- GTK 3 APIs only.
- Match surrounding style (4 spaces, single quotes); type hints on new functions.
- Multiprocessing uses `spawn`: code run in child processes must be importable
  and picklable.
- Wrap user-visible strings in `_()`.
- Record user-visible changes in `ChangeLog.md`. It is history, not a
  requirement: behavior that must hold goes in `docs/requirements/`. Update
  the user guide in `docs/user/` when what users see or do changes.

## Rules

- **Stay in this repo.** Never change the owner's system settings, global
  tool config or antivirus. Recommend such changes instead.
- **Installing software:**
  - Never install system-wide or global tools yourself: no winget, choco,
    installers, PATH changes or conda environments other than `.venv`. Ask the
    owner, and give the exact command and what it changes, so they can run it
    themselves or approve it.
  - A new package in `.venv` needs the owner's approval before you install
    it. When asking, state the package, why it's needed, its size and
    maintenance status, and the alternatives, including "write it ourselves".
- **Processes:** only stop processes you started, by PID. Never stop
  processes by port or by name. An installed MComix may be running; don't kill it.
- **Comments:** only the "why" the code can't show: a constraint, a platform
  quirk or the design note it implements. Never restate what the code does.
- **Simplicity:** implement the direct approach that satisfies the request,
  with the smallest change. Follow the existing architecture and conventions;
  add frameworks, abstractions, options or caches only for a current need.
  Before handing off, read your diff and remove speculative branches,
  unneeded layers and duplicated state. Record unrelated improvements as Open
  items instead of doing them.
  - Validate at boundaries (files, archives, external tools, helper-process
    output) and trust internal callers: no checks for states the code can't
    reach.
  - Delete the code, tests, options and fixtures your change leaves unused,
    in the same diff.

## Sessions and Git

- Allow one writing session per checkout, across all tools. Never take over,
  reset or delete another session's worktree, branch or unfinished work.
- `main` is the trunk. Never commit directly to it (owner-approved doc edits
  on `main` are the exception). Work on a branch, and merge it into `main`
  only as [docs/agents/merging.md](docs/agents/merging.md) allows.
- Commit each checked unit of work without asking. Use a short imperative
  subject and one logical change per commit.
- Write a commit or merge message that has a body to a UTF-8 temporary file
  outside the repo and pass it with `-F <file>`: shell quoting mangles
  multi-line `-m` text.
- Take commit hashes from `git rev-parse` output; never type one from memory.
- Never push: the owner publishes to GitHub. Never rebase or amend a commit
  that another branch already contains.

## End of a chat

After a task, fix, closeout or plan draft, the last message holds only what
the owner must decide or act on:

- decisions the owner needs to make, each with your recommendation;
- actions the owner must take: a merge command, a manual check;
- anything that differs from what the owner agreed, or that wasn't verified
  or reviewed;
- the merge sha, or what kept the work unmerged; Open items added, one line
  each.

Leave out check results, review rounds and change summaries: they are in the commit and
merge bodies, and the owner asks when they want them.

**Stop and ask the owner only for:**

- a new dependency,
- a conflict with a plan's Decided item,
- a genuinely ambiguous task,
- a review finding still unresolved after two rounds,
- anything destructive or outside the repo.

Otherwise keep going without asking.
