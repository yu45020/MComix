# coding: utf-8

import functools
import hashlib
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

from . import MComixTest, get_testfile_path

from mcomix.archive import (
    archive_recursive,
    sevenzip_external,
    tar,
    zip,
)
import mcomix


def md5(path):
    hash = hashlib.md5()
    hash.update(open(path, 'rb').read())
    return hash.hexdigest()

class ArchiveFormatTest(object):

    # Removed from unittest in Python 3.
    assertItemsEqual = unittest.TestCase.assertCountEqual

    skip = None
    handler = None
    format = ''
    solid = False
    # Expected supports_random_access(); None means "not solid".
    random_access = None
    password = None
    header_encryption = False
    contents = ()
    archive = None

    @classmethod
    def _ask_password(cls, archive):
        if cls.password:
            return cls.password
        raise Exception('asked for password on unprotected archive!')

    @classmethod
    def setUpClass(cls):
        if cls.skip is not None:
            raise unittest.SkipTest(cls.skip)
        cls.archive_path = '%s.%s' % (get_testfile_path('archives', cls.archive), cls.format)
        cls.archive_contents = dict([
            (archive_name, filename)
            for name, archive_name, filename
            in cls.contents
        ])

    def setUp(self):
        super(ArchiveFormatTest, self).setUp()
        prompt = mock.patch.object(mcomix.archive.password, 'ask_for_password',
                                   self._ask_password)
        prompt.start()
        self.addCleanup(prompt.stop)
        self.dest_dir = tempfile.mkdtemp(prefix='extract.')
        self.archive = None

    def tearDown(self):
        if self.archive is not None:
            self.archive.close()
        super(ArchiveFormatTest, self).tearDown()

    def test_list_contents(self):
        self.archive = self.handler(self.archive_path)
        contents = self.archive.list_contents()
        self.assertItemsEqual(contents, list(self.archive_contents.keys()))

    def test_is_solid(self):
        self.archive = self.handler(self.archive_path)
        self.archive.list_contents()
        self.assertEqual(self.solid, self.archive.is_solid())

    def test_extract(self):
        self.archive = self.handler(self.archive_path)
        contents = self.archive.list_contents()
        self.assertItemsEqual(contents, list(self.archive_contents.keys()))
        # Use out-of-order extraction to try to trip implementation.
        for name in reversed(contents):
            self.archive.extract(name, self.dest_dir)
            path = os.path.join(self.dest_dir, name)
            self.assertTrue(os.path.isfile(path))
            extracted_md5 = md5(path)
            original_md5 = md5(get_testfile_path(self.archive_contents[name]))
            self.assertEqual((name, extracted_md5), (name, original_md5))

    def test_iter_extract(self):
        self.archive = self.handler(self.archive_path)
        contents = self.archive.list_contents()
        self.assertItemsEqual(contents, list(self.archive_contents.keys()))
        extracted = []
        for name in self.archive.iter_extract(reversed(contents), self.dest_dir):
            extracted.append(name)
            path = os.path.join(self.dest_dir, name)
            self.assertTrue(os.path.isfile(path))
            extracted_md5 = md5(path)
            original_md5 = md5(get_testfile_path(self.archive_contents[name]))
            self.assertEqual((name, extracted_md5), (name, original_md5))
        # Entries must have been extracted in the order they are listed in the archive.
        # (necessary to prevent bad performances on solid archives)
        self.assertEqual(extracted, contents)

    def test_read(self):
        self.archive = self.handler(self.archive_path)
        contents = self.archive.list_contents()
        self.assertItemsEqual(contents, list(self.archive_contents.keys()))
        # Use out-of-order reads to try to trip implementation.
        for name in reversed(contents):
            data = self.archive.read(name)
            original_md5 = md5(get_testfile_path(self.archive_contents[name]))
            self.assertEqual((name, hashlib.md5(data).hexdigest()), (name, original_md5))

    def test_read_and_extract(self):
        self.archive = self.handler(self.archive_path)
        contents = self.archive.list_contents()
        # Interleave reads and extractions: they share the archive state.
        for n, name in enumerate(reversed(contents)):
            original_md5 = md5(get_testfile_path(self.archive_contents[name]))
            if n % 2:
                data = self.archive.read(name)
                self.assertEqual((name, hashlib.md5(data).hexdigest()), (name, original_md5))
            else:
                self.archive.extract(name, self.dest_dir)
                path = os.path.join(self.dest_dir, name)
                self.assertEqual((name, md5(path)), (name, original_md5))

    def test_get_entry_size(self):
        self.archive = self.handler(self.archive_path)
        for name in self.archive.list_contents():
            size = self.archive.get_entry_size(name)
            if size is not None:
                original_size = os.path.getsize(get_testfile_path(self.archive_contents[name]))
                self.assertEqual((name, size), (name, original_size))

    def test_supports_random_access(self):
        self.archive = self.handler(self.archive_path)
        self.archive.list_contents()
        expected = not self.solid if self.random_access is None else self.random_access
        self.assertEqual(expected, self.archive.supports_random_access())


class RecursiveArchiveFormatTest(ArchiveFormatTest):

    base_handler = None

    def handler(self, archive):
        main_archive = self.base_handler(archive)
        return archive_recursive.RecursiveArchive(main_archive, self.dest_dir)


RECURSIVE_TWINS = {
    'ArchiveFormatZipFlatTest',
    'ArchiveFormatTarSolidFlatTest',
    'ArchiveFormatTarGzipSolidFlatTest',
    'ArchiveFormatZipEncryptedTest',
}

for name, handler, is_available, format, not_solid, solid, password, header_encryption in (
    ('7z (external)'    , sevenzip_external.SevenZipArchive, sevenzip_external.SevenZipArchive.is_available(), '7z'     , True , True , True , True  ),
    ('7z (external) lha', sevenzip_external.SevenZipArchive, sevenzip_external.SevenZipArchive.is_available(), 'lha'    , True , False, False, False ),
    ('7z (external) rar', sevenzip_external.SevenZipArchive, sevenzip_external.SevenZipArchive.is_available(), 'rar'    , True , True , True , True  ),
    ('7z (external) zip', sevenzip_external.SevenZipArchive, sevenzip_external.SevenZipArchive.is_available(), 'zip'    , True , False, True , False ),
    ('tar'              , tar.TarArchive                   , True                                            , 'tar'    , False, True , False, False ),
    ('tar (gzip)'       , tar.TarArchive                   , True                                            , 'tar.gz' , False, True , False, False ),
    ('tar (bzip2)'      , tar.TarArchive                   , True                                            , 'tar.bz2', False, True , False, False ),
    ('zip'              , zip.ZipArchive                   , True                                            , 'zip'    , True , False, True , False ),
):
    base_class_name = 'ArchiveFormat'
    base_class_name += ''.join([part.capitalize() for part in re.sub(r'[^\w]+', ' ', name).split()])
    base_class_name += '%sTest'
    base_class_dict = {
        'name': name,
        'handler': handler,
        'format': format,
    }
    if 'tar' == format:
        # Always "solid", but uncompressed entries can be read directly.
        base_class_dict['random_access'] = True

    skip = None
    if not is_available:
        skip = 'support for %s format with %s not available' % (format, name)
    base_class_dict['skip'] = skip

    base_class_list = []
    if not_solid:
        base_class_list.append(('', {}))
    if solid:
        base_class_list.append(('Solid', {'solid': True}))

    class_list = []

    if password:
        for variant, params in base_class_list:
            variant = variant + 'Encrypted'
            params = dict(params)
            params['password'] = 'password'
            params['contents'] = (
                ('arg.jpeg', 'arg.jpeg', 'images/01-JPG-Indexed.jpg'),
                ('foo.JPG' , 'foo.JPG' , 'images/04-PNG-Indexed.png'),
                ('bar.jpg' , 'bar.jpg' , 'images/02-JPG-RGB.jpg'    ),
                ('meh.png' , 'meh.png' , 'images/03-PNG-RGB.png'    ),
            )
            class_list.append((variant, params))
            if header_encryption:
                variant = variant + 'Header'
                params = dict(params)
                params['header_encryption'] = True
                class_list.append((variant, params))
    else:
        assert not header_encryption

    for sub_variant, is_supported, contents in (
        ('Flat', True, (
            ('arg.jpeg'            , 'arg.jpeg'            , 'images/01-JPG-Indexed.jpg'),
            ('foo.JPG'             , 'foo.JPG'             , 'images/04-PNG-Indexed.png'),
            ('bar.jpg'             , 'bar.jpg'             , 'images/02-JPG-RGB.jpg'    ),
            ('meh.png'             , 'meh.png'             , 'images/03-PNG-RGB.png'    ),
        )),
        ('Tree', True, (
            ('dir1/arg.jpeg'       , 'dir1/arg.jpeg'       , 'images/01-JPG-Indexed.jpg'),
            ('dir1/subdir1/foo.JPG', 'dir1/subdir1/foo.JPG', 'images/04-PNG-Indexed.png'),
            ('dir2/subdir1/bar.jpg', 'dir2/subdir1/bar.jpg', 'images/02-JPG-RGB.jpg'    ),
            ('meh.png'             , 'meh.png'             , 'images/03-PNG-RGB.png'    ),
        )),
        ('Unicode', True, (
            ('1-قفهسا.jpg'        , '1-قفهسا.jpg'        , 'images/01-JPG-Indexed.jpg'),
            ('2-רדןקמא.png'       , '2-רדןקמא.png'       , 'images/04-PNG-Indexed.png'),
            ('3-りえsち.jpg'      , '3-りえsち.jpg'      , 'images/02-JPG-RGB.jpg'    ),
            ('4-щжвщджл.png'      , '4-щжвщджл.png'      , 'images/03-PNG-RGB.png'    ),
        )),
        # Check we don't treat an entry name as an option or command line switch.
        ('OptEntry', True, (
            ('-rg.jpeg'            , '-rg.jpeg'            , 'images/01-JPG-Indexed.jpg'),
            ('--o.JPG'             , '--o.JPG'             , 'images/04-PNG-Indexed.png'),
            ('+ar.jpg'             , '+ar.jpg'             , 'images/02-JPG-RGB.jpg'    ),
            ('@eh.png'             , '@eh.png'             , 'images/03-PNG-RGB.png'    ),
        )),
        # Check an entry name is not used as glob pattern.
        ('GlobEntries', 'win32' != sys.platform, (
            ('[rg.jpeg'            , '[rg.jpeg'            , 'images/01-JPG-Indexed.jpg'),
            ('[]rg.jpeg'           , '[]rg.jpeg'           , 'images/02-JPG-RGB.jpg'    ),
            ('*oo.JPG'             , '*oo.JPG'             , 'images/04-PNG-Indexed.png'),
            ('?eh.png'             , '?eh.png'             , 'images/03-PNG-RGB.png'    ),
            # ('\\r.jpg'             , '\\r.jpg'             , 'images/blue.png'          ),
            # ('ba\\.jpg'            , 'ba\\.jpg'            , 'images/red.png'           ),
        )),
        # Same, Windows version.
        ('GlobEntries', 'win32' == sys.platform, (
            ('[rg.jpeg'            , '[rg.jpeg'            , 'images/01-JPG-Indexed.jpg'),
            ('[]rg.jpeg'           , '[]rg.jpeg'           , 'images/02-JPG-RGB.jpg'    ),
            ('*oo.JPG'             , '_oo.JPG'             , 'images/04-PNG-Indexed.png'),
            ('?eh.png'             , '_eh.png'             , 'images/03-PNG-RGB.png'    ),
            # ('\\r.jpg'             , '\\r.jpg'             , 'images/blue.png'          ),
            # ('ba\\.jpg'            , 'ba\\.jpg'            , 'images/red.png'           ),
        )),
        # Check how invalid filesystem characters are handled.
        # ('InvalidFileSystemChars', 'win32' == sys.platform, (
        #     ('a<g.jpeg'            , 'a_g.jpeg'            ,'images/01-JPG-Indexed.jpg'),
        #     ('f*o.JPG'             , 'f_o.JPG'             ,'images/04-PNG-Indexed.png'),
        #     ('b:r.jpg'             , 'b_r.jpg'             ,'images/02-JPG-RGB.jpg'    ),
        #     ('m?h.png'             , 'm_h.png'             ,'images/03-PNG-RGB.png'    ),
        # )),
    ):
        if not is_supported:
            continue
        contents = [
            [s.replace('/', os.sep) for s in names]
            for names in contents
        ]
        for variant, params in base_class_list:
            variant = variant + sub_variant
            params = dict(params)
            params['contents'] = contents
            class_list.append((variant, params))

    for variant, params in class_list:
        class_name = base_class_name % variant
        class_dict = dict(base_class_dict)
        class_dict.update(params)
        class_dict['archive'] = variant
        globals()[class_name] = type(class_name, (ArchiveFormatTest, MComixTest), class_dict)
        # RecursiveArchive only delegates, so a few in-process twins cover it.
        if class_name not in RECURSIVE_TWINS:
            continue
        class_name = 'Recursive' + class_name
        class_dict = dict(class_dict)
        class_dict['base_handler'] = class_dict['handler']
        del class_dict['handler']
        globals()[class_name] = type(class_name, (RecursiveArchiveFormatTest, MComixTest), class_dict)


# Custom tests for recursive archives support.

class RecursiveArchiveFormatRedAndBluesTest(RecursiveArchiveFormatTest):

    @classmethod
    def setUpClass(cls):
        skip = None
        if not cls.is_available:
            raise unittest.SkipTest('support for archive format not available')
        if not os.path.exists(cls.archive_path):
            skip = 'archive is missing: %s' % cls.archive_path
        if skip is not None:
            raise unittest.SkipTest(skip)
        cls.archive_contents = dict([
            (archive_name.replace('/', os.sep),
             get_testfile_path(filename))
            for archive_name, filename in
            cls.contents])

class RecursiveArchiveFormatTarRedAndBluesTest(RecursiveArchiveFormatRedAndBluesTest, MComixTest):

    base_handler = tar.TarArchive
    # Sub-archives need 7z support.
    is_available = sevenzip_external.SevenZipArchive.is_available()
    archive_path = get_testfile_path('archives', 'red_and_blues.tar')
    contents = (
        ('red_and_blues.7z/blues.rar/blue0.png', 'images/blue.png'),
        ('red_and_blues.7z/blues.rar/blue1.png', 'images/blue.png'),
        ('red_and_blues.7z/blues.rar/blue2.png', 'images/blue.png'),
        ('red_and_blues.7z/red.png'            , 'images/red.png' ),
        ('red_and_blues.rar/blues.7z/blue0.png', 'images/blue.png'),
        ('red_and_blues.rar/blues.7z/blue1.png', 'images/blue.png'),
        ('red_and_blues.rar/blues.7z/blue2.png', 'images/blue.png'),
        ('red_and_blues.rar/red.png'           , 'images/red.png' ),
    )
    solid = True

class RecursiveArchiveFormat7zRedAndBluesTest(RecursiveArchiveFormatRedAndBluesTest, MComixTest):

    base_handler = sevenzip_external.SevenZipArchive
    is_available = sevenzip_external.SevenZipArchive.is_available()
    archive_path = get_testfile_path('archives', 'red_and_blues.7z')
    contents = (
        ('blues.rar/blue0.png', 'images/blue.png'),
        ('blues.rar/blue1.png', 'images/blue.png'),
        ('blues.rar/blue2.png', 'images/blue.png'),
        ('red.png'            , 'images/red.png' ),
    )
    solid = True

class RecursiveArchiveFormat7zRarRedAndBluesTest(RecursiveArchiveFormatRedAndBluesTest, MComixTest):

    base_handler = sevenzip_external.SevenZipArchive
    is_available = sevenzip_external.SevenZipArchive.is_available()
    archive_path = get_testfile_path('archives', 'red_and_blues.rar')
    contents = (
        ('blues.7z/blue0.png', 'images/blue.png'),
        ('blues.7z/blue1.png', 'images/blue.png'),
        ('blues.7z/blue2.png', 'images/blue.png'),
        ('red.png'           , 'images/red.png' ),
    )
    solid = True

class RecursiveArchiveFormat7zRarEmbeddedRedAndBluesRarTest(RecursiveArchiveFormat7zRarRedAndBluesTest):

    archive_path = get_testfile_path('archives', 'embedded_red_and_blues_rar.rar')
    contents = (
        ('red_and_blues.rar/blues.7z/blue0.png', 'images/blue.png'),
        ('red_and_blues.rar/blues.7z/blue1.png', 'images/blue.png'),
        ('red_and_blues.rar/blues.7z/blue2.png', 'images/blue.png'),
        ('red_and_blues.rar/red.png'           , 'images/red.png' ),
    )

xfail_list = [
    # Not supported by 7z executable...
    ('7zExternalLhaUnicode'   , 'test_list_contents'),
    ('7zExternalLhaUnicode'   , 'test_iter_extract' ),
    ('7zExternalLhaUnicode'   , 'test_extract'      ),
] if 'win32' == sys.platform else []

# Reading in memory fails where extracting or listing does.
for test, attr in list(xfail_list):
    if 'test_extract' == attr:
        xfail_list.extend(((test, 'test_read'), (test, 'test_read_and_extract')))
    elif 'test_list_contents' == attr:
        xfail_list.append((test, 'test_get_entry_size'))

# Expected failures.
for test, attr in xfail_list:
    for name in (
        'ArchiveFormat%sTest' % test,
        'RecursiveArchiveFormat%sTest' % test,
    ):
        if not name in globals():
            continue
        klass = globals()[name]
        # Wrap first: the test function is shared with all other classes
        # (inherited from ArchiveFormatTest), and expectedFailure marks the
        # function object itself.
        func = getattr(klass, attr)
        wrapper = functools.wraps(func)(lambda self, func=func: func(self))
        setattr(klass, attr, unittest.expectedFailure(wrapper))

