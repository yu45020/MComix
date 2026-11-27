# Reading archive pages on demand

Which formats open, and through which tool, is in [archive-formats.md](archive-formats.md).

Code: the "Open a file" flow and the "Archive formats" and "Page loading / caching" rows of the
AGENTS.md Roadmap. The features under Real files are in the rows that name this file.

## Which archives

- With the `lazy archive reading` preference on (the default), an archive whose backend supports
  random access is read on demand: ZIP, uncompressed TAR, and an archive read through 7z (such
  as RAR or 7z) that is not solid. An archive that contains other archives qualifies only when all
  of them do.
- Every other archive (solid RAR and 7z, compressed TAR, PDF, MOBI), and every archive when the
  preference is off, is extracted to a temporary folder as before.
- The preference takes effect for the next archive opened.

## Opening and reading

- Opening an on-demand archive reads no page and writes none to disk. A page's bytes are read
  from the archive only when something asks for that page.
- All pages and comments are available as soon as the archive is listed; no page waits for
  another to be extracted.
- A password is asked for while the archive is listed, before any page is read, so a page read
  never blocks the window on a password dialog.
- Sidebar thumbnails of archive pages are made in memory; no thumbnail file is written for them.
  The cover thumbnail of an on-demand archive is read in memory too.
- Closing the archive waits for a read in progress. A read after closing fails with an error,
  and a page of a closed archive is never shown in the next book. The temporary folder is
  removed on close.

## Real files

- A page is written to the temporary folder only when a feature needs a file: Open with using
  the page's file or folder, dragging a thumbnail out, copying to the clipboard, and repacking in
  the edit dialog. Only the requested pages are written.
- Save page writes the page's bytes straight to the chosen file, and a failed read never empties
  an existing file.
- Once a page's file is written, later reads use it, so a change made by an external program
  shows when viewing, saving and repacking. A deleted file is written again on the next request.
- A failed write leaves no partial file, and repacking stops if any requested file is missing.
  A repacking save that fails leaves the existing archive unchanged.

## External tools

- When a single page is read or written through 7z, an exit
  status of 2 or more, or a missing tool, is an error: the output is discarded and never shown
  as a page. A status of 1 is a warning and the data is kept.
- When a solid archive is extracted through 7z and the tool's output ends before a
  page is complete, that page is never written or shown, and the pages after it fail. The same
  holds for a complete page whose data doesn't match the CRC32 in the listing (7z and RAR
  members that aren't encrypted). A full-length page with no CRC32 to compare (an encrypted
  member, or a type other than 7z and RAR read through 7z) is shown as it is decoded, whatever
  the tool's exit status: holding it until the tool exits would show no page of an encrypted
  solid book until the whole archive is decompressed.
