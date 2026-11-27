# Configuration files

How MComix saves and loads its preferences, bookmarks, shortcuts and saved position, and the files
it shares with another installation.

Code: the "Preferences", "Bookmarks, recent, last page", "Keyboard / mouse", "Library" and "Paths,
config files" rows of the AGENTS.md Roadmap.

## Saving

- Preferences, bookmarks, shortcuts and the saved file position are written through a complete
  temporary file that then replaces the old one; a failed save keeps the previous file.
- Bookmarks are saved with each change. Another MComix window changing bookmarks or quitting never
  undoes this window's removals or reorders.
- MComix exits even when a config file can't be written at quit.
- A shortcut the user cleared stays cleared after a restart.

## Damaged files

- A `preferences.conf` that can't be read is never overwritten. One that holds something other
  than a JSON object is kept as `preferences.conf.broken`, and MComix starts with the defaults.
- A bookmarks file that can't be read is never overwritten. One that can't be parsed is kept as
  `bookmarks.pickle.broken`. An error is shown when a bookmark change can't be saved.

## Shared files

- The library database's structure is never upgraded: one at another version (the installed
  MComix 1.2.1 shares it at version 6) is used as it is, with a warning.
