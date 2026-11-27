# -*- coding: utf-8 -*-

import threading
from typing import Any, Optional

from gi.repository import GLib, Gtk

from mcomix import message_dialog
from mcomix.i18n import _


_active_dialogs = []


def _cancel_dialog(dialog: Gtk.Dialog) -> bool:
    if any(item[1] is dialog for item in _active_dialogs):
        dialog.response(Gtk.ResponseType.CANCEL)
    return False


def cancel_password(owner: Any) -> None:
    """Dismiss only this owner's requests, including same-path reopenings."""
    for request_owner, dialog in _active_dialogs[:]:
        if request_owner is owner:
            if threading.current_thread() is threading.main_thread():
                _cancel_dialog(dialog)
            else:
                GLib.idle_add(_cancel_dialog, dialog)


def ask_for_password(owner: Any) -> Optional[str]:
    """ Openes an input dialog to ask for the password of the archive
    <owner>. Returns either an Unicode string (the password), or None."""
    dialog = message_dialog.MessageDialog(None, Gtk.DialogFlags.MODAL,
            Gtk.MessageType.QUESTION, Gtk.ButtonsType.OK_CANCEL)
    dialog.set_text(
        _("The archive is password-protected:"),
        owner.archive + '\n\n' +
        _("Please enter the password to continue:"))
    dialog.set_default_response(Gtk.ResponseType.OK)
    dialog.set_auto_destroy(False)

    password_box = Gtk.Entry()
    password_box.set_visibility(False)
    password_box.set_activates_default(True)
    dialog.get_content_area().pack_end(password_box, True, True, 0)
    dialog.set_focus(password_box)

    entry = (owner, dialog)
    _active_dialogs.append(entry)
    try:
        if owner._cancelled.is_set():
            return None
        result = dialog.run()
        password = password_box.get_text()
    finally:
        _active_dialogs.remove(entry)
        dialog.destroy()

    if result == Gtk.ResponseType.OK and password:
        return password
    else:
        return None

# vim: expandtab:sw=4:ts=4
