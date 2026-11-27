"""cursor_handler.py - Cursor handler."""

from gi.repository import Gdk, GLib

from mcomix import constants

class CursorHandler(object):

    def __init__(self, window):
        self._window = window
        self._timer_id = None
        self._current_cursor = constants.NORMAL_CURSOR

    def set_cursor_type(self, cursor):
        """Set the cursor to type <cursor>, one of the cursor constants."""
        if cursor == constants.NORMAL_CURSOR:
            mode = None
        elif cursor == constants.GRAB_CURSOR:
            mode = Gdk.Cursor.new(Gdk.CursorType.FLEUR)
        else:
            mode = self._get_hidden_cursor()

        self._window.set_cursor(mode)

        self._current_cursor = cursor

        if cursor == constants.NORMAL_CURSOR:
            self._set_hide_timer()
        else:
            self._kill_timer()

    def auto_hide_on(self):
        """Start hiding the normal cursor after a timeout."""
        if self._current_cursor == constants.NORMAL_CURSOR:
            self._set_hide_timer()

    def refresh(self):
        """Refresh the current cursor (i.e. display it and set a new hide
        timer). Used when we move the cursor.
        """
        self.set_cursor_type(self._current_cursor)

    def _on_timeout(self):
        mode = self._get_hidden_cursor()
        self._window.set_cursor(mode)
        self._timer_id = None
        return False

    def _set_hide_timer(self):
        self._kill_timer()
        self._timer_id = GLib.timeout_add(2000, self._on_timeout)

    def _kill_timer(self):
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None

    def _get_hidden_cursor(self):
        return Gdk.Cursor.new(Gdk.CursorType.BLANK_CURSOR)


# vim: expandtab:sw=4:ts=4
