"""image_handler.py - Image handler that takes care of cacheing and giving out images."""

import os
import traceback
import threading
from typing import Any, List, Optional, Sequence, Tuple

from mcomix.preferences import prefs
from mcomix import i18n
from mcomix import tools
from mcomix import image_tools
from mcomix import thumbnail_tools
from mcomix import constants
from mcomix import callback
from mcomix import log
from mcomix import upscale
from mcomix.render_cache import RenderCache
from mcomix.worker_thread import WorkerThread
from mcomix.webp_animation import WebPPlayback

class _Decode(object):
    """One captured decode, shared by synchronous readers and the worker."""

    def __init__(self) -> None:
        self.ready = threading.Event()
        self.pixbuf = image_tools.MISSING_IMAGE_ICON


class ImageHandler(object):

    """The FileHandler keeps track of images, pages, caches and reads files.

    When the Filehandler's methods refer to pages, they are indexed from 1,
    i.e. the first page is page 1 etc.

    Other modules should *never* read directly from the files pointed to by
    paths given by the FileHandler's methods. The files are not even
    guaranteed to exist at all times since the extraction of archives is
    threaded.
    """

    def __init__(self, window):

        #: Reference to main window
        self._window = window

        #: Protect page-list replacement and decoded cache publication.
        self._page_lock = threading.RLock()
        self._page_generation = 0
        self._inflight_decodes = {}

        #: Caching thread
        self._thread = WorkerThread(self._cache_pixbuf, name='image',
                                    sort_orders=True)

        #: List of image file names, either from extraction or directory
        self._image_files = None
        #: Index of current page
        self._current_image_index = None
        #: Set of images reading for decoding (i.e. already extracted)
        self._available_images = set()
        #: List of pixbufs we want to cache
        self._wanted_pixbufs = []
        #: Pixbuf map from page > Pixbuf
        self._raw_pixbufs = {}
        self._render_cache = RenderCache()
        #: Dimensions survive pixbuf eviction, but never a page-list change.
        self._page_dimensions = {}
        self._wanted_page_width = 1
        #: How many pages to keep in cache
        self._cache_pages = prefs['max pages to cache']
        #: Neural upscaling of small pages
        self.upscaler = upscale.UpscaleService()
        self._animations: Optional[WebPPlayback] = None

        self._window.filehandler.file_available += self._file_available

    @property
    def animations(self) -> WebPPlayback:
        playback = self._animations
        if playback is None:
            playback = self._animations = WebPPlayback(self._window.images)
        return playback

    def stop_animations(self) -> None:
        if self._animations is not None:
            self._animations.clear()

    def _same_page(self, generation: int, index: int, path: str) -> bool:
        """Check a decode's identity while holding the page lock."""
        if generation != self._page_generation or self._image_files is None:
            return False
        return 0 <= index < len(self._image_files) and self._image_files[index] == path

    def _cancel_decodes(self) -> None:
        """Wake consumers of a replaced page list, while the owner finishes."""
        for decode in self._inflight_decodes.values():
            decode.ready.set()
        self._inflight_decodes.clear()

    def _get_pixbuf(self, index: int, generation: Optional[int] = None,
                    path: Optional[str] = None, cache_only: bool = False) -> Any:
        """Synchronously obtain one page, sharing any captured decode in flight.

        Explicit consumers such as clipboard/enhancement retain a synchronous
        contract. Redraw uses get_pixbufs(block=False) and never waits here.
        A demand reader can receive an evicted page without caching it again.
        """
        with self._page_lock:
            if generation is None:
                generation = self._page_generation
                if not self._image_files or not 0 <= index < len(self._image_files):
                    return image_tools.MISSING_IMAGE_ICON
                path = self._image_files[index]
            if not self._same_page(generation, index, path):
                return image_tools.MISSING_IMAGE_ICON
            if index in self._raw_pixbufs:
                return self._raw_pixbufs[index]
            if cache_only and index not in self._wanted_pixbufs:
                return image_tools.MISSING_IMAGE_ICON
            key = (generation, index, path)
            decode = self._inflight_decodes.get(key)
            owner = decode is None
            if owner:
                decode = _Decode()
                self._inflight_decodes[key] = decode
            available = index in self._available_images

        if not owner:
            decode.ready.wait()
            with self._page_lock:
                return decode.pixbuf if self._same_page(generation, index, path) else image_tools.MISSING_IMAGE_ICON

        completed = False
        pixbuf = image_tools.MISSING_IMAGE_ICON
        try:
            # Neither archive waits nor decoding hold the page lock.
            try:
                if not available:
                    self._window.filehandler._wait_on_file(path)
                with self._page_lock:
                    if not self._same_page(generation, index, path):
                        return image_tools.MISSING_IMAGE_ICON
                source = self._window.filehandler.get_image_source(path)
                pixbuf = image_tools.load_pixbuf(source)
            except Exception as ex:
                pixbuf = image_tools.MISSING_IMAGE_ICON
                log.error('Could not load pixbuf for page %u: %r', index + 1, ex)
            completed = True
        finally:
            # Decide publication and retire this job atomically. Returning to
            # the page during completion must neither lose its only wakeup nor
            # make a new request observe a half-retired in-flight decode.
            with self._page_lock:
                current = self._same_page(generation, index, path)
                published = completed and current and index in self._wanted_pixbufs
                if completed and current:
                    decode.pixbuf = pixbuf
                if published:
                    self._raw_pixbufs[index] = pixbuf
                if self._inflight_decodes.get(key) is decode:
                    del self._inflight_decodes[key]
                decode.ready.set()
            if published:
                tools.garbage_collect()
                self._page_decoded(generation, index, path)
        with self._page_lock:
            return decode.pixbuf if self._same_page(generation, index, path) else image_tools.MISSING_IMAGE_ICON

    @callback.Callback
    def _page_decoded(self, generation: int, index: int, path: str) -> None:
        """Discard obsolete idle notifications before public listeners run."""
        with self._page_lock:
            current = self._same_page(generation, index, path) and index in self._raw_pixbufs
        if current:
            # A decoded wide page can reduce a prospective pair to one page.
            # Rebuild only when that changes the working set's visible width.
            page = self.get_current_page()
            if page and page - 1 <= index <= page and \
                    self._get_page_width(page) != self._wanted_page_width:
                self.do_cacheing()
            self.page_cached(index + 1)

    def get_pixbufs(self, number_of_bufs: int, block: bool = True) -> Optional[List[Any]]:
        """Get current pixbufs; nonblocking redraw returns None while pending.

        The default synchronous API is retained for explicit image consumers.
        Nonblocking requests share the cache worker and finish via page_cached,
        including failures, which cache the missing-image placeholder.
        """
        with self._page_lock:
            if self._current_image_index is None:
                return []
            first = self._current_image_index
            if not block:
                result = [self._raw_pixbufs.get(first + i) for i in range(number_of_bufs)]
                if all(pixbuf is not None for pixbuf in result):
                    return result
        if not block:
            self.do_cacheing()
            return None
        return [self._get_pixbuf(first + i) for i in range(number_of_bufs)]

    def get_cached_pixbuf(self, page):
        """Return the pixbuf of <page> if it is already decoded, else None.
        Never blocks."""
        with self._page_lock:
            return self._raw_pixbufs.get(page - 1)

    def reset_page_list(self, image_files: List[str]) -> None:
        """Replace edited pages; the caller selects the new current page."""
        self.stop_animations()
        with self._page_lock:
            old_files = self._image_files or []
            available_paths = {old_files[index] for index in self._available_images
                               if 0 <= index < len(old_files)}
        image_files = list(image_files)
        available = set()
        filehandler = self._window.filehandler
        for index, path in enumerate(image_files):
            # Local imports have no extraction event to wait for. Try decoding
            # even a missing file so the normal missing-image fallback is shown.
            local = filehandler._archive_name(path) is None
            if local or path in available_paths or filehandler.file_is_available(path):
                available.add(index)
        with self._page_lock:
            self._page_generation += 1
            self._cancel_decodes()
            self._image_files = image_files
            self._current_image_index = None
            self._available_images = available
            self._raw_pixbufs = {}
            self._page_dimensions = {}
            self._wanted_page_width = 1
            self._wanted_pixbufs = []
        self._thread.clear_orders()
        self._render_cache.clear()
        self.upscaler.invalidate()

    def render_conventional(self, source: Any, rect: Sequence[int],
                            rotation: int) -> Any:
        """Reuse unchanged conventional resizes without sharing mutable output."""
        return self._render_cache.render(self._page_generation, source,
                                         rect, rotation)

    def get_pixbuf_auto_background(self, number_of_bufs: int) -> List[int]:
        """Return the exact raw-page edge color, reusing unchanged enhancement."""
        sources = self.get_pixbufs(number_of_bufs)
        assert len(sources) in (1, 2), 'Unexpected pixbuf count'
        if len(sources) == 2 and self._window.is_manga_mode:
            sources = list(reversed(sources))
        return list(self._render_cache.background(
            self._page_generation, sources, self._window.enhancer))

    def do_cacheing(self):
        """Keep visible pages first, then nearby pages up to the live limit."""
        self._cache_pages = prefs['max pages to cache']
        if not self._window.filehandler.file_loaded or not self.get_current_page():
            return

        # Flush caching orders.
        self._thread.clear_orders()
        # Get list of wanted pixbufs.
        wanted_pixbufs = self._ask_for_pages(self.get_current_page())
        with self._page_lock:
            if -1 != self._cache_pages:
                # No obsolete worker can repopulate the cache after eviction.
                for index in set(self._raw_pixbufs) - set(wanted_pixbufs):
                    del self._raw_pixbufs[index]
            self._render_cache.prune(list(self._raw_pixbufs.values()))
            log.debug('Caching page(s) %s', ' '.join([str(index + 1) for index in wanted_pixbufs]))
            self._wanted_pixbufs = wanted_pixbufs
            orders = [(priority, self._page_generation, index, self._image_files[index])
                      for priority, index in enumerate(wanted_pixbufs)
                      if index in self._available_images and index not in self._raw_pixbufs and (self._page_generation, index, self._image_files[index]) not in self._inflight_decodes]
        if len(orders) > 0:
            self._thread.extend_orders(orders)

    def _cache_pixbuf(self, wanted: Tuple[int, int, int, str]) -> None:
        priority, generation, index, path = wanted
        log.debug('Caching page %u', index + 1)
        self._get_pixbuf(index, generation, path, cache_only=True)

    def set_page(self, page_num):
        """Set up filehandler to the page <page_num>.
        """
        assert 0 < page_num <= self.get_number_of_pages()
        if self._current_image_index != page_num - 1:
            self.stop_animations()
        self._current_image_index = page_num - 1
        self.do_cacheing()

    def get_virtual_double_page(self, page: Optional[int] = None,
                                cached_only: bool = False) -> bool:
        """Return True if the current state warrants use of virtual
        double page mode (i.e. if double page mode is on, the corresponding
        preference is set, and one of the two images that should normally
        be displayed has a width that exceeds its height), or if currently
        on the first page.
        """
        if page == None:
            page = self.get_current_page()

        if (page == 1 and
            prefs['virtual double page for fitting images'] & constants.SHOW_DOUBLE_AS_ONE_TITLE and
            self._window.filehandler.archive_type is not None):
            return True

        if (not prefs['default double page'] or
            not prefs['virtual double page for fitting images'] & constants.SHOW_DOUBLE_AS_ONE_WIDE or
            page == self.get_number_of_pages()):
            return False

        for candidate in (page, page + 1):
            if not self.page_is_available(candidate):
                if cached_only:
                    continue
                return False
            if self._page_is_wide(candidate, cached_only):
                return True

        return False

    def _page_is_wide(self, page: int, cached_only: bool) -> Optional[bool]:
        """Read known geometry without decoding when planning cache work."""
        index = page - 1
        with self._page_lock:
            if not self._image_files or not 0 <= index < len(self._image_files):
                return None
            generation = self._page_generation
            path = self._image_files[index]
            dimensions = self._page_dimensions.get(index)
            pixbuf = self._raw_pixbufs.get(index)
        if dimensions is None:
            if pixbuf is None:
                if cached_only:
                    return None
                pixbuf = self._get_pixbuf(index)
            rotation = image_tools.get_implied_rotation(pixbuf)
            dimensions = (pixbuf.get_width(), pixbuf.get_height(), rotation)
            with self._page_lock:
                if not self._same_page(generation, index, path):
                    return None
                self._page_dimensions[index] = dimensions
        width, height, rotation = dimensions
        if prefs['auto rotate from exif'] and tools.rotation_swaps_axes(rotation):
            width, height = height, width
        return width > height

    def _get_page_width(self, page: int) -> int:
        """Use known display rules, conservatively keeping an undecoded pair."""
        if not prefs['default double page'] or page >= self.get_number_of_pages():
            return 1
        return 1 if self.get_virtual_double_page(page, cached_only=True) else 2

    def get_real_path(self):
        """Return the "real" path to the currently viewed file, i.e. the
        full path to the archive or the full path to the currently
        viewed image.
        """
        if self._window.filehandler.archive_type is not None:
            return self._window.filehandler.get_path_to_base()
        return self.get_path_to_page()

    def cleanup(self):
        """Run clean-up tasks. Should be called prior to exit."""

        self.stop_animations()
        with self._page_lock:
            self._page_generation += 1
            self._cancel_decodes()
        self._thread.stop()
        # Don't stop the upscaler: it outlives books, and waiting for it
        # would block. Its late results are ignored.
        self.upscaler.invalidate()
        self._image_files = []
        self._current_image_index = None
        self._available_images.clear()
        self._raw_pixbufs.clear()
        self._render_cache.clear()
        self._page_dimensions.clear()
        self._wanted_page_width = 1
        self._cache_pages = prefs['max pages to cache']

    def page_is_available(self, page=None):
        """ Returns True if <page> is available and calls to get_pixbufs
        would not block. If <page> is None, the current page(s) are assumed. """

        if page is None:
            current_page = self.get_current_page()
            if not current_page:
                # Current 'book' has no page.
                return False
            index_list = [ current_page - 1 ]
            if self._window.displayed_double() and current_page < len(self._image_files):
                index_list.append(current_page)
        else:
            index_list = [ page - 1 ]

        for index in index_list:
            if not index in self._available_images:
                return False

        return True

    @callback.Callback
    def page_cached(self, page):
        """ Called in the main thread when <page> has been decoded. """
        pass

    @callback.Callback
    def page_available(self, page):
        """ Called whenever a new page becomes available, i.e. the corresponding
        file has been extracted. """
        log.debug('Page %u is available', page)
        index = page - 1
        assert index not in self._available_images
        self._available_images.add(index)
        # Check if we need to cache it.
        priority = None
        if index in self._wanted_pixbufs:
            # In the list of wanted pixbufs.
            priority = self._wanted_pixbufs.index(index)
        elif -1 == self._cache_pages:
            # We're caching everything.
            priority = self.get_number_of_pages()
        if priority is not None:
            self._thread.append_order((priority, self._page_generation, index,
                                       self._image_files[index]))

    def _file_available(self, filepaths):
        """ Called by the filehandler when a new file becomes available. """
        # Find the page that corresponds to <filepath>
        if not self._image_files:
            return

        available = sorted(filepaths)
        for i, imgpath in enumerate(self._image_files):
            if i not in self._available_images and tools.bin_search(available, imgpath) >= 0:
                self.page_available(i + 1)

    def get_number_of_pages(self):
        """Return the number of pages in the current archive/directory."""
        if self._image_files is not None:
            return len(self._image_files)
        else:
            return 0

    def get_current_page(self):
        """Return the current page number (starting from 1), or 0 if no file is loaded."""
        if self._current_image_index is not None:
            return self._current_image_index + 1
        else:
            return 0

    def get_path_to_page(self, page=None):
        """Return the full path to the image file for <page>, or the current
        page if <page> is None.
        """
        if page is None:
            index = self._current_image_index
        else:
            index = page - 1

        if self._image_files and index is not None and 0 <= index < len(self._image_files):
            return self._image_files[index]
        else:
            return None

    def get_page_filename(self, double=False):
        """Return the filename of the currently viewed page. If <double> is
        True, return a tuple (p, p') where p is the filename of the current
        page and p' is the filename of the page after.
        """
        page = self.get_current_page()

        first_path = self.get_path_to_page(page)
        if first_path == None:
            return None

        if double:
            second_path = self.get_path_to_page(page + 1)

            if second_path != None:
                first = os.path.basename(first_path)
                second = os.path.basename(second_path)
            else:
                return None

            return first, second

        return os.path.basename(first_path)

    def get_page_filesize(self, double=False):
        """Return the filesize of the currently viewed page. If <double> is
        True, return a tuple (s, s') where s is the filesize of the current
        page and s' is the filesize of the page after.
        """
        returnvalue_on_error = ('', '') if double else ''

        if not self.page_is_available():
            return returnvalue_on_error

        page = self.get_current_page()

        first_path = self.get_path_to_page(page)
        if first_path == None:
            return returnvalue_on_error

        def format_size(path):
            size = self._window.filehandler.get_file_size(path)
            return '' if size is None else tools.format_byte_size(size)

        if double:
            second_path = self.get_path_to_page(page + 1)
            if second_path != None:
                first = format_size(first_path)
                second = format_size(second_path)
            else:
                return ('', '')
            return first, second

        return format_size(first_path)

    def get_pretty_current_filename(self):
        """Return a string with the name of the currently viewed file that is
        suitable for printing.
        """
        if self._window.filehandler.archive_type is not None:
            name = os.path.basename(self._window.filehandler._base_path)
        elif self._image_files and self._current_image_index is not None:
            img_file = os.path.abspath(self._image_files[self._current_image_index])
            name = os.path.join(
                os.path.basename(os.path.dirname(img_file)),
                os.path.basename(img_file)
            )
        else:
            name = ''

        return i18n.to_unicode(name)

    def get_size(self):
        """Return a tuple (width, height) with the size of the current page.
        """
        self._wait_on_page(None)

        page_path = self.get_path_to_page()
        if page_path is None:
            return (0, 0)

        format, dimensions, providers = self._get_image_info(page_path)
        return dimensions

    def get_mime_name(self):
        """Return a string with the name of the mime type of the current page.
        """
        self._wait_on_page(None)

        page_path = self.get_path_to_page()
        if page_path is None:
            return None

        format, dimensions, providers = self._get_image_info(page_path)
        return format

    def _get_image_info(self, path):
        """ Same as image_tools.get_image_info(), for the page at <path>. """
        try:
            source = self._window.filehandler.get_image_source(path)
        except Exception as e:
            log.error('Could not read %s: %r', path, e)
            source = b''
        return image_tools.get_image_info(source)

    def get_thumbnail(self, page=None, width=128, height=128, nowait=False):
        """Return a thumbnail pixbuf of <page> that fit in a box with
        dimensions <width>x<height>. Return a thumbnail for the current
        page if <page> is None.

        If <nowait> is True, don't wait for <page> to be available.
        """
        if not self._wait_on_page(page, check_only=nowait):
            # Page is not available!
            return None
        path = self.get_path_to_page(page)

        if path == None:
            return None

        try:
            source = self._window.filehandler.get_image_source(path)
            if isinstance(source, bytes):
                # Read on demand from an archive: there is no file
                # to store a thumbnail for.
                return image_tools.load_pixbuf_size(source, width, height)
            thumbnailer = thumbnail_tools.Thumbnailer(store_on_disk=False,
                                                      size=(width, height))
            return thumbnailer.thumbnail(path)
        except Exception:
            log.debug("Failed to create thumbnail for image `%s':\n%s",
                      path, traceback.format_exc())
            return image_tools.MISSING_IMAGE_ICON

    def _wait_on_page(self, page, check_only=False):
        """Block the running (main) thread until the file corresponding to
        image <page> has been fully extracted.

        If <check_only> is True, only check (and return status), don't wait.
        """
        if page is None:
            index = self._current_image_index
        else:
            index = page - 1
        if index in self._available_images:
            # Already extracted!
            return True
        if check_only:
            # Asked for check only...
            return False

        path = self.get_path_to_page(page)
        if path is None:
            return False
        log.debug('Waiting for page %u', index + 1)
        self._window.filehandler._wait_on_file(path)
        return True

    def _ask_for_pages(self, page):
        """Ask for pages around <page> to be given priority extraction.
        """
        files = []
        total = self.get_number_of_pages()
        if not 1 <= page <= total:
            return []
        page_width = self._get_page_width(page)
        self._wanted_page_width = page_width
        if self._cache_pages == -1:
            num_pages = total
        else:
            # A finite limit never evicts a page that is being displayed.
            num_pages = min(total, max(page_width, self._cache_pages))
        first = page - 1
        following = list(range(first, min(total, first + num_pages)))
        previous = list(range(max(0, first - page_width), first))
        # Current screen, next screen, previous screen, then farther pages.
        page_list = following[:2 * page_width] + previous + following[2 * page_width:]
        # Near the end, use earlier pages to fill the remaining cache slots.
        missing = num_pages - len(page_list)
        if missing > 0:
            start = first - page_width - 1
            page_list.extend(range(start, max(-1, start - missing), -1))
        page_list = page_list[:num_pages]

        log.debug('Ask for priority extraction around page %u: %s',
                  page, ' '.join([str(n + 1) for n in page_list]))

        for index in page_list:
            if index not in self._available_images:
                files.append(self._image_files[index])

        if len(files) > 0:
            self._window.filehandler._ask_for_files(files)

        return page_list

# vim: expandtab:sw=4:ts=4
