""" Gtk.IconView subclass for dynamically generated thumbnails. """

from gi.repository import Gtk, GLib

from mcomix.preferences import prefs
from mcomix.worker_thread import WorkerThread


class ThumbnailViewBase(object):
    """ This class provides shared functionality for Gtk.TreeView and
    Gtk.IconView. Instantiating this class directly is *impossible*,
    as it depends on methods provided by the view classes. """

    def __init__(self, uid_column, pixbuf_column, status_column):
        """ Constructs a new ThumbnailView.
        @param uid_column: index of unique identifer column.
        @param pixbuf_column: index of pixbuf column.
        @param status_column: index of status boolean column
                              (True if pixbuf is not temporary filler)
        """

        #: Keep track of already generated thumbnails.
        self._uid_column = uid_column
        self._pixbuf_column = pixbuf_column
        self._status_column = status_column

        #: Ignore updates when this flag is True.
        self._updates_stopped = True
        self._update_generation = 0
        self._thumbnail_rows = {}
        self.connect('notify::model', self._model_changed)
        self.connect('destroy', self._destroy_view)
        #: Worker thread
        self._thread = WorkerThread(self._pixbuf_worker,
                                    name='thumbview',
                                    unique_orders=True,
                                    max_threads=prefs["max threads"])

    def stop_update(self):
        """ Stops generation of pixbufs. """
        self._updates_stopped = True
        self._update_generation += 1
        self._thread.stop()
        self._thumbnail_rows.clear()

    def _model_changed(self, widget: Gtk.Widget, parameter: object) -> None:
        """Fence jobs from a detached model without waiting for decoding."""
        self._updates_stopped = True
        self._update_generation += 1
        self._thread.clear_orders()
        self._thumbnail_rows.clear()

    def _destroy_view(self, widget: Gtk.Widget) -> None:
        self.stop_update()

    def draw_thumbnails_on_screen(self, *args):
        """ Prepares valid thumbnails for currently displayed icons.
        This method is supposed to be called from the expose-event
        callback function. """

        model = self.get_model()
        if model is None:
            return
        visible = self.get_visible_range()
        if not visible:
            # No valid paths available
            return

        pixbufs_needed = []
        start = visible[0][0]
        end = visible[1][0]
        # Read ahead/back and start caching a few more icons. Currently invisible
        # icons are always cached only after the visible icons have been completed.
        additional = (end - start) // 2
        required = list(range(start, end + additional + 1)) + \
                   list(range(max(0, start - additional), start))
        # Filter invalid paths.
        required = [path for path in required if 0 <= path < len(model)]
        with self._thread:
            # Flush current pixmap generation orders.
            self._thread.clear_orders()
            queued = set()
            for path in required:
                iter = model.get_iter(path)
                uid, generated = model.get(iter,
                                           self._uid_column,
                                           self._status_column)
                # Do not queue again if thumbnail was already created.
                if not generated:
                    pixbufs_needed.append(self._thumbnail_order(model, iter, uid))
                    queued.add(uid)
            # Discarded orders never finish: keep row references for this
            # window only. A dropped running order still updates its row.
            self._thumbnail_rows = {uid: entry for uid, entry
                                    in self._thumbnail_rows.items() if uid in queued}
            if len(pixbufs_needed) > 0:
                self._updates_stopped = False
                self._thread.extend_orders(pixbufs_needed)

    def _thumbnail_order(self, model: Gtk.TreeModel, iterator: Gtk.TreeIter,
                         uid: object) -> tuple:
        """Reuse a pending row's identity, but never one from a removed row."""
        path = model.get_path(iterator)
        previous = self._thumbnail_rows.get(uid)
        if previous is None or previous[0].get_path() != path:
            previous = (Gtk.TreeRowReference.new(model, path), object())
            self._thumbnail_rows[uid] = previous
        row, token = previous
        return ((self._update_generation, uid, token), model, row)

    def _pixbuf_worker(self, order):
        """ Run by a worker thread to generate the thumbnail for a path."""
        (generation, uid, token), model, row = order
        pixbuf = None
        try:
            pixbuf = self.generate_thumbnail(uid)
        finally:
            # Also release the row's bookkeeping when generation fails.
            GLib.idle_add(self._pixbuf_finished, generation, uid,
                          model, row, pixbuf, token)

    def _pixbuf_finished(self, generation: int, uid: object,
                         model: Gtk.TreeModel, row: Gtk.TreeRowReference,
                         pixbuf: object, token: object) -> int:
        """Resolve the queued row only on the GTK thread, if it still exists."""
        pending = self._thumbnail_rows.get(uid)
        if pending is not None and pending[1] is token:
            del self._thumbnail_rows[uid]
        if self._updates_stopped:
            return 0
        if generation != self._update_generation or model != self.get_model():
            return 0

        path = row.get_path()
        if path is None:
            # A replacement needs its own request, even if it reused this UID.
            self.queue_draw()
            return 0
        iterator = model.get_iter(path)
        if model.get_value(iterator, self._uid_column) != uid:
            self.queue_draw()
            return 0
        if pixbuf is not None:
            model.set(iterator, self._status_column, True,
                      self._pixbuf_column, pixbuf)
        return 0

class ThumbnailIconView(Gtk.IconView, ThumbnailViewBase):
    def __init__(self, model, uid_column, pixbuf_column, status_column):
        assert 0 != (model.get_flags() & Gtk.TreeModelFlags.ITERS_PERSIST)
        super(ThumbnailIconView, self).__init__(model)
        ThumbnailViewBase.__init__(self, uid_column, pixbuf_column, status_column)
        self.set_pixbuf_column(pixbuf_column)

        # Connect events
        self.connect('draw', self.draw_thumbnails_on_screen)

class ThumbnailTreeView(Gtk.TreeView, ThumbnailViewBase):
    def __init__(self, model, uid_column, pixbuf_column, status_column):
        assert 0 != (model.get_flags() & Gtk.TreeModelFlags.ITERS_PERSIST)
        super(ThumbnailTreeView, self).__init__(model)
        ThumbnailViewBase.__init__(self, uid_column, pixbuf_column, status_column)

        # Connect events
        self.connect('draw', self.draw_thumbnails_on_screen)

# vim: expandtab:sw=4:ts=4
