# External commands

MComix can run your own commands on the open page, book or folder: another
image editor, a file manager at the book's location, or a script. They are
listed under **File > Open with**, and the keys **1** to **9** run the first
nine. Add and edit them with **File > Open with > Edit commands**.

## A command's fields

- **Label:** the name shown in the menu.
- **Command:** the program to run, followed by its arguments, separated by
  spaces. Put an argument that contains spaces in double quotes. The
  [variables](#variables) below are replaced when the command runs.
- **Working directory:** the folder the command runs in. It may use the same
  variables.
- **Disabled in archives:** don't run the command while an archive is open, so
  it can't change temporary files that are deleted when the archive closes.

The dialog shows the command line that would run for the current page, and
can run it as a test.

## Variables

### Page

| Variable | Meaning | Example |
| --- | --- | --- |
| `%F` | full path of the current page's file | `C:\Comics\cats.jpg` |
| `%f` | file name of the current page | `cats.jpg` |
| `%D` | full path of the folder holding the current page | `C:\Comics` |
| `%d` | name of the folder holding the current page | `Comics` |

In an archive, these name the page's temporary copy. With **Read archive pages
on demand** on (Preferences > Advanced), that temporary folder holds only the
pages you have run a command on, not the whole book.

### Archive

Only valid while an archive is open.

| Variable | Meaning | Example |
| --- | --- | --- |
| `%A` | full path of the archive | `C:\Comics\comic-2012.cbz` |
| `%a` | file name of the archive | `comic-2012.cbz` |
| `%C` | full path of the folder holding the archive | `C:\Comics` |
| `%c` | name of the folder holding the archive | `Comics` |

### Book and shelf

These follow what is open: the archive, or the folder of loose images. Think of
B as "book" and S as "shelf".

| Variable | In a folder of images | In an archive |
| --- | --- | --- |
| `%B` | the page's folder (`C:\Comics\Issue 1`) | the archive (`C:\Comics\comic-2012.cbz`) |
| `%b` | the page's folder name (`Issue 1`) | the archive's file name (`comic-2012.cbz`) |
| `%S` | the folder holding that folder (`C:\Comics`) | the folder holding the archive (`C:\Comics`) |
| `%s` | its name (`Comics`) | its name (`Comics`) |

### Special characters

| Variable | Inserts |
| --- | --- |
| `%/` | the path separator: `\` on Windows, `/` elsewhere |
| `%"` | a literal double quote |
| `%%` | a literal percent sign |
