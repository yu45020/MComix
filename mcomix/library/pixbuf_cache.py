""" pixbuf_cache.py - Caches book covers for the library display."""
# -*- coding: utf-8 -*-

from collections import OrderedDict
import threading
from typing import Optional

from gi.repository import GdkPixbuf

__all__ = ["get_pixbuf_cache"]

#: Cover cache limits: number of covers and bytes of pixel storage.
MAX_CACHE_ITEMS = 500
MAX_CACHE_BYTES = 64 * 1024 * 1024


class _PixbufCache(object):

    """ Pixbuf cache for the library window. Instead of loading book covers
    from disk again after switching collection or using filtering, this class
    stores a bounded number of pixbufs in memory, evicting the least recently
    used ones as necessary. Covers are stored as compact private copies, so a
    subpixbuf cannot keep its larger parent alive.
    """

    def __init__(self, size: int, max_bytes: int = MAX_CACHE_BYTES) -> None:
        assert size > 0 and max_bytes > 0
        self.cachesize = size
        self.max_bytes = max_bytes
        #: Store book id => (pixbuf, bytes), least recently used first
        self._cache = OrderedDict()
        self._bytes = 0
        #: Bumped by invalidate_all to reject covers loaded before it
        self._generation = 0
        #: Ensure thread safety
        self._lock = threading.RLock()

    def generation(self) -> int:
        """ Returns the token to pass to add() for a cover loaded now. """
        return self._generation

    def add(self, id: object, pixbuf: GdkPixbuf.Pixbuf,
            generation: Optional[int] = None) -> None:
        """ Adds a copy of <pixbuf> for <id>, unless invalidate_all() was
        called since <generation> was taken or it exceeds the byte limit. """
        width, height = pixbuf.get_width(), pixbuf.get_height()
        with self._lock:
            if generation is not None and generation != self._generation:
                return
            self.invalidate(id)
            # Check the compact size before allocating the copy.
            rowstride = (width * pixbuf.get_n_channels() + 3) & ~3
            if rowstride * height > self.max_bytes:
                return
            owned = GdkPixbuf.Pixbuf.new(
                pixbuf.get_colorspace(), pixbuf.get_has_alpha(),
                pixbuf.get_bits_per_sample(), width, height)
            pixbuf.copy_area(0, 0, width, height, owned, 0, 0)
            size = owned.get_rowstride() * height
            while self._cache and (len(self._cache) >= self.cachesize or
                                   self._bytes + size > self.max_bytes):
                self.invalidate(next(iter(self._cache)))
            self._cache[id] = (owned, size)
            self._bytes += size

    def exists(self, id: object) -> bool:
        """ Checks if there is an entry for the given id in the cache. """
        with self._lock:
            return id in self._cache

    def get(self, id: object) -> Optional[GdkPixbuf.Pixbuf]:
        """ Returns the pixbuf for the given cache id, or None, if such
        an entry does not exist. The pixbuf must not be modified. """
        with self._lock:
            if id not in self._cache:
                return None
            self._cache.move_to_end(id)
            return self._cache[id][0]

    def invalidate(self, id: object) -> None:
        """ Invalidates the object with the specified cache ID. """
        with self._lock:
            entry = self._cache.pop(id, None)
            if entry is not None:
                self._bytes -= entry[1]

    def invalidate_all(self) -> None:
        """ Invalidates all cached objects and covers still being loaded. """
        with self._lock:
            self._generation += 1
            self._cache.clear()
            self._bytes = 0


_cache = None

def get_pixbuf_cache():
    global _cache

    if _cache:
        return _cache
    else:
        _cache = _PixbufCache(MAX_CACHE_ITEMS)
        return _cache

# vim: expandtab:sw=4:ts=4
