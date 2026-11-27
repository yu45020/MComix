# -*- coding: utf-8 -*-

import atexit
import os
import shutil
import tempfile

# Before the first mcomix import: constants binds the config, data and thumbnail paths under
# %APPDATA% when imported, and tests never touch the owner's %APPDATA%\MComix. Child processes
# inherit the folder: spawned ones skip atexit, so a folder of their own would be left behind.
if 'MCOMIX_TEST_APPDATA' not in os.environ:
    _appdata = tempfile.mkdtemp(prefix='mcomix-test-appdata.')
    os.environ['APPDATA'] = os.environ['MCOMIX_TEST_APPDATA'] = _appdata
    atexit.register(shutil.rmtree, _appdata, ignore_errors=True)

# Configure locale.

import locale

locale.setlocale(locale.LC_ALL, '')

# Since some of MComix' modules depend on gettext being installed for _(),
# add such a function here that simply returns the string passed into it.

import builtins

if '_' not in builtins.__dict__:
    builtins.__dict__['_'] = str

# Enable debug logging to make post-mortem analysis easier.

from mcomix import log

log.setLevel('DEBUG')

# Use a custom testcase class:
# - isolate tests: do not use or modify the user current
#   configuration for MComix (preferences, library, ...)
# - make sure MComix state is reset before each test

import unittest

from mcomix.preferences import prefs

default_prefs = {}
default_prefs.update(prefs)

class MComixTest(unittest.TestCase):

    _storage_environment = ('HOME', 'XDG_DATA_HOME', 'XDG_CONFIG_HOME',
                            'TMPDIR', 'TEMP', 'TMP')

    def setUp(self):
        self._original_cwd = os.getcwd()
        self._original_environment = {
            name: os.environ.get(name) for name in self._storage_environment}
        self._original_tempdir = tempfile.tempdir
        self._original_prefs = dict(prefs)
        self.tmp_dir = None
        # unittest runs registered cleanups even when a subclass setup fails.
        self.addCleanup(self._restore_test_storage)
        name = '.'.join((
            self.__module__.split('.')[-1],
            self.__class__.__name__,
            self._testMethodName))
        self.tmp_dir = tempfile.mkdtemp(prefix='mcomix.%s.' % name)
        home_dir = os.path.join(self.tmp_dir, 'home')
        os.mkdir(home_dir)
        os.environ['HOME'] = home_dir
        os.environ['XDG_DATA_HOME'] = os.path.join(home_dir, 'data')
        os.environ['XDG_CONFIG_HOME'] = os.path.join(home_dir, 'config')
        temp_dir = os.path.join(self.tmp_dir, 'tmp')
        os.mkdir(temp_dir)
        os.environ['TMPDIR'] = os.environ['TEMP'] = os.environ['TMP'] = temp_dir
        tempfile.tempdir = temp_dir
        prefs.clear()
        prefs.update(default_prefs)

    def _restore_test_storage(self) -> None:
        # Restore cwd and process state before removing their temporary paths.
        try:
            os.chdir(self._original_cwd)
        finally:
            for name, value in self._original_environment.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            tempfile.tempdir = self._original_tempdir
            prefs.clear()
            prefs.update(self._original_prefs)
            if self.tmp_dir is not None:
                shutil.rmtree(self.tmp_dir)

# Helper to get path to testsuite sample files.

def get_testfile_path(*components):
    return str(os.path.join(os.path.dirname(__file__), 'files', *components))

