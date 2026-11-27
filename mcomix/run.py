
import os
import sys
import optparse
import signal

if __name__ == '__main__':
    print('PROGRAM TERMINATED', file=sys.stderr)
    print('Please do not run this script directly! Use the mcomix script or mcomixstarter.py instead.', file=sys.stderr)
    sys.exit(1)

# These modules must not depend on GTK, Pillow,
# or any other optional libraries.
from mcomix import (
    constants,
    log,
    portability,
    preferences,
)

def wait_and_exit() -> None:
    """Keep dependency errors visible when started in an interactive console."""
    try:
        if sys.platform == 'win32' and sys.stdin is not None and \
                sys.stdout is not None and not sys.stdin.closed and \
                not sys.stdout.closed and sys.stdin.isatty() and \
                sys.stdout.isatty():
            print()
            input("Press ENTER to continue...")
    except (EOFError, OSError):
        pass
    sys.exit(1)

def print_version(opt, value, parser, *args, **kwargs):
    """Print the version number and exit."""
    print(constants.APPNAME + ' ' + constants.VERSION)
    sys.exit(0)

def parse_arguments(argv):
    """ Parse the command line passed in <argv>. Returns a tuple containing
    (options, arguments). Errors parsing the command line are handled in
    this function. """
    from mcomix.i18n import _

    parser = optparse.OptionParser(
            usage="%%prog %s" % _('[OPTION...] [PATH]'),
            description=_('View images and comic book archives.'),
            add_help_option=False)
    parser.add_option('--help', action='help',
            help=_('Show this help and exit.'))
    parser.add_option('-s', '--slideshow', dest='slideshow', action='store_true',
            help=_('Start the application in slideshow mode.'))
    parser.add_option('-l', '--library', dest='library', action='store_true',
            help=_('Show the library on startup.'))
    parser.add_option('-v', '--version', action='callback', callback=print_version,
            help=_('Show the version number and exit.'))
    parser.add_option('--lang', dest='language_code', action='store',
            help=_('Temporarily override the interface language.'))
    parser.add_option('-e', '--entry', dest='entry', action='store', default='',
            metavar=_('NAME'),
            help=_('Open PATH at the page with this name inside the archive '
                   '(folders separated by /), or this file in the folder.'))
    parser.add_option('-p', '--page', dest='page', action='store', type='int',
            metavar='N',
            help=_('Open PATH at page N, when --entry is not given or names no page.'))

    viewmodes = optparse.OptionGroup(parser, _('View modes'))
    viewmodes.add_option('-f', '--fullscreen', dest='fullscreen', action='store_true',
            help=_('Start the application in fullscreen mode.'))
    viewmodes.add_option('-m', '--manga', dest='manga', action='store_true',
            help=_('Start the application in manga mode.'))
    viewmodes.add_option('-d', '--double-page', dest='doublepage', action='store_true',
            help=_('Start the application in double page mode.'))
    parser.add_option_group(viewmodes)

    fitmodes = optparse.OptionGroup(parser, _('Zoom modes'))
    fitmodes.add_option('-b', '--zoom-best', dest='zoommode', action='store_const',
            const=constants.ZoomMode.BEST,
            help=_('Start the application with zoom set to best fit mode.'))
    fitmodes.add_option('-w', '--zoom-width', dest='zoommode', action='store_const',
            const=constants.ZoomMode.WIDTH,
            help=_('Start the application with zoom set to fit width.'))
    fitmodes.add_option('-h', '--zoom-height', dest='zoommode', action='store_const',
            const=constants.ZoomMode.HEIGHT,
            help=_('Start the application with zoom set to fit height.'))
    parser.add_option_group(fitmodes)

    debugopts = optparse.OptionGroup(parser, _('Debug options'))
    debugopts.add_option('-W', dest='loglevel', action='store',
            choices=('all', 'debug', 'info', 'warn', 'error'), default='warn',
            metavar='[ all | debug | info | warn | error ]',
            help=_('Sets the desired output log level.'))
    parser.add_option_group(debugopts)

    opts, args = parser.parse_args(argv)
    if opts.page is not None and opts.page < 1:
        parser.error(_('--page needs a page number of 1 or more.'))
    if (opts.page is not None or opts.entry) and not args:
        parser.error(_('--page and --entry need a PATH.'))

    # Fix up log level to use constants from log.
    if opts.loglevel == 'all':
        opts.loglevel = log.DEBUG
    if opts.loglevel == 'debug':
        opts.loglevel = log.DEBUG
    if opts.loglevel == 'info':
        opts.loglevel = log.INFO
    elif opts.loglevel == 'warn':
        opts.loglevel = log.WARNING
    elif opts.loglevel == 'error':
        opts.loglevel = log.ERROR

    return opts, args

def setup_dependencies():
    """Check for PyGTK and PIL dependencies."""
    from mcomix.i18n import _

    try:
        from gi import require_version

        require_version('PangoCairo', '1.0')
        require_version('Gtk', '3.0')
        require_version('Gdk', '3.0')

        from gi.repository import Gdk, GLib, Gtk  # noqa

    except ValueError:
        log.error(_("You do not have the required versions of GTK+ 3.0 and PyGObject installed."))
        wait_and_exit()

    except ImportError:
        log.error(_('No version of GObject was found on your system.'))
        log.error(_('This error might be caused by missing GTK+ libraries.'))
        wait_and_exit()

    try:
        import PIL.Image  # noqa
    except ImportError:
        log.error(_('Python Imaging Library Fork (Pillow) 6.0.0 or higher is required.'))
        log.error(_('No version of the Python Imaging Library was found on your system.'))
        wait_and_exit()


def run():
    """Run the program."""

    # Load configuration and setup localisation.
    preferences.read_preferences_file()
    from mcomix import i18n
    i18n.install_gettext()

    # Retrieve and parse command line arguments.
    argv = sys.argv[1:]
    opts, args = parse_arguments(argv)

    # First things first: set the log level.
    log.setLevel(opts.loglevel)

    # Reconfigure stdout to replace characters that cannot be printed
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')

    if opts.language_code:
        i18n.install_gettext(opts.language_code)

    setup_dependencies()

    from gi.repository import Gdk, GLib, Gtk

    if not os.path.exists(constants.DATA_DIR):
        os.makedirs(constants.DATA_DIR, 0o700)

    if not os.path.exists(constants.CONFIG_DIR):
        os.makedirs(constants.CONFIG_DIR, 0o700)

    from mcomix import icons
    icons.load_icons()

    open_path = None
    if len(args) == 1:
        open_path = args[0]
    elif len(args) > 1:
        open_path = args

    # Some languages require a RTL layout
    if preferences.prefs['language'] in ('he', 'fa'):
        Gtk.widget_set_default_direction(Gtk.TextDirection.RTL)

    Gdk.set_program_class(constants.APPNAME)
    GLib.set_prgname(constants.APPNAME)

    settings = Gtk.Settings.get_default()
    if settings:
        # Enable icons for menu items.
        settings.props.gtk_menu_images = True

        # Prefer dark theme if system theme mode is set to dark
        if portability.is_system_ui_dark_themed() == constants.SystemThemeLightness.DARK:
            settings.set_property('gtk-application-prefer-dark-theme', True)

    from mcomix import main
    window = main.MainWindow(fullscreen = opts.fullscreen, is_slideshow = opts.slideshow,
            show_library = opts.library, manga_mode = opts.manga,
            double_page = opts.doublepage, zoom_mode = opts.zoommode,
            open_path = open_path, open_page = opts.page or 0,
            open_entry = opts.entry)
    main.set_main_window(window)

    if 'win32' != sys.platform:
        # Add a SIGCHLD handler to reap zombie processes.
        def on_sigchld(signum, frame):
            try:
                os.waitpid(-1, os.WNOHANG)
            except OSError:
                pass
        signal.signal(signal.SIGCHLD, on_sigchld)

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda signum, stack: GLib.idle_add(window.terminate_program))
    try:
        Gtk.main()
    except KeyboardInterrupt: # Will not always work because of threading.
        window.terminate_program()

# vim: expandtab:sw=4:ts=4
