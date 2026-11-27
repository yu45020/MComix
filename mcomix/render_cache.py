"""Bounded redraw intermediates for immutable, generation-scoped pages.

Only the owning GTK thread uses these caches. Sources remain owned by the
image handler; weak references prevent a derived entry retaining decoded pages.
Returned pixbufs are shared and read-only, like decoded pages: the draw
pipeline's flips and enhancement create new images.
"""

from collections import OrderedDict
from typing import Any, Sequence, Tuple
import weakref

from mcomix import image_tools
from mcomix.preferences import prefs


class RenderCache:
    """Reuse conventional resizes and exact full-resolution edge colors."""

    def __init__(self, max_bytes: int = 64 * 1024 * 1024,
                 max_colors: int = 64, max_entries: int = 64) -> None:
        self.max_bytes = max(0, max_bytes)
        self.max_colors = max(0, max_colors)
        self.max_entries = max(0, max_entries)
        self.bytes_used = 0
        self._scaled = OrderedDict()
        self._colors = OrderedDict()

    def clear(self) -> None:
        """Drop all derived pixels when a book or edited page list changes."""
        self._scaled.clear()
        self._colors.clear()
        self.bytes_used = 0

    def prune(self, sources: Sequence[Any]) -> None:
        """Release intermediates whose decoded sources have been evicted."""
        live = {id(source) for source in sources}
        for key, (ref, pixbuf, size) in list(self._scaled.items()):
            if ref() is None or id(ref()) not in live:
                del self._scaled[key]
                self.bytes_used -= size
        for key, (refs, color) in list(self._colors.items()):
            if any(ref() is None or id(ref()) not in live for ref in refs):
                del self._colors[key]

    def render(self, generation: int, source: Any, rect: Sequence[int],
               rotation: int) -> Any:
        """Return exactly the conventional scale/rotate result (read-only).

        Enhancement and flips remain after this operation in the draw pipeline.
        Decoded sources must not be mutated in place: replacement/editing uses a
        new source or page generation, as in the decoded-page cache itself.
        """
        key = (generation, id(source), tuple(rect), rotation,
               prefs['scaling quality'],
               prefs['checkered bg for transparent images'])
        cached = self._scaled.get(key)
        if cached is not None and cached[0]() is source:
            self._scaled.move_to_end(key)
            return cached[1]
        result = image_tools.fit_pixbuf_to_rectangle(source, rect, rotation)
        # Native-size opaque output is already a no-op in image_tools. Keep
        # that cheap path and its existing immutable-source contract.
        if result is source:
            return result
        size = result.get_rowstride() * result.get_height()
        if size <= self.max_bytes and self.max_entries:
            previous = self._scaled.pop(key, None)
            if previous is not None:
                self.bytes_used -= previous[2]
            while self._scaled and (self.bytes_used + size > self.max_bytes
                                    or len(self._scaled) >= self.max_entries):
                old_key, (old_ref, old_pixbuf, old_size) = self._scaled.popitem(last=False)
                self.bytes_used -= old_size
            self._scaled[key] = (weakref.ref(source), result, size)
            self.bytes_used += size
        return result

    def background(self, generation: int, sources: Sequence[Any],
                   enhancer: Any) -> Tuple[int, int, int]:
        """Sample full-resolution enhancement once per ordered page/settings.

        Contrast and autocontrast require full-image statistics. Sampling a
        cropped or rendered image instead would change the background color.
        Animations retain the existing static-poster sampling behavior.
        """
        key = (generation, tuple(id(source) for source in sources),
               enhancer.settings_key())
        cached = self._colors.get(key)
        if cached is not None and all(ref() is source for ref, source in zip(cached[0], sources)):
            self._colors.move_to_end(key)
            return cached[1]
        samples = [enhancer.enhance(image_tools.static_image(source))
                   for source in sources]
        sample = samples[0] if len(samples) == 1 else tuple(samples)
        color = tuple(image_tools.get_most_common_edge_colour(sample))
        if self.max_colors:
            self._colors[key] = (tuple(weakref.ref(source) for source in sources), color)
            self._colors.move_to_end(key)
            while len(self._colors) > self.max_colors:
                self._colors.popitem(last=False)
        return color
