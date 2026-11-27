# coding: utf-8

import os
import tempfile
import unittest
import zipfile
from unittest import mock

from PIL import Image

from . import MComixTest, get_testfile_path

from mcomix import portability
from mcomix import thumbnail_tools
from mcomix.archive import sevenzip_external, zip
from urllib.request import pathname2url


class ArchiveThumbnailTest(MComixTest):

    def setUp(self):
        super(ArchiveThumbnailTest, self).setUp()
        self.thumb_dir = os.path.join(self.tmp_dir, 'thumbnails')
        self.archive_path = os.path.join(self.tmp_dir, 'book.cbz')
        with zipfile.ZipFile(self.archive_path, 'w') as zf:
            zf.write(get_testfile_path('images', 'red.png'), '01.png')
            # 200x100: picked as cover because of its name.
            zf.write(get_testfile_path('images', 'pattern-opaque-rgb.png'), 'cover.png')
            zf.write(get_testfile_path('images', 'blue.png'), '02.png')

    def _thumbnailer(self, store_on_disk):
        return thumbnail_tools.Thumbnailer(dst_dir=self.thumb_dir,
                                           store_on_disk=store_on_disk,
                                           size=(64, 64),
                                           archive_support=True)

    def test_archive_thumbnail(self):
        pixbuf = self._thumbnailer(False).thumbnail(self.archive_path)
        self.assertIsNotNone(pixbuf)
        # Cover is 200x100.
        self.assertEqual((pixbuf.get_width(), pixbuf.get_height()), (64, 32))
        self.assertFalse(os.path.exists(self.thumb_dir))

    def test_archive_thumbnail_does_not_extract(self):
        def extract(*args, **kwargs):
            raise AssertionError('cover must be read in memory, not extracted')
        temp_dir = tempfile.gettempdir()
        before = os.listdir(temp_dir)
        with mock.patch.object(zip.ZipArchive, 'extract', extract):
            pixbuf = self._thumbnailer(False).thumbnail(self.archive_path)
        self.assertIsNotNone(pixbuf)
        self.assertEqual(os.listdir(temp_dir), before)

    def test_archive_thumbnail_stored(self):
        thumbnailer = self._thumbnailer(True)
        pixbuf = thumbnailer.thumbnail(self.archive_path)
        self.assertIsNotNone(pixbuf)
        thumbpath = thumbnailer._path_to_thumbpath(self.archive_path)
        self.assertTrue(os.path.isfile(thumbpath))
        info = Image.open(thumbpath).info
        stat = os.stat(self.archive_path)
        # Metadata describes the archive, with the cover's dimensions.
        self.assertEqual(info['Thumb::URI'],
                         portability.uri_prefix() + pathname2url(os.path.normpath(self.archive_path)))
        self.assertEqual(info['Thumb::MTime'], str(int(stat.st_mtime)))
        self.assertEqual(info['Thumb::Size'], str(stat.st_size))
        self.assertEqual((info['Thumb::Image::Width'], info['Thumb::Image::Height']), ('200', '100'))
        self.assertEqual(info['MComix::Thumb::Size'], '64x64')

    def test_archive_without_images(self):
        path = os.path.join(self.tmp_dir, 'empty.zip')
        with zipfile.ZipFile(path, 'w') as zf:
            zf.writestr('readme.txt', b'no images')
        self.assertIsNone(self._thumbnailer(False).thumbnail(path))

    @unittest.skipUnless(sevenzip_external.SevenZipArchive.is_available(), '7z not available')
    def test_rar_archive_thumbnail(self):
        pixbuf = self._thumbnailer(False).thumbnail(get_testfile_path('archives', 'Flat.rar'))
        self.assertIsNotNone(pixbuf)
