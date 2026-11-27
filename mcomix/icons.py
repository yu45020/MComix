"""icons.py - Load MComix specific icons."""

from gi.repository import Gdk, Gtk
import os
import pkgutil

from mcomix import image_tools
from mcomix import images
from mcomix import log
from mcomix.i18n import _


def mcomix_icons():
    """ Returns a list of differently sized pixbufs for the
    application icon. """

    sizes = ('16', '32', '48', '256')
    pixbufs = [
        image_tools.load_pixbuf(
            pkgutil.get_data('mcomix', f'images/mcomix-{size}.png')
        ) for size in sizes
    ]

    return pixbufs


def load_icons() -> None:
    _icons = (('gimp-flip-horizontal.png',   'mcomix-flip-horizontal'),
              ('gimp-flip-vertical.png',     'mcomix-flip-vertical'),
              ('gimp-rotate-180.png',        'mcomix-rotate-180'),
              ('gimp-rotate-270.png',        'mcomix-rotate-270'),
              ('gimp-rotate-90.png',         'mcomix-rotate-90'),
              ('gimp-transform.png',         'mcomix-transform'),
              ('tango-enhance-image.png',    'mcomix-enhance-image'),
              ('tango-add-bookmark.png',     'mcomix-add-bookmark'),
              ('tango-archive.png',          'mcomix-archive'),
              ('tango-image.png',            'mcomix-image'),
              ('library.png',                'mcomix-library'),
              ('comments.png',               'mcomix-comments'),
              ('zoom.png',                   'mcomix-zoom'),
              ('magnifyingglass.png',        'mcomix-lens'),
              ('double-page.png',            'mcomix-double-page'),
              ('manga.png',                  'mcomix-manga'),
              ('fitbest.png',                'mcomix-fitbest'),
              ('fitwidth.png',               'mcomix-fitwidth'),
              ('fitheight.png',              'mcomix-fitheight'),
              ('fitmanual.png',              'mcomix-fitmanual'),
              ('fitsize.png',                'mcomix-fitsize'))

    # Load window title icons.
    pixbufs = mcomix_icons()
    Gtk.Window.set_default_icon_list(pixbufs)
    # Load application icons.
    factory = Gtk.IconFactory()
    for filename, stockid in _icons:
        try:
            icon_data = pkgutil.get_data('mcomix', 'images/%s' % filename)
            pixbuf = image_tools.load_pixbuf(icon_data)
            iconset = Gtk.IconSet(pixbuf)
            factory.add(stockid, iconset)
        except Exception:
            log.warning(_('! Could not load icon "%s"'), filename)
    factory.add_default()
    _enlarge_titlebar_buttons()


# GTK draws the title bar itself (client-side decorations) with 16 px bitmap
# minimize/maximize/close icons, which are hard to see. GTK 3 CSS can only
# scale that bitmap (blurry), not pick a larger icon, so the stock icon is
# hidden and the image's background draws a vector icon at full size.
_TITLEBAR_CSS = b'''
button.titlebutton { min-width: 40px; min-height: 40px; }
button.titlebutton image {
    min-width: 32px; min-height: 32px;
    -gtk-icon-transform: scale(0);
    background-repeat: no-repeat;
}
button.titlebutton.minimize image {
    background-image: -gtk-icontheme('mcomix-window-minimize-symbolic');
}
button.titlebutton.maximize image {
    background-image: -gtk-icontheme('mcomix-window-maximize-symbolic');
}
.maximized button.titlebutton.maximize image {
    background-image: -gtk-icontheme('mcomix-window-restore-symbolic');
}
button.titlebutton.close image {
    background-image: -gtk-icontheme('mcomix-window-close-symbolic');
}
'''


def _enlarge_titlebar_buttons() -> None:
    Gtk.IconTheme.get_default().append_search_path(
        os.path.join(os.path.dirname(images.__file__), 'titlebar'))
    provider = Gtk.CssProvider()
    provider.load_from_data(_TITLEBAR_CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


# vim: expandtab:sw=4:ts=4
