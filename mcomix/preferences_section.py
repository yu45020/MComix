"""preferences_section.py - Preference dialog section."""

from gi.repository import Gtk

from mcomix import labels

class _PreferenceSection(Gtk.Box):

    """The _PreferenceSection is a convenience class for making one
    "section" of a preference-style dialog, e.g. it has a bold header
    and a number of rows which are indented with respect to that header.
    """

    def __init__(self, header):
        """Contruct a new section with the header set to the text in
        <header>.
        """
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.contentbox = Gtk.Box.new(Gtk.Orientation.VERTICAL, 6)
        self.contentbox.set_margin_start(9)
        label = labels.BoldLabel(header)
        label.set_alignment(0, 0.5)
        self.pack_start(label, False, False, 0)
        self.pack_start(self.contentbox, True, True, 0)

    def new_split_vboxes(self):
        """Return two new VBoxes that are automatically put in the section
        after the previously added items.
        """
        left_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 6)
        right_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 6)

        hbox = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
        hbox.pack_start(left_box, True, True, 0)
        hbox.pack_start(right_box, False, False, 0)
        self.contentbox.pack_start(hbox, True, True, 0)
        return left_box, right_box

# vim: expandtab:sw=4:ts=4
