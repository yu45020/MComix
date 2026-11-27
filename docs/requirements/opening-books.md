# Opening books

Which book and page open, from the command line, at startup and when resuming, and what the recent
menu lists.

Code: the "Open a file" flow and the "Startup, CLI args", "Navigation, scrolling" and "Bookmarks,
recent, last page" rows of the AGENTS.md Roadmap.

## Command line

- `--entry NAME` (`-e`) opens the book at the member (archive) or file (folder) whose stored name
  is exactly `NAME`, case-sensitive, with either slash; a member of a nested archive can't be
  named. When no entry matches, a notice shows and the book opens at `--page N` (`-p`), else at
  page 1. With either option the resume prompt is never shown. External programs, such as a manga
  manager, rely on this.
- A path on the command line beats the saved startup position; without `--page` or `--entry` it
  keeps the normal resume prompt.

## Startup and resuming

- With no path given, the saved startup page (Save and quit, or the last-file auto-load) is
  restored even with reading history off.
- A remembered No to resuming is honored.
- A finished book doesn't ask to resume, including one finished in double page mode.
- A folder book with subfolders reopens as the whole folder at the page shown, from Save and quit,
  the last-file auto-load and a bookmark.
- A file that can't be opened leaves the window empty, so it can't be bookmarked or saved at quit.

## Folders

- Opening a folder shows its nested folders depth-first; each subfolder's pages stay together in
  every sort order.

## Recent files

- The recent menu lists only files MComix registered. Clearing it removes those only MComix
  registered; a file another application also registered is kept and stays listed.
- Nothing is added while `store recent file info` is off.
