# Archive formats and external tools

Which formats open, through which tool, and how member names are handled. Reading pages on demand
is in [archive-reading.md](archive-reading.md).

Code: the "Archive formats" and "Open with..." rows of the AGENTS.md Roadmap.

## Formats

- RAR/CBR, LHA, and ZIPs with a compression method this Python build can't read (such as
  AES-encrypted ones, Deflate64, or BZIP2 or LZMA without the codec) open only through the `7z`
  executable. UnRAR and the `unrar`/`rar`, `unzip` and `lha` executables are never used. Every
  other ZIP, including traditionally encrypted ones, opens through Python.
- PDF opens through PyMuPDF or `mutool` 1.8 or later (`mutool draw`). `mudraw` is never used;
  without either, PDF files don't open.
- A plain `.xz` or `.lzma` file doesn't open as a book; `.tar.xz` and `.tar.lzma` open without
  7-Zip.

## Members

- A ZIP member name without the UTF-8 flag is decoded as UTF-8, then as cp932, and only then as
  cp437.
- Folders inside an archive are never pages or members.
- A damaged or unsupported nested archive doesn't stop the book from opening; its other pages
  show.
- Extraction never writes outside its destination, and members whose names need cleanup for the
  filesystem stay distinct files.

## External programs

- A helper named without a folder (such as `7z` or `mutool`) is looked up only in absolute `PATH`
  folders (on Windows, MComix's own folder first), never in the working directory.
- On Windows an Open with command is started only from a resolved executable; a bare name with a
  `PATHEXT` extension (such as `.bat` or `.cmd`) is found on `PATH`. Filenames inserted into the
  command stay literal.
