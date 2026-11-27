# Requirements

What the fork's features must do, one file per feature or area. Each line is a behavior the owner
relies on and a test or a manual check can observe. How the code achieves it is not recorded
here: that is in the code and the commit history.

| Feature or area | File |
| --- | --- |
| Archive formats and external tools | [archive-formats.md](archive-formats.md) |
| Reading archive pages on demand | [archive-reading.md](archive-reading.md) |
| Animated WebP playback | [animated-webp.md](animated-webp.md) |
| Neural upscaling | [neural-upscaling.md](neural-upscaling.md) |
| Configuration files | [config-files.md](config-files.md) |
| Opening books | [opening-books.md](opening-books.md) |

- A change that alters a listed behavior updates the file in the same diff; the owner decides
  such a change.
- Add a file only for a feature or area whose intended behavior the code alone doesn't make
  clear, and a line only for behavior that must hold. Defaults and limits are named by their
  preference, not copied, unless the number itself is the requirement.
