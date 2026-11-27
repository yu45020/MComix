# coding: utf-8

import hashlib
import os
import random
import tempfile
import threading
import time
import unittest
from unittest import mock

from gi.repository import GLib

from . import MComixTest, get_testfile_path

from mcomix import archive_extractor
from mcomix import archive_tools
from mcomix.archive import archive_base
from mcomix.archive import password as archive_password
from mcomix.archive import sevenzip_external
from mcomix.preferences import prefs


# Contents of the "Flat" test archives.
_FLAT_CONTENTS = {
    'arg.jpeg': 'images/01-JPG-Indexed.jpg',
    'foo.JPG' : 'images/04-PNG-Indexed.png',
    'bar.jpg' : 'images/02-JPG-RGB.jpg',
    'meh.png' : 'images/03-PNG-RGB.png',
}


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()

def original_md5(name: str) -> str:
    with open(get_testfile_path(_FLAT_CONTENTS[name]), 'rb') as fp:
        return md5(fp.read())


class ExtractorTest(MComixTest):

    def setUp(self):
        super(ExtractorTest, self).setUp()
        self.dest_dir = tempfile.mkdtemp(prefix='extract.')
        self.extractor = archive_extractor.Extractor()

    def tearDown(self):
        self.extractor.close()
        super(ExtractorTest, self).tearDown()

    def _open(self, archive):
        """ Set up the extractor for test archive <archive>, and
        wait for its contents to be listed. Returns the file list. """
        self.extractor.setup(get_testfile_path('archives', archive), self.dest_dir)
        # No main loop: poll instead of relying on the contents_listed callback.
        deadline = time.monotonic() + 10
        while self.extractor.get_files() is None:
            self.assertLess(time.monotonic(), deadline, 'listing timed out')
            time.sleep(0.01)
        files = self.extractor.get_files()
        self.assertCountEqual(files, list(_FLAT_CONTENTS.keys()))
        self.extractor.set_files(files)
        return files

    def _wait_extracted(self, files):
        deadline = time.monotonic() + 10
        while not all(self.extractor.is_ready(name) for name in files):
            self.assertLess(time.monotonic(), deadline, 'extraction timed out')
            time.sleep(0.01)

    def _check_lazy(self, archive):
        files = self._open(archive)
        self.assertTrue(self.extractor.is_lazy())
        # Everything is ready, without being extracted.
        for name in files:
            self.assertTrue(self.extractor.is_ready(name))
        self.extractor.extract()
        for name in reversed(files):
            self.assertEqual((name, md5(self.extractor.read(name))),
                             (name, original_md5(name)))
            size = self.extractor.get_entry_size(name)
            self.assertEqual((name, size),
                             (name, os.path.getsize(get_testfile_path(_FLAT_CONTENTS[name]))))
        self.assertEqual(os.listdir(self.dest_dir), [])
        return files

    def test_lazy_zip(self):
        prefs['lazy archive reading'] = True
        self._check_lazy('Flat.zip')

    def test_lazy_materialize(self):
        prefs['lazy archive reading'] = True
        files = self._open('Flat.zip')
        name = files[0]
        for _ in range(2):
            path = self.extractor.materialize(name)
            self.assertEqual(path, os.path.join(self.dest_dir, name))
            with open(path, 'rb') as fp:
                self.assertEqual(md5(fp.read()), original_md5(name))
        # Only the requested file was written.
        self.assertEqual(os.listdir(self.dest_dir), [name])

    def test_lazy_concurrent_reads(self):
        prefs['lazy archive reading'] = True
        files = self._open('Flat.zip')
        errors = []

        def reader(seed):
            rng = random.Random(seed)
            try:
                for _ in range(50):
                    name = rng.choice(files)
                    if md5(self.extractor.read(name)) != original_md5(name):
                        errors.append('bad data for %s' % name)
            except Exception as ex:
                errors.append(repr(ex))

        threads = [threading.Thread(target=reader, args=(n,)) for n in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])

    def test_lazy_read_after_close(self):
        prefs['lazy archive reading'] = True
        files = self._open('Flat.zip')
        self.extractor.close()
        self.assertRaises(archive_extractor.ArchiveException,
                          self.extractor.read, files[0])

    def test_lazy_disabled(self):
        prefs['lazy archive reading'] = False
        files = self._open('Flat.zip')
        self.assertFalse(self.extractor.is_lazy())
        self.extractor.extract()
        self._wait_extracted(files)
        self.assertCountEqual(os.listdir(self.dest_dir), files)
        for name in files:
            self.assertEqual(md5(self.extractor.read(name)), original_md5(name))
            self.assertEqual(self.extractor.materialize(name),
                             os.path.join(self.dest_dir, name))

    def test_size_unknown_until_extracted(self):
        prefs['lazy archive reading'] = False
        files = self._open('Flat.zip')
        name = files[0]
        archive = self.extractor._archive
        extract = archive.extract
        writing = threading.Event()
        release = threading.Event()

        def slow_extract(filename, destination_dir):
            if filename == name:
                # The destination holds a partial file while extraction runs.
                with open(os.path.join(destination_dir, name), 'wb') as fp:
                    fp.write(b'x')
                writing.set()
                release.wait(10)
            extract(filename, destination_dir)

        archive.extract = slow_extract
        self.extractor.extract()
        try:
            self.assertTrue(writing.wait(10))
            self.assertIsNone(self.extractor.get_entry_size(name))
        finally:
            release.set()
        self._wait_extracted(files)
        self.assertEqual(self.extractor.get_entry_size(name),
                         os.path.getsize(get_testfile_path(_FLAT_CONTENTS[name])))

    def test_not_lazy_when_solid(self):
        prefs['lazy archive reading'] = True
        files = self._open('SolidFlat.tar.gz')
        self.assertFalse(self.extractor.is_lazy())
        self.extractor.extract()
        self._wait_extracted(files)
        self.assertCountEqual(os.listdir(self.dest_dir), files)

    def _open_listed(self, archive, timeout=10):
        """ Like _open(), but runs the GLib main loop while waiting, so that
        callbacks from other threads (e.g. password requests) are run. """
        self.extractor.setup(get_testfile_path('archives', archive), self.dest_dir)
        context = GLib.MainContext.default()
        deadline = time.monotonic() + timeout
        while self.extractor.get_files() is None:
            self.assertLess(time.monotonic(), deadline, 'listing timed out')
            if not context.iteration(False):
                time.sleep(0.01)
        return self.extractor.get_files()

    def _read_in_thread(self, names, timeout=5):
        """ Read <names> from another thread, without running the main
        loop, and return the md5 of each. Fails if reading does not finish
        within <timeout> (e.g. because it waits on a main loop callback). """
        result = {}

        def reader():
            for name in names:
                result[name] = md5(self.extractor.read(name))

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        thread.join(timeout)
        self.assertFalse(thread.is_alive(), 'read blocked on the main thread')
        return result

    def test_password_asked_while_listing(self):
        prefs['lazy archive reading'] = True
        asked = []

        class EncryptedArchive(archive_base.BaseArchive):
            # Like the RAR library: entries ask for the password when read.
            def iter_contents(self):
                self._is_encrypted = True
                yield 'page.png'

            def supports_random_access(self):
                return True

            def read(self, filename):
                self._get_password()
                return self._password.encode()

        def ask_for_password(archive):
            asked.append(threading.current_thread().name)
            return 'secret'

        with mock.patch.object(archive_tools, 'get_recursive_archive_handler',
                               lambda src, dst, type=None: EncryptedArchive(src)), \
             mock.patch.object(archive_password, 'ask_for_password', ask_for_password):
            self._open_listed('Flat.zip')
            self.assertEqual(asked, ['MainThread'])
            self.assertEqual(self._read_in_thread(['page.png']),
                             {'page.png': md5(b'secret')})
            self.assertIsNone(self.extractor.get_entry_size('page.png'))
        self.assertEqual(len(asked), 1)

    @unittest.skipUnless(sevenzip_external.SevenZipArchive.is_available(), '7z not available')
    def test_lazy_encrypted_rar(self):
        prefs['lazy archive reading'] = True
        asked = []

        def ask_for_password(archive):
            asked.append(archive)
            return 'password'

        with mock.patch.object(archive_password, 'ask_for_password', ask_for_password):
            # Encrypted data, plain headers: the password is only needed
            # to read entries, and must be asked for while listing.
            files = self._open_listed('Encrypted.rar')
            self.assertEqual(len(asked), 1)
            self.assertTrue(self.extractor.is_lazy())
            self.assertEqual(self._read_in_thread(files),
                             {name: original_md5(name) for name in files})
        self.assertEqual(len(asked), 1)

    def test_lazy_materialized_file_is_used(self):
        prefs['lazy archive reading'] = True
        files = self._open('Flat.zip')
        name = files[0]
        path = self.extractor.materialize(name)
        # Edited by an external program: reads and sizes follow the file.
        with open(path, 'wb') as fp:
            fp.write(b'EDITED')
        self.assertEqual(self.extractor.read(name), b'EDITED')
        self.assertEqual(self.extractor.get_entry_size(name), 6)
        # Removed: read from the archive again, and recreated on demand.
        os.unlink(path)
        self.assertEqual(md5(self.extractor.read(name)), original_md5(name))
        self.assertEqual(self.extractor.materialize(name), path)
        with open(path, 'rb') as fp:
            self.assertEqual(md5(fp.read()), original_md5(name))

    def test_lazy_failed_materialize(self):
        prefs['lazy archive reading'] = True
        files = self._open('Flat.zip')
        name = files[0]
        path = os.path.join(self.dest_dir, name)

        def extract(filename, destination_dir):
            with open(os.path.join(destination_dir, filename), 'wb') as fp:
                fp.write(b'PARTIAL')
            raise OSError('corrupt archive')

        with mock.patch.object(self.extractor._archive, 'extract', extract):
            self.assertRaises(OSError, self.extractor.materialize, name)
        self.assertFalse(os.path.exists(path))
        # Not recorded as materialized: the next attempt extracts it.
        self.assertEqual(self.extractor.materialize(name), path)
        with open(path, 'rb') as fp:
            self.assertEqual(md5(fp.read()), original_md5(name))
