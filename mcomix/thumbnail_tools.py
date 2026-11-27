"""thumbnail.py - Thumbnail module for MComix implementing (most of) the
freedesktop.org "standard" at http://jens.triq.net/thumbnail-spec/
"""

import io
import os
import stat
import re
import shutil
import tempfile
import mimetypes
import threading
import locale
import PIL.Image as Image
from urllib.request import pathname2url
from typing import Optional, TYPE_CHECKING
from hashlib import md5

from mcomix.preferences import prefs
from mcomix import constants
from mcomix import archive_tools
from mcomix import tools
from mcomix import image_tools
from mcomix import portability
from mcomix import log
from mcomix.i18n import _

if TYPE_CHECKING:
    from gi.repository import GdkPixbuf


class Thumbnailer(object):
    """ The Thumbnailer class is responsible for managing MComix
    internal thumbnail creation. Depending on its settings,
    it either stores thumbnails on disk and retrieves them later,
    or simply creates new thumbnails each time it is called. """

    def __init__(self, dst_dir=constants.THUMBNAIL_PATH, store_on_disk=None,
                 size=None, archive_support=False):
        """
        <dst_dir> set the thumbnailer's storage directory.

        If <store_on_disk> on disk is True, it changes the thumbnailer's
        behaviour to store files on disk, or just create new thumbnails each
        time it was called when set to False. Defaults to the 'create
        thumbnails' preference if not set.

        The dimensions for the created thumbnails is set by <size>, a (width,
        height) tupple. Defaults to the 'thumbnail size' preference if not set.

        If <archive_support> is True, support for archive thumbnail creation
        (based on cover detection) is enabled. Otherwise, only image files are
        supported.
        """
        self.dst_dir = dst_dir
        if store_on_disk is None:
            self.store_on_disk = prefs['create thumbnails']
        else:
            self.store_on_disk = store_on_disk
        if size is None:
            self.width = self.height = prefs['thumbnail size']
        else:
            self.width, self.height = size
        self.archive_support = archive_support
        self._cancelled = threading.Event()
        self._archive_lock = threading.Lock()
        self._archive = None

    def thumbnail(self, filepath: str) -> Optional["GdkPixbuf.Pixbuf"]:
        """ Returns a thumbnail pixbuf for <filepath>, transparently handling
        both normal image files and archives. If a thumbnail file already exists,
        it is re-used. Otherwise, a new thumbnail is created from <filepath>.

        Returns None if thumbnail creation failed. """

        if self._cancelled.is_set():
            return None
        pixbuf = self._cached_thumbnail(filepath)
        if pixbuf is not None:
            return pixbuf
        return self._create_thumbnail(filepath)

    def cancel(self) -> None:
        """Cancel owned archive/password work; a decoder already running may finish."""
        self._cancelled.set()
        with self._archive_lock:
            if self._archive is not None:
                self._archive.cancel()

    def delete(self, filepath):
        """ Deletes the thumbnail for <filepath> (if it exists) """
        thumbpath = self._path_to_thumbpath(filepath)
        if os.path.isfile(thumbpath):
            try:
                os.remove(thumbpath)
            except IOError as error:
                log.error(_("! Could not remove file \"%s\""), thumbpath)
                log.error(error)

    def _create_thumbnail_pixbuf(self, filepath):
        """ Creates a thumbnail pixbuf from <filepath>, and returns it as a
        tuple along with a file metadata dictionary: (pixbuf, tEXt_data) """

        if self.archive_support:
            mime = archive_tools.archive_mime_type(filepath)
        else:
            mime = None
        if mime is not None:
            cleanup = []
            try:
                tmpdir = tempfile.mkdtemp(prefix='mcomix_archive_thumb.')
                cleanup.append(lambda: shutil.rmtree(tmpdir, True))
                archive = archive_tools.get_recursive_archive_handler(filepath,
                                                                      tmpdir,
                                                                      type=mime)
                if archive is None:
                    return None, None
                cleanup.append(archive.close)
                with self._archive_lock:
                    self._archive = archive
                    if self._cancelled.is_set():
                        archive.cancel()
                if self._cancelled.is_set():
                    return None, None
                files = archive.list_contents()
                if self._cancelled.is_set():
                    return None, None
                wanted = self._guess_cover(files)
                if wanted is None:
                    return None, None

                # Read the cover into memory instead of extracting it.
                try:
                    image_data = archive.read(wanted)
                except Exception as ex:
                    log.debug('Could not read cover "%s" of "%s": %s', wanted, filepath, ex)
                    return None, None
                if not image_data:
                    return None, None

                pixbuf = image_tools.load_pixbuf_size(image_data, self.width, self.height)
                if self.store_on_disk:
                    # Describe the archive (the thumbnailed file),
                    # with the dimensions of its cover.
                    tEXt_data = self._get_text_data(filepath)
                    width, height = image_tools.get_image_info(image_data)[1]
                    tEXt_data['tEXt::Thumb::Image::Width'] = str(width)
                    tEXt_data['tEXt::Thumb::Image::Height'] = str(height)
                else:
                    tEXt_data = None

                return pixbuf, tEXt_data
            finally:
                with self._archive_lock:
                    self._archive = None
                for fn in reversed(cleanup):
                    fn()

        elif image_tools.is_image_file(filepath):
            pixbuf = image_tools.load_pixbuf_size(filepath, self.width, self.height)
            if self.store_on_disk:
                tEXt_data = self._get_text_data(filepath)
            else:
                tEXt_data = None

            return pixbuf, tEXt_data
        else:
            return None, None

    def _create_thumbnail(self, filepath: str) -> Optional["GdkPixbuf.Pixbuf"]:
        """ Creates the thumbnail pixbuf for <filepath>, and saves the pixbuf
        to disk if necessary. Returns the created pixbuf, or None, if creation failed. """

        if self._cancelled.is_set():
            return None
        try:
            pixbuf, tEXt_data = self._create_thumbnail_pixbuf(filepath)
        except Exception as error:
            log.debug('Could not create thumbnail for "%s": %s', filepath, error)
            raise
        if self._cancelled.is_set():
            return None
        if pixbuf is not None and self.store_on_disk:
            self._save_thumbnail(pixbuf, self._path_to_thumbpath(filepath), tEXt_data)
        return pixbuf

    def _get_text_data(self, filepath):
        """ Creates a tEXt dictionary for <filepath>. """
        mime = mimetypes.guess_type(filepath)[0] or "unknown/mime"
        uri = portability.uri_prefix() + pathname2url(os.path.normpath(filepath))
        stat = os.stat(filepath)
        # MTime could be floating point number, so convert to long first to have a fixed point number
        mtime = str(int(stat.st_mtime))
        size = str(stat.st_size)
        width, height = image_tools.get_image_info(filepath)[1]
        return {
            'tEXt::Thumb::URI':           uri,
            'tEXt::Thumb::MTime':         mtime,
            'tEXt::Thumb::Size':          size,
            'tEXt::Thumb::Mimetype':      mime,
            'tEXt::Thumb::Image::Width':  str(width),
            'tEXt::Thumb::Image::Height': str(height),
            'tEXt::MComix::Thumb::Size':  self._requested_size(),
            'tEXt::Software':             'MComix %s' % constants.VERSION
        }

    def _save_thumbnail(self, pixbuf, thumbpath, tEXt_data):
        """ Saves <pixbuf> as <thumbpath>, with additional metadata
        from <tEXt_data>. If <thumbpath> already exists, it is overwritten. """

        try:
            directory = os.path.dirname(thumbpath)
            if not os.path.isdir(directory):
                os.makedirs(directory, 0o700)
            if os.path.isfile(thumbpath):
                os.remove(thumbpath)

            option_keys = []
            option_values = []
            for key, value in list(tEXt_data.items()):
                option_keys.append(key)
                option_values.append(value)
            pixbuf.savev(thumbpath, 'png', option_keys, option_values)
            os.chmod(thumbpath, 0o600)

        except Exception as ex:
            log.warning( _('! Could not save thumbnail "%(thumbpath)s": %(error)s'),
                { 'thumbpath' : thumbpath, 'error' : ex } )

    def _requested_size(self) -> str:
        """ Return the bounding box recorded with saved thumbnails. """
        return '%dx%d' % (self.width, self.height)

    def _cached_thumbnail(self, filepath: str) -> Optional["GdkPixbuf.Pixbuf"]:
        """Return the disk thumbnail of <filepath> when its identity, source
        freshness and requested size match and its pixels decode, else None."""
        thumbpath = self._path_to_thumbpath(filepath)
        try:
            with open(thumbpath, 'rb') as cache:
                data = cache.read()
            # verify() checks every chunk CRC but leaves the image unusable,
            # and load() checks no CRC from IDAT on: parse twice, decode once.
            with Image.open(io.BytesIO(data)) as img:
                info = img.info
                uri = portability.uri_prefix() + pathname2url(os.path.normpath(filepath))
                if info.get('Thumb::URI') != uri:
                    return None
                if info.get('MComix::Thumb::Size') != self._requested_size():
                    return None
                stored_mtime = int(info['Thumb::MTime'])
                stored_size = int(info['Thumb::Size'])
                try:
                    source_stat = os.stat(filepath)
                except FileNotFoundError:
                    # Keep covers available for temporarily offline library books.
                    source_stat = None
                if source_stat is not None:
                    if not stat.S_ISREG(source_stat.st_mode):
                        return None
                    if stored_mtime != int(source_stat.st_mtime) or stored_size != source_stat.st_size:
                        return None
                img.verify()
            with Image.open(io.BytesIO(data)) as img:
                # Valid CRCs can still frame pixel data that doesn't decode.
                img.load()
                return image_tools.pil_to_pixbuf(img, keep_orientation=True)
        except Exception:
            # Missing, corrupt or incompatible thumbnails are disposable caches,
            # and a malformed chunk can raise anything from Pillow's parser.
            return None

    def _path_to_thumbpath(self, filepath):
        """ Converts <path> to an URI for the thumbnail in <dst_dir>. """
        uri = portability.uri_prefix() + pathname2url(os.path.normpath(filepath))
        return self._uri_to_thumbpath(uri)

    def _uri_to_thumbpath(self, uri):
        """ Return the full path to the thumbnail for <uri> with <dst_dir>
        being the base thumbnail directory. """
        uri = uri.encode(locale.getpreferredencoding()) if isinstance(uri, str) else uri
        md5hash = md5(uri).hexdigest()
        thumbpath = os.path.join(self.dst_dir, md5hash + '.png')
        return thumbpath

    def _guess_cover(self, files):
        """Return the filename within <files> that is the most likely to be the
        cover of an archive using some simple heuristics.
        """
        # Ignore MacOSX meta files.
        files = filter(lambda filename:
                '__MACOSX' not in os.path.normpath(filename).split(os.sep),
                files)
        # Ignore credit files if possible.
        files = filter(lambda filename:
                'credit' not in os.path.split(filename)[1].lower(), files)

        images = list(filter(image_tools.is_image_file, files))

        tools.alphanumeric_sort(images)

        front_re = re.compile('(cover|front)', re.I)
        candidates = list(filter(front_re.search, images))
        candidates = [c for c in candidates if 'back' not in c.lower()]

        if candidates:
            return candidates[0]

        if images:
            return images[0]

        return None

# vim: expandtab:sw=4:ts=4
