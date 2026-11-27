# Changes since MComix 3.2.0

This fork started from MComix 3.2.0 (2026-08-20). Upstream's history before
that is in [upstream's ChangeLog](https://sourceforge.net/p/mcomix/git/ci/master/tree/ChangeLog.md)
and in this repository's git history (`git show 3.2.0:ChangeLog.md`).

### Features
- Larger minimize, maximize and close buttons, with sharp vector icons, in
  the title bar that GTK draws for every window.
- New command line options `--entry NAME` (`-e`) and `--page N` (`-p`) open
  the book at the page with that name inside the archive (or that file in the
  folder), or else at page N, without asking to resume. External programs
  such as a manga manager can open a book at a chosen page.
- Open SVG images (`.svg`, `.svgz`) as pages; the Windows build bundles
  librsvg, which also removes GTK's missing-icon warnings in Preferences.
- RAR/CBR and LHA archives, and ZIP archives that Python can't read (such as
  encrypted ones), now open only through 7-Zip. The UnRAR library and the
  `unrar`/`rar`, `unzip` and `lha` executables are no longer used, and the
  Windows build no longer bundles UnRAR64.dll.
- Add View menu items "Shift pairing forward" and "Shift pairing back"
  (Ctrl+Page Down / Ctrl+Page Up) that move one page in double page mode, so
  books with a missing page can be re-paired without the thumbnail sidebar.
- Keep enlarged animated WebP pages playing with fewer reserved output buffers.
  Add an animation cache limit, an option to prevent WebP enlargement, and a
  nonmodal notice when the memory limit prevents playback.
- Play animated WebP pages from files and archives, respecting frame timing,
  loop counts and the animation preference, with zoom, rotation, flips and
  continuous playback while resizing or switching fullscreen.
- Add trained SPAN-F-YC checkpoint export (a luma head with kernel-resampled
  chroma) to `tools/export_onnx.py`, with `--spanf-activation` for the Tanh
  variant (default) or SiLU.
- Added `build_portable.bat` to build one portable Windows folder with the
  SPAN-F-YC model. Custom exported ONNX models can be selected in
  Preferences. ZIP creation is optional (`--zip`).
- The "Hyperbolic" scaling mode (deprecated by GdkPixbuf: slower and lower
  quality than bilinear) is replaced by "Lanczos (sharp)", which resizes pages
  with Pillow; existing Hyperbolic settings switch to it. The magnifier and
  thumbnails use bilinear in that mode.
- Added support for AVIF images, through Pillow's AVIF module. The dev
  environment installs Pillow from PyPI, whose wheels include it; the
  conda-forge build doesn't.
- Pages of ZIP/CBZ, uncompressed TAR and non-solid RAR/CBR/7z archives are now
  read on demand instead of extracting the whole archive to a temporary
  folder. Can be disabled in Preferences > Advanced. In that mode, the folder
  given to external commands by `%D` only contains the pages opened with them.
- Optional neural upscaling of small pages (Preferences > Display > Image
  quality). Uses the bundled SPAN-F-YC model `SPANF_YC_2x` by default (other
  models can be converted with `tools/export_onnx.py`) and needs the optional
  `onnxruntime-windowsml` and `windowsml` packages with a DirectX 12 GPU; it
  never runs on the CPU, and a GPU failure turns it off. Large pages are
  processed in tiles to limit GPU memory use. With the "Automatic" GPU
  runtime, a faster runtime installed by Windows ML (NVIDIA TensorRT-RTX on
  Windows 11 24H2+) is used when available, otherwise DirectML. Models
  exported in fp16 and fp32 use the variant recorded as best for the GPU
  runtime in use. Grayscale pages stay grayscale (models trained on colour
  photos would tint them).
- PDF reading through the `mutool` executable needs MuPDF 1.8 or later
  (`mutool draw`). MuPDF 1.6 and 1.7 and the separate `mudraw` executable are
  no longer used; without PyMuPDF, PDF files then don't open.
- Neural upscaling runs on DirectML or a Windows ML runtime only; ONNX
  Runtime's CUDA provider (used outside Windows) is no longer used. Any
  remembered Windows ML engine failure keeps that runtime off for its model,
  including the inference-failure records of earlier development builds.
- Data from old versions is no longer imported: `preferences.pickle`
  (before 2012) and `lastreadpage.db` are ignored; a bookmarks file holding
  bookmarks saved before 2011 loads only up to the first of them, and the next
  save drops the rest. The library
  database is never upgraded. A library from another version
  (MComix 1.2.1 stores version 6) is used unchanged with a warning; one older
  than version 6 is unsupported. A `keybindings-Gtk.rc` file from MComix 1.0
  is left in place and no longer renamed. An "Open with" command saved before
  2013 (label and command only) stops MComix from starting until it is removed
  from the preferences file, a "Store recently opened files" value of 2 saved
  in 2012 shows no choice in Preferences until one is picked, and the
  thumbnail bar no longer takes the main background colour from the hidden
  `thumbnail bg uses main colour` setting.
- Startup no longer checks the Pillow version; MComix still reports a missing
  Pillow. The hidden `-o` command-line option is gone, so `-o` is an unknown
  option.
- The documentation moved from upstream's SourceForge wiki into `docs/user/`,
  and the About dialog and the external command editor link to this fork's
  GitHub repository.
- Upstream's MSI installer and Chocolatey package files, which pointed to
  SourceForge, are removed, as is the `build_installer.bat` alias; Windows
  builds are made with `build_portable.bat` only.

### Bug fixes
- Shrink the Windows portable build by over 500 MB: numpy now uses OpenBLAS
  instead of Intel MKL, whose DLLs MComix never needed.
- Show ZIP member names that lack the UTF-8 flag correctly: they are read as
  UTF-8, then as Japanese Shift JIS (cp932), and only then as cp437.
- Stop listing folders inside an archive as pages or members; the pages in
  them no longer get a " (1)" folder suffix.
- Open a book that holds a damaged or unsupported nested archive with its
  other pages.
- Stop opening a plain `.xz` or `.lzma` file as an archive; `.tar.xz` and
  `.tar.lzma` are archives without 7-Zip.
- End a hung MuPDF child when its PDF is stopped, instead of blocking.
- Report a member's size as unknown until it is extracted when lazy reading is
  off (archive editor).
- Keep bookmarks removed or reordered in one MComix window when another window
  changes bookmarks or quits. Bookmarks are saved with each change, not at quit.
- Never overwrite a bookmarks file that can't be read; keep one that can't be
  parsed as `bookmarks.pickle.broken`. Show an error when a bookmark change
  can't be saved.
- Keep a `preferences.conf` that can't be read and don't overwrite it at quit;
  keep one that holds something other than a JSON object as
  `preferences.conf.broken` and start with the defaults.
- Load out-of-range smart scroll and mouse wheel values in `preferences.conf`
  as the defaults.
- Exit even when a config file can't be written at quit.
- Stop moving an old `~\MComix` folder to `%APPDATA%\MComix`. On Windows, the
  file choosers start in the home folder instead of a missing `~\MComix`.
- Reopen a folder book with subfolders as the whole folder at the page shown,
  after Save and quit, from the last-file auto-load and from a bookmark.
  Clicking a bookmark of the open folder book only turns the page, and a second
  bookmark in the same folder asks to replace the first, as in an archive.
- Leave the window empty when a file can't be opened, so it can't be bookmarked
  or saved at quit.
- Write the saved file position atomically.
- Stop adding an archive without images to the library when it is closed.
- Stop asking to resume a book finished in double page mode, or one with
  `__MACOSX` images, and show the library's finished badge for it.
- Keep each subfolder's pages together in the GLib "Sort archive by" order.
- Open at the image when its path is typed in another case, and stop adding
  `book.cbz` as a page for `mcomix Folder book.cbz`.
- Accept comment extensions typed as `txt`, `.txt` or `*.txt`; an empty field
  marks no file as a comment, and no value stops MComix from starting.
- Draw a PNG with malformed embedded Exif text instead of leaving the previous
  page shown.
- Fix the magnifier failing on animated GIF pages with the checkered background
  off, showing the poster of a playing animated WebP, and freezing the window
  while a page is still being extracted.
- Open Properties for a loose image over Pillow's decompression bomb limit.
- Use a Windows ML provider installed from Preferences on the next upscaled
  page, without a restart.
- Upscale pages whose short side is below a custom model's pad multiple.
- Name the bundled default model SPAN-F-YC, 2x in Preferences.
- Library Copy puts the book's path on the clipboard instead of its cover.
  Edit > Copy offers the page image and its file path together, so an image
  editor pastes the page and a text editor its path; animated GIF pages copy as
  their static image instead of failing.
- Ignore the Delete, Go to page and Save and quit shortcuts while no book is
  open, and the shortcut of any other action that is unavailable. Close and
  Next/Previous archive and directory stay available while a book is loading.
- Stop Ctrl+Shift+W, Ctrl+Shift+O and Ctrl+Shift+L running the Ctrl shortcut;
  Shift+= zooms in on US layouts.
- Stay in the book when deleting the second-last page in double page mode.
- Start at the top of a page turned while a redraw is queued.
- Print the GTK version message instead of a traceback when GTK typelibs are
  missing, and stop logging a keybindings error on the first run.
- Make the keyboard editor's Name column read-only, show icons in the popup's
  Bookmarks and View submenus, and make the "Go to page..." title translatable.
- Give a sidebar thumbnail dragged out of the window a valid file URI on
  Windows.
- Stop a watch scan reporting a read book again on every scan, and stop adding
  a book twice when it is found under another case on Windows.
- Stop a removed book's reading position passing to the next book added.
- Skip a damaged book and go on with the rest of an add or a watch scan.
- Show each comment of a ZIP once in the Comments dialog.
- Refresh an open library window when the reading history is cleared.
- Restore the filter the library's Add books dialog saved (default: All
  archives). A saved filter of 1 left by earlier versions now selects the first
  format; choosing All archives once resets it.
- Still ask to save after editing an Open with command and then leaving
  another cell unchanged.
- List a book in a collection and its subcollection once, and report a failed
  collection duplicate as failed.
- Show the tooltip of an Edit archive image named with `&` or `<`.
- Keep the scroll position when a page finishes loading after it was drawn,
  instead of jumping to the destination of an earlier page change.
- Decode UTF-8 archive comment files correctly on CJK Windows locales when
  chardet is not installed.
- Stop showing truncated or damaged pages when 7z fails while
  extracting a solid archive.
- Preserve animated WebP frame order and encoded hold times when playback falls
  behind, slowing down instead of skipping frames or rushing to catch up.
- Overlap animated WebP decoding and rendering with separate workers while
  keeping in-flight frames within the shared animation cache budget.
- Skip unnecessary alpha compositing for fully opaque animated WebP frames,
  resizing owned RGB snapshots before exporting pixels when memory permits.
- Keep expensive animated WebP pages progressing when copying or rendering
  misses frame deadlines, including periodic progress when decoding falls behind.
- Prepare one upcoming animated WebP frame for timely presentation, and limit
  display size to a configurable 2048-pixel longest edge by default (0 disables).
- Buffer about 300 ms of upcoming animated WebP frames when memory permits,
  sharing the cache ceiling across visible animations.
- Keep native-size animated WebP playback running when selecting Lanczos,
  including rotated pages.
- Exit startup errors cleanly in windowed or redirected launches without waiting
  for console input.
- Display images from nested folders in depth-first order when opening a directory.
- Honor configured Fit to Size bounds even when the window has more space.
- Respect manga reading order when the viewport lies equally close to
  two pages of different sizes.
- Report invalid numeric scroll destinations with their value and axis.
- Use supported Python threading APIs for workers, archive packing and
  temporary-directory cleanup.
- Use explicit encodings and Windows process flags for external archive
  listings; report invalid output and failed listings without changing member names.
- Keep inserted filenames literal in Open With commands while preserving
  template environment expansion, quoting and working-directory behavior.
- Translate the Save As directory preference tooltip.
- Translate the password prompt continuation sentence.
- Render filenames containing ampersands or markup-like text literally in
  confirmation and error dialogs, while retaining formatted headings.
- Use the same case-insensitive Unicode name and path matching for library
  searches and newly added books, retaining percent/underscore wildcards.
- Reset the thumbnail sidebar remembered row when its contents are cleared.
- Show complete archive-edit drag icons with the native IconView API,
  including displays using higher scaling.
- Keep shortcut replacements, active keys and menu labels consistent,
  preserving explicitly cleared shortcuts after restarting.
- Normalize combined EXIF, automatic and manual rotations so full turns
  render correctly instead of failing the image transform.
- Navigate archive siblings safely across Windows path aliases and missing
  folders, and sort Unicode numeric filenames without conversion errors.
- Bound the library cover cache by owned pixel bytes and count, reuse recent
  covers, and release cached pixels when the library closes.
- Reuse valid disk thumbnails of small covers and close validation handles;
  regenerate invalid or undecodable cached images.
  Thumbnails record their requested size, so older cached thumbnails are
  regenerated once.
- Keep recent-file menus and clearing scoped to MComix. Clearing removes only
  entries no other application has registered; shared entries are kept.
- Save bookmark reordering once, update each menu once, and skip unchanged
  editor closes.
- Save preferences, bookmarks and shortcuts through complete temporary files
  before replacement, preserving the previous configuration when a save fails.
  The file is written where its path points: a symbolic link is replaced by
  a regular file, and on Linux and other POSIX systems the saved file is
  readable only by its owner.
- Synchronize Windows ML provider refresh, and release failed or closed
  sessions before unregistering their provider libraries.
- Resize neural results off the GTK thread and retain eligible results when
  upscaling policy changes, with bounded delivery and complete worker shutdown.
- Reuse unchanged page scaling and smart-background calculations in bounded
  caches, preserving enhancement, animation and native-size rendering behavior.
- Bound file chooser previews to one worker and the latest selection, clear
  failed previews and cancel obsolete archive/password work without waiting in GTK.
- Start PDF workers lazily, reuse one child document per archive, and release
  its process and file handles when closing, with incremental page listing.
- Share in-flight page decodes, discard obsolete results and keep redraws
  responsive while pages load, preserving pending navigation destinations.
- Restore library Copy and external file drops with Python-3 strings,
  decode file URIs once, and retain the selected collection destination.
- Open BZIP2 and LZMA ZIP archives through Python when their codecs are
  available, retaining external-tool fallback for unsupported compression.
- Prioritize displayed pages with small cache limits, apply cache preferences
  immediately, and preserve page geometry without decoding during scheduling.
- Honor watched-folder collection destinations independently of the sidebar,
  normalize All books imports, and prevent invalid collection memberships.
- Extract PDF pages with large page/object numbers correctly, preserve rotated
  image quality and mixed formats, and report external-renderer failures safely.
- Keep book switching and closing responsive during nested extraction, retain
  live archive storage until workers finish, and release readers before disk operations.
- Honor remembered No when resuming books, and restore saved startup pages
  even when reading history is disabled; explicit paths take precedence.
- Discard thumbnail results for removed or replaced rows, and prepare library
  status icons on the GTK thread before composing them in workers.
- Safely replace edited archives after releasing their open handles, preserve
  retry inputs on failure, and remove library entries only after file deletion.
- Stop page-selector previews and cancel library watch scans before shutdown
  closes their resources; scan once per library opening and discard stale results.
- Serialize nested file opens and closes, preserve temporary archive ownership
  on cleanup errors, and discard thumbnails queued for a previous book.
- Release destroyed dialog callback subscriptions and stop their thumbnail
  workers, avoiding accumulated page-change and histogram work.
- Finish failed archive extraction and listing operations so loading and file
  actions do not wait indefinitely, and report failures instead of using partial files.
- Keep edited page lists and cached pixels consistent after importing, reordering
  or removing pages, including decodes still running when Apply is pressed.
- Stop searching the working directory implicitly for helper executables and
  fail explicitly when a Windows external command cannot be resolved. Bare
  Open With commands with a PATHEXT extension (e.g. `.bat`, `.cmd`, `.com`)
  are still found on PATH.
- Keep archive extraction inside its destination and preserve distinct members
  whose names need filesystem cleanup, including long names. Keep nested archive
  storage separate from page paths.
- Fix animated pages with smart backgrounds and allow subsequent redraws after
  rendering errors.
- Allow GPU upscaling to retry after interrupted engine builds or transient
  inference failures.
- Fixed error/crash when trying to open PDF file on Windows build.
