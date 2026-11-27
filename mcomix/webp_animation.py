"""Visible WebP playback, with bounded original frames and worker resizing."""

from contextlib import nullcontext
import math
import threading
import time
from typing import Any, Dict, List, Optional, Tuple, cast

from gi.repository import GLib, GdkPixbuf
from PIL import Image

from mcomix import callback, constants, image_tools, log, tools
from mcomix.preferences import prefs
from mcomix.worker_thread import WorkerThread


RESIZE_DELAY = 0.15
LOOKAHEAD = 0.3
_BILINEAR = getattr(Image, 'Resampling', Image).BILINEAR


def _pillow_render_memory(target: tuple, source_size: Tuple[int, int]) -> int:
    rect, rotation = target[:2]
    size = (rect[1], rect[0]) if tools.rotation_swaps_axes(rotation) else tuple(rect)
    output = int(rect[0]) * int(rect[1]) * 4
    # Pillow's separable resize can retain a horizontal pass spanning the
    # source height while allocating the final vertical-pass result.
    intermediate = int(size[0]) * source_size[1] * 4 if size[0] != source_size[0] and size[1] != source_size[1] else 0
    return max(3 * output, intermediate + output)


def _render_memory(target: tuple, source: Any) -> Tuple[int, int]:
    rect, rotation, horizontal, vertical, enhancement, quality, checker = target
    size = (rect[1], rect[0]) if tools.rotation_swaps_axes(rotation) else tuple(rect)
    originals = 0
    if enhancement != (1.0, 1.0, 1.0, 1.0, False, False):
        buffers = 4  # Scaled pixbuf plus Pillow's input, degenerate and result.
    else:
        buffers = 2 if rotation or horizontal or vertical else 1
    if isinstance(source, Image.Image):
        buffers = max(buffers, 3)  # Pillow resize, byte export and GLib copy.
        if quality not in (GdkPixbuf.InterpType.BILINEAR, image_tools.SCALING_LANCZOS):
            originals = 2  # Full-size export for GDK nearest/tiles rendering.
    elif quality == image_tools.SCALING_LANCZOS and not source.get_has_alpha() and size != (source.get_width(), source.get_height()):
        buffers = max(buffers, 3)  # Pillow image, byte export and GLib copy.
        originals = 2
    pending = int(rect[0]) * int(rect[1]) * 4 * buffers
    if isinstance(source, Image.Image):
        pending = max(pending, _pillow_render_memory(target, source.size))
    return pending, originals


class _Page:
    def __init__(self, identity: Any, poster: Any) -> None:
        self.identity = identity
        self.poster = poster
        self.animation = poster.webp_animation
        self.started = time.monotonic()
        self.frames = [(0, 0.0, poster, self.animation.duration)]
        self.next_frame = (1, self.animation.duration)
        self.capacity = 1
        self.pillow_frames = False
        self.target: tuple = ()
        self.revision = 0
        self.settle = 0.0
        self.fast = False
        self.display: Any = None
        self.display_revision = -1
        self.presented = -1
        self.next_due = self.started
        self.ready: Any = None
        self.rendering_frame: Any = None
        self.rendering_bytes = 0
        self.rendering_originals = 0
        self.decoding = False
        self.stopped = False
        self.failed = False
        self.finished = False
        self.condition = threading.Condition()
        self.worker = WorkerThread(self.run, name='webp-decode')
        self.renderer = WorkerThread(self.run_renderer, name='webp-render')

    def close(self) -> None:
        with self.condition:
            self.stopped = True
            self.condition.notify_all()
        self.worker.request_stop()
        self.renderer.request_stop()
        self.worker.stop()
        self.renderer.stop()
        self.frames.clear()
        self.ready = self.display = None

    def render(self, frame: Any, target: tuple, fast: bool) -> Any:
        rect, rotation, horizontal, vertical, enhancement, quality, checker = target
        quality = GdkPixbuf.InterpType.BILINEAR if fast else quality
        if isinstance(frame, Image.Image):
            # Preserve the bilinear policy for WebP canvases carrying alpha,
            # even when their composed pixels happen to be entirely opaque.
            if frame.info.get('webp opaque alpha') and quality == image_tools.SCALING_LANCZOS:
                quality = GdkPixbuf.InterpType.BILINEAR
            if frame.info.get('webp opaque alpha'):
                # The existing RGBA enhancement path skips autocontrast, while
                # still applying an explicitly configured contrast factor.
                enhancement = (*enhancement[:4], False, enhancement[5])
            size = (rect[1], rect[0]) if tools.rotation_swaps_axes(rotation) else tuple(rect)
            if quality in (GdkPixbuf.InterpType.BILINEAR, image_tools.SCALING_LANCZOS):
                resampling = image_tools._LANCZOS if quality == image_tools.SCALING_LANCZOS else _BILINEAR
                scaled: Any = frame.resize(size, resampling) if frame.size != size else frame
                result = image_tools.rotate_pixbuf(image_tools.pil_to_pixbuf(scaled), rotation)
                scaled = None
            else:
                # Keep GDK's nearest/tiles interpolation semantics.
                result = image_tools.fit_in_rectangle(
                    image_tools.pil_to_pixbuf(frame), rect[0], rect[1], keep_ratio=False,
                    scale_up=True, rotation=rotation, scaling_quality=quality)
        else:
            result = image_tools.fit_in_rectangle(
                frame, rect[0], rect[1], keep_ratio=False, scale_up=True, rotation=rotation,
                scaling_quality=quality)
        if horizontal:
            result = image_tools.flip_pixbuf(result, 0)
        if vertical:
            result = image_tools.flip_pixbuf(result, 1)
        if enhancement != (1.0, 1.0, 1.0, 1.0, False, False):
            result = image_tools.enhance(result, *enhancement)
        return result

    def capture(self, decoder: Any) -> Any:
        """Own a composed canvas without exporting full-size opaque pixels."""
        if self.pillow_frames:
            alpha = decoder.mode == 'RGBA'
            if decoder.mode == 'RGB' or (alpha and decoder.getchannel('A').getextrema() == (255, 255)):
                frame = decoder.convert('RGB')
                frame.info['webp opaque alpha'] = alpha
                return frame
        return image_tools.pil_to_pixbuf(decoder)

    def trim_frames(self, slots: int) -> None:
        """Release queued pixels, remembering the first unseen frame to decode again."""
        for frame in self.frames[slots:]:
            if self.presented < frame[0] < self.next_frame[0]:
                self.next_frame = (frame[0], frame[1])
                self.finished = False
                break
        del self.frames[slots:]

    def source_count(self) -> int:
        """Count queued, reserved decode and separately pinned render sources.

        The caller holds condition, including when removing queued frames.
        """
        pinned = self.rendering_frame is not None and all(item is not self.rendering_frame for item in self.frames)
        return len(self.frames) + int(self.decoding) + int(pinned)

    def fail(self) -> None:
        with self.condition:
            if self.failed:
                return
            self.failed = True
            self.ready = None
            self.frames.clear()
            self.condition.notify_all()

    def run_renderer(self, order: Any) -> None:
        rendered: Any = None
        frame: Any = None
        try:
            while True:
                with self.condition:
                    if self.stopped:
                        return
                    fallback = self.failed
                    if fallback:
                        frame = (0, 0.0, self.poster, self.animation.duration)
                        if rendered == (0, self.revision, True):
                            frame = None
                    else:
                        frame = next((item for item in self.frames
                                      if item[0] >= self.presented
                                      if rendered is None or rendered[1] != self.revision or item[0] > rendered[0]), None)
                    if self.ready is not None or frame is None:
                        frame = None
                        self.condition.wait()
                        continue
                    key = (frame[0], self.revision)
                    target, fast = self.target, self.fast
                    deadline, duration = frame[1], frame[3]
                    self.rendering_frame = frame
                    self.rendering_bytes, self.rendering_originals = _render_memory(target, frame[2])
                try:
                    output = self.render(frame[2], target, fast)
                except Exception:
                    log.error('Could not render animated WebP poster' if fallback else 'Could not play animated WebP', exc_info=True)
                    if fallback:
                        return
                    self.fail()
                    rendered = None
                    with self.condition:
                        frame = self.rendering_frame = None
                        self.rendering_bytes = self.rendering_originals = 0
                        self.condition.notify_all()
                    continue
                with self.condition:
                    if not self.stopped and key[1] == self.revision and fallback == self.failed and (fallback or key[0] >= self.presented):
                        self.ready = (key, output, (key[0], 0.0 if fallback else deadline, None, duration))
                    output = None
                    frame = self.rendering_frame = None
                    self.rendering_originals = 0
                    self.rendering_bytes = 0
                    rendered = (*key, fallback) if fallback == self.failed else None
                    self.condition.notify_all()
        finally:
            with self.condition:
                self.rendering_bytes = self.rendering_originals = 0
                frame = self.rendering_frame = None
                self.condition.notify_all()

    def run(self, order: Any) -> None:
        decoder: Any = None
        try:
            decoder = image_tools._open_pil(self.animation.source)
            decoder.load()
            decoder_loop = 0
            while True:
                with self.condition:
                    if self.stopped or self.failed:
                        return
                    # The current frame is separate from the forward duration.
                    # Stop at the first frame covering the target, or the byte cap.
                    decode = not self.finished and self.source_count() < self.capacity and sum(item[3] for item in self.frames[1:]) < LOOKAHEAD
                    if not decode:
                        self.condition.wait()
                        continue
                    serial, deadline = self.next_frame
                    self.decoding = True
                loop, index = divmod(serial, self.animation.frames)
                if self.animation.loops and loop >= self.animation.loops:
                    with self.condition:
                        self.finished = True
                        self.decoding = False
                        self.condition.notify_all()
                    if decoder is not None:
                        decoder.close()
                        decoder = None
                    continue
                if decoder is None or loop != decoder_loop:
                    if decoder is not None:
                        decoder.close()
                    decoder = image_tools._open_pil(self.animation.source)
                    decoder_loop = loop
                decoder.seek(index)
                decoder.load()
                duration = max(1, decoder.info.get('duration', 100)) / 1000
                pixels = self.capture(decoder)
                with self.condition:
                    # A budget change can trim unseen frames while capture is in
                    # flight. Retry from the saved cursor instead of skipping them.
                    self.decoding = False
                    if not self.stopped and not self.failed and self.next_frame == (serial, deadline) and self.source_count() < self.capacity:
                        self.frames.append((serial, deadline, pixels, duration))
                        self.next_frame = (serial + 1, deadline + duration)
                    pixels = None
                    self.condition.notify_all()
        except Exception:
            log.error('Could not play animated WebP', exc_info=True)
            self.fail()
        finally:
            if decoder is not None:
                decoder.close()
            with self.condition:
                self.decoding = False
                self.condition.notify_all()


class WebPPlayback:
    def __init__(self, images: Any) -> None:
        self.images = images
        self.pages: Dict[int, _Page] = {}
        self.timer: Optional[int] = None
        self.limited = False

    @callback.Callback
    def notice_changed(self, limited: bool) -> None:
        pass

    def _set_limited(self, limited: bool) -> None:
        if limited != self.limited:
            self.limited = limited
            self.notice_changed(limited)

    def clear(self) -> None:
        if self.timer is not None:
            GLib.source_remove(self.timer)
            self.timer = None
        for page in self.pages.values():
            page.close()
        self.pages.clear()
        self._set_limited(False)

    def configure(self, specifications: List[Tuple[int, Any, Any, tuple]]) -> None:
        if prefs['animation mode'] == constants.ANIMATION_DISABLED:
            specifications = []
        wanted = {slot: identity for slot, identity, poster, target in specifications}
        for slot, old_page in list(self.pages.items()):
            if wanted.get(slot) != old_page.identity:
                old_page.close()
                del self.pages[slot]
        budget = int(cast(float, prefs['animation cache limit']) * 1024 * 1024)
        share = budget // max(1, len(specifications))
        now = time.monotonic()
        limited = False
        created_pages = []
        for slot, identity, poster, target in specifications:
            original = poster.get_rowstride() * poster.get_height()
            previous = self.pages.get(slot)
            with previous.condition if previous is not None else nullcontext():
                resized = target[0][0] * target[0][1] * 4
                display_bytes = resized
                pending_bytes, originals = _render_memory(target, poster)
                # Poster, decoder canvas and capture/export scratch can coexist
                # with the render worker's sources and output scratch.
                originals += 3
                # Pillow snapshots avoid full-size byte exports, but their resized
                # export needs additional scratch. Keep the original pixbuf path
                # for pages starting with a budget too small for that scratch.
                native_size = (poster.get_width(), poster.get_height())
                if tools.rotation_swaps_axes(target[1]):
                    native_size = tuple(reversed(native_size))
                pillow_pending = max(pending_bytes, _pillow_render_memory(target, (poster.get_width(), poster.get_height())))
                pillow_capacity = (share - display_bytes - pillow_pending) // original - originals
                pillow_frames = previous.pillow_frames if previous is not None else poster.get_has_alpha() and tuple(target[0]) != native_size and pillow_capacity >= 2
                if pillow_frames:
                    pending_bytes = pillow_pending
                    if target[5] not in (GdkPixbuf.InterpType.BILINEAR, image_tools.SCALING_LANCZOS):
                        originals = max(originals, 5)
                if previous is not None:
                    originals = max(originals, 3 + previous.rendering_originals)
                    pending_bytes = max(pending_bytes, previous.rendering_bytes)
                    if previous.display is not None:
                        output = previous.display
                        display_bytes = max(display_bytes, output.get_rowstride() * output.get_height())
                    if previous.ready is not None:
                        output = previous.ready[1]
                        pending_bytes = max(pending_bytes, output.get_rowstride() * output.get_height())
                    output = None
                # Pending is dropped before rendering; transforms reserve their scratch space.
                # Opaque Lanczos also converts the render source to Pillow.
                capacity = (share - display_bytes - pending_bytes) // original - originals
                retired = None
                created = False
                if capacity < 2:
                    limited = True
                    retired = self.pages.pop(slot, None)
                else:
                    page = self.pages.get(slot)
                    created = page is None
                    if page is None:
                        page = _Page(identity, poster)
                        page.pillow_frames = pillow_frames
                        self.pages[slot] = page
                    with page.condition:
                        if target != page.target:
                            page.fast = bool(page.target and target[0] != page.target[0])
                            page.settle = now + RESIZE_DELAY if page.fast else now
                            page.target = target
                            page.revision += 1
                            page.ready = None
                        page.capacity = capacity
                        slots = capacity - int(page.decoding)
                        page.trim_frames(slots)
                        # A resized source may have left the queue while still pinned
                        # by the worker. Its pixels occupy one of the original slots.
                        if page.rendering_frame is not None and all(item is not page.rendering_frame for item in page.frames):
                            page.trim_frames(max(0, slots - 1))
                        page.condition.notify_all()
            if retired is not None:
                retired.close()
            if created and page is not None:
                created_pages.append(page)
        # Retained queues must release their old share before new workers decode.
        for page in created_pages:
            page.renderer.append_order(None)
            page.worker.append_order(None)
        self._set_limited(limited)
        if self.timer is not None:
            GLib.source_remove(self.timer)
            self.timer = None
        if self.pages:
            self.timer = GLib.timeout_add(10, self.tick)

    def displayed(self, slot: int) -> Any:
        page = self.pages.get(slot)
        return page.display if page is not None and not page.failed else None

    def tick(self) -> bool:
        if self.timer is not None:
            GLib.source_remove(self.timer)
            self.timer = None
        if prefs['animation mode'] == constants.ANIMATION_DISABLED:
            self.clear()
            return False
        now = time.monotonic()
        delays = []
        for slot, page in self.pages.items():
            with page.condition:
                if page.fast and now >= page.settle:
                    page.fast = False
                    page.revision += 1
                    page.ready = None
                if page.ready is not None:
                    key, output, prepared = page.ready
                    if key[1] != page.revision or (not page.failed and key[0] < page.presented):
                        page.ready = None
                    elif page.failed or key[0] == page.presented or now >= page.next_due:
                        page.display = output
                        page.display_revision = key[1]
                        if key[0] != page.presented:
                            # Each new frame gets its full encoded duration.
                            # Late work shifts playback rather than causing a burst.
                            page.next_due = now + prepared[3]
                        page.presented = key[0]
                        self.images[slot].set_from_pixbuf(output)
                        page.ready = None
                        while len(page.frames) > 1 and page.frames[1][0] <= page.presented:
                            page.frames.pop(0)
                page.condition.notify_all()
                # Wait directly for the encoded hold to end, instead of adding
                # up polling delays on every frame. Poll only overdue work.
                delay = max(1, math.ceil((page.next_due - now) * 1000)) if page.next_due > now else 10
                if page.display_revision != page.revision:
                    delay = min(delay, 10)
                if page.fast:
                    delay = min(delay, max(1, math.ceil((page.settle - now) * 1000)))
                delays.append(delay)
        if delays:
            self.timer = GLib.timeout_add(min(delays), self.tick)
        return False
