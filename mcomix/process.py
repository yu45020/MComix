"""process.py - Process spawning module."""

import sys
import os
import subprocess
import errno
from typing import Callable, Iterable, Optional


NULL = open(os.devnull, 'r+b')
PIPE = subprocess.PIPE
STDOUT = subprocess.STDOUT

def _get_creationflags():
    if 'win32' == sys.platform:
        # Do not create a console window.
        return 0x08000000
    else:
        return 0

# Cannot spawn processes with PythonW/Win32 unless stdin
# and stderr are redirected to a pipe/devnull as well.
def popen(args, stdin=NULL, stdout=PIPE, stderr=NULL):
    return subprocess.Popen(args, stdin=stdin,
                            stdout=stdout, stderr=stderr,
                            creationflags=_get_creationflags())


def _is_absolute_search_dir(path: str) -> bool:
    """Require PATH directories to be independent of the working directory."""
    if 'win32' == sys.platform and not os.path.splitdrive(path)[0]:
        return False
    return os.path.isabs(path)


def _get_application_dir() -> Optional[str]:
    """Locate bundled helpers without trusting the invocation's argv[0]."""
    if getattr(sys, 'frozen', False):
        if not _is_absolute_search_dir(sys.executable):
            return None
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.realpath(__file__)))


if 'win32' == sys.platform:
    _exe_dir = _get_application_dir()

#: Used when PATHEXT is unset; the formats CreateProcessW can start.
_DEFAULT_PATHEXT = '.COM;.EXE;.BAT;.CMD'


def _windows_executable_name(name: str) -> str:
    """Keep a PATHEXT extension (e.g. .bat, .com); otherwise append .exe."""
    pathext = os.environ.get('PATHEXT') or _DEFAULT_PATHEXT
    extensions = {ext.lower() for ext in pathext.split(os.pathsep) if ext}
    extensions.add('.exe')
    if os.path.splitext(name)[1].lower() in extensions:
        return name
    return name + '.exe'


def find_executable(
        candidates: Iterable[str], workdir: Optional[str] = None,
        is_valid_candidate: Optional[Callable[[str], bool]] = None
) -> Optional[str]:
    """ Find executable in path.

    Return an absolute path to a valid executable or None.

    <workdir> defaults to the current working directory. It is used only
    to resolve candidates containing a directory component (e.g. ./tool).
    Bare names search only absolute PATH directories, never the working
    directory implicitly. Empty and relative PATH entries are ignored.

    <is_valid_candidate> is an optional function that must return True
    if the path passed in argument is a valid candidate (to check for
    version number, symlinks to an unsupported variant, etc...).

    If a candidate has a directory component,
    it will be checked relative to <workdir>.

    On Windows:

    - '.exe' will be appended to each candidate unless it already ends
      with an extension listed in PATHEXT (e.g. '.bat', '.cmd', '.com')

    - MComix executable directory is prepended to the path on Windows
      (to support embedded tools/executables in the distribution).

    On Unix:

    - a valid candidate must have execution right

    """
    if workdir is None:
        workdir = os.getcwd()
    workdir = os.path.abspath(workdir)

    search_path = [path for path in os.environ.get('PATH', '').split(os.pathsep)
                   if _is_absolute_search_dir(path)]
    if 'win32' == sys.platform and _exe_dir is not None:
        search_path.insert(0, _exe_dir)

    is_valid_exe = lambda exe: \
            os.path.isfile(exe) and \
            os.access(exe, os.R_OK|os.X_OK)

    if is_valid_candidate is None:
        is_valid = is_valid_exe
    else:
        is_valid = lambda exe: \
                is_valid_exe(exe) and \
                is_valid_candidate(exe)

    for name in candidates:
        if not name:
            continue

        # On Windows, must end with '.exe' or another PATHEXT extension.
        if 'win32' == sys.platform:
            name = _windows_executable_name(name)

        # Absolute path?
        if os.path.isabs(name):
            name = os.path.abspath(name)
            if is_valid(name):
                return name

        # Does candidate have a directory component?
        elif os.path.dirname(name):
            # Yes, check relative to working directory.
            path = os.path.abspath(os.path.join(workdir, name))
            if is_valid(path):
                return path

        # Look in search path.
        else:
            for dir in search_path:
                path = os.path.abspath(os.path.join(dir, name))
                if is_valid(path):
                    return path

    return None


def Win32Popen(cmd) -> int:
    """ Spawns a new process on Win32 and returns its PID. cmd is a list
    of parameters. """
    # Without an explicit executable, Windows searches the working
    # directory itself, undoing find_executable's lookup restrictions.
    exe = None
    if os.path.dirname(cmd[0]):
        # Explicit commands may name another Windows executable format
        # (e.g. .com). Preserve the supplied file before trying .exe lookup.
        explicit = os.path.abspath(cmd[0])
        if os.path.isfile(explicit) and os.access(explicit, os.R_OK | os.X_OK):
            exe = explicit
    if exe is None:
        exe = find_executable((cmd[0],))
    if exe is None:
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), cmd[0])

    return subprocess.Popen(cmd, executable=exe).pid


# vim: expandtab:sw=4:ts=4
