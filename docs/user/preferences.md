# Preferences

Open the dialog with **F12** or **Edit > Preferences**. Most changes take
effect at once; changing the language needs a restart. Hover over an option to
see its tooltip. **Clear dialog choices** brings back every question you told
MComix not to ask again.

Settings are saved in `preferences.conf` in `%APPDATA%\MComix` on Windows, or
in `~/.config/mcomix` on Linux.

## Appearance

- **User interface:** the interface language, and whether **Escape** quits
  MComix or only leaves fullscreen.
- **Background:** a fixed colour, or a dynamic colour taken from the edges of
  the page shown, so a page with a white border gets a white background.
- **Thumbnails:** the same choice for the thumbnail sidebar, page numbers on
  thumbnails, thumbnail size, and using the book's first page as the window
  icon.
- **Transparency:** a grey checkered background behind transparent images,
  instead of plain white.

## Behaviour

- **Scroll:**
  - **Use smart scrolling** makes the mouse wheel scroll like Space: in
    reading order, sideways across the page first, then down.
  - **Flip pages when scrolling off the edges** turns the page after the
    number of extra scroll "steps" set below, so you don't flip by accident.
  - Open the next or previous archive or directory automatically at the end
    or the start of a book, and whether going back opens the previous book at
    its first or last page.
  - How far the arrow keys, the mouse wheel and Space scroll.
- **Double page mode:**
  - flip two pages at a time;
  - show one page alone for the title page, for wide images, for both, or
    never;
  - how two pages of different sizes are resized;
  - the gap between the pages.
- **Files:** reopen the last book at startup, keep a list of recent files
  (File > Recent, and the library's Recent collection), and start Save As in
  the last folder saved into.

## Display

- **Fullscreen:** start in fullscreen, and hide the toolbars there.
- **Fit to size mode:** the fixed width and height used by **Fit to size**,
  set separately for wide pages and other pages.
- **Slideshow:** the delay and step. A step of 0 flips a page each time; a
  negative step scrolls backwards. For smooth scrolling, try a delay of 0.05 s
  and a step of 1 px.
- **Rotation:** rotate pages according to their Exif orientation.
- **Image quality:**
  - **Scaling mode:** Normal (fast), Bilinear, or Lanczos (sharp). Lanczos is
    the sharpest and handles fine patterns best when shrinking, at a few more
    milliseconds per page.
  - **Neural upscaling for small pages:** see
    [Neural upscaling](README.md#neural-upscaling). The options under it pick
    the model (**Default** returns to the bundled one), the size below which
    pages are upscaled, whether to upscale pages that aren't enlarged, and the
    GPU runtime. The status line shows the runtime in use, and **Install**
    appears when Windows can install a faster runtime for your GPU.

## Advanced

- **File order:** how files in folders and pages in archives are sorted.
- **Extraction and cache:**
  - **Read archive pages on demand** reads pages straight from ZIP/CBZ,
    uncompressed TAR and non-solid RAR/CBR/7z archives instead of extracting
    the whole book first. It applies to the next book opened.
  - the number of extraction threads;
  - storing thumbnails on disk (the freedesktop.org cache that file managers
    share);
  - how many pages to keep decoded in memory (-1 keeps the whole book).
- **Magnifying lens:** its size and magnification.
- **Comments:** the file extensions treated as archive comments (for example
  `txt` and `nfo`).
- **Animated images:**
  - play animations or show their first frame;
  - whether animated WebP pages may be enlarged beyond their own size;
  - a maximum display size for animated WebP (0: no limit);
  - the memory shared by all visible animations.

## Shortcuts

Every action's shortcuts. Select an action to add, change or clear its keys;
the defaults are listed in [Keyboard and mouse](keyboard-and-mouse.md).
