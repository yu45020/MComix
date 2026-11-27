# -*- coding: utf-8 -*-

"""preferences_dialog.py - Preferences dialog."""

import operator
import os
import threading
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, GObject

from mcomix.preferences import prefs
from mcomix import preferences_page
from mcomix import image_tools
from mcomix import constants
from mcomix import message_dialog
from mcomix import keybindings
from mcomix import keybindings_editor
from mcomix import upscale
from mcomix.i18n import _

_dialog = None

class _PreferencesDialog(Gtk.Dialog):

    """The preferences dialog where most (but not all) settings that are
    saved between sessions are presented to the user.
    """

    def __init__(self, window):
        super(_PreferencesDialog, self).__init__(_('Preferences'), window)

        # Button text is set later depending on active tab
        self.reset_button = self.add_button('', constants.RESPONSE_REVERT_TO_DEFAULT)
        self.add_button(Gtk.STOCK_CLOSE, Gtk.ResponseType.CLOSE)

        self._window = window
        self.set_resizable(True)
        self.set_default_response(Gtk.ResponseType.CLOSE)

        self.connect('response', self._response)

        notebook = self.notebook = Gtk.Notebook()
        self.vbox.pack_start(notebook, True, True, 0)
        self.set_border_width(4)
        notebook.set_border_width(6)

        page_inits = (
            (_('Appearance'), self._init_appearance_tab),
            (_('Behaviour'), self._init_behaviour_tab),
            (_('Display'), self._init_display_tab),
            (_('Advanced'), self._init_advanced_tab),
        )

        for title, page_init in page_inits:
            container = Gtk.ScrolledWindow()
            container.set_policy(
                Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            container.set_min_content_height(400)
            container.set_propagate_natural_height(True)
            container.set_overlay_scrolling(False)
            page = page_init()
            container.add(page)
            notebook.append_page(container, Gtk.Label(label=title))

        # Shortcuts is already a ScrolledWindow
        self.shortcuts = self._init_shortcuts_tab()
        notebook.append_page(
            self.shortcuts, Gtk.Label(label=_('Shortcuts')))

        notebook.connect('switch-page', self._tab_page_changed)
        # Update the Reset button's tooltip
        self._tab_page_changed(notebook, None, 0)

        self.show_all()

    def _init_appearance_tab(self):
        # ----------------------------------------------------------------
        # The "Appearance" tab.
        # ----------------------------------------------------------------
        page = preferences_page._PreferencePage()

        page.new_section(_('User interface'))

        page.add_row(Gtk.Label(label=_('Language (needs restart):')),
            self._create_language_control())

        page.add_row(self._create_pref_check_button(
            _('Escape key closes program'), 'escape quits',
            _('When active, the ESC key closes the program, instead of only '
              'disabling fullscreen mode.')))

        page.new_section(_('Background'))

        fixed_bg_button, dynamic_bg_button = self._create_binary_pref_radio_buttons(
            _('Use this colour as background:'),
            'color box bg',
            _('Always use this selected colour as the background colour.'),
            _('Use dynamic background colour'),
            'smart bg',
            _('Automatically pick a background colour that fits the viewed image.'))
        page.add_row(fixed_bg_button, self._create_color_button('bg colour'))
        page.add_row(dynamic_bg_button)

        page.new_section(_('Thumbnails'))

        thumb_fixed_bg_button, thumb_dynamic_bg_button = self._create_binary_pref_radio_buttons(
            _('Use this colour as the thumbnail background:'),
            'color box thumb bg',
            _('Always use this selected colour as the thumbnail background colour.'),
            _('Use dynamic background colour'),
            'smart thumb bg',
            _('Automatically use the colour that fits the viewed image for the thumbnail background.'))
        page.add_row(thumb_fixed_bg_button, self._create_color_button('thumb bg colour'))
        page.add_row(thumb_dynamic_bg_button)

        page.add_row(self._create_pref_check_button(
            _('Show page numbers on thumbnails'),
            'show page numbers on thumbnails', None))

        page.add_row(self._create_pref_check_button(
            _('Use archive thumbnail as application icon'),
            'archive thumbnail as icon',
            _('By enabling this setting, the first page of a book will be used as application icon instead of the standard icon.')))

        page.add_row(Gtk.Label(label=_('Thumbnail size (in pixels):')),
            self._create_pref_spinner('thumbnail size',
            1, 20, 500, 1, 10, 0, None))

        page.new_section(_('Transparency'))

        page.add_row(self._create_pref_check_button(
            _('Use checkered background for transparent images'),
            'checkered bg for transparent images',
            _('Use a grey checkered background for transparent images. If this preference is unset, the background is plain white instead.')))

        return page

    def _init_behaviour_tab(self):
        # ----------------------------------------------------------------
        # The "Behaviour" tab.
        # ----------------------------------------------------------------
        page = preferences_page._PreferencePage()

        page.new_section(_('Scroll'))

        page.add_row(self._create_pref_check_button(
            _('Use smart scrolling'),
            'smart scroll',
            _('With this preference set, the space key and mouse wheel '
              'do not only scroll down or up, but also sideways and so '
              'try to follow the natural reading order of the comic book.')))

        page.add_row(self._create_pref_check_button(
            _('Flip pages when scrolling off the edges of the page'),
            'flip with wheel',
            _('Flip pages when scrolling "off the page" with the scroll wheel or with the arrow keys. It takes n consecutive "steps" with the scroll wheel or the arrow keys for the pages to be flipped.')))

        page.add_row(self._create_pref_check_button(
            _('Automatically open the next archive'),
            'auto open next archive',
            _('Automatically open the next archive in the directory when flipping past the last page, or the previous archive when flipping past the first page.')))

        page.add_row(self._create_pref_check_button(
            _('Automatically open next directory'),
            'auto open next directory',
            _('Automatically open the first file in the next sibling directory when flipping past the last page of the last file in a directory, or the previous directory when flipping past the first page of the first file.')))

        page.add_row(self._create_pref_check_button(
            _('Open first file when navigating to previous archive'),
            'open first file in prev archive',
            _('Automatically open the first file of the previous archive when navigating to it, instead of opening the last file of the previous archive.')))

        page.add_row(self._create_pref_check_button(
            _('Open first file when navigating to previous directory'),
            'open first file in prev directory',
            _('Automatically open the first file of the previous directory when navigating to it, instead of opening the last file of the previous directory.')))

        page.add_row(Gtk.Label(label=_('Number of pixels to scroll per arrow key press:')),
            self._create_pref_spinner('number of pixels to scroll per key event',
            1, 1, 500, 1, 3, 0,
            _('Set the number of pixels to scroll on a page when using the arrow keys.')))

        page.add_row(Gtk.Label(label=_('Number of pixels to scroll per mouse wheel turn:')),
            self._create_pref_spinner('number of pixels to scroll per mouse wheel event',
            1, 1, 500, 1, 3, 0,
            _('Set the number of pixels to scroll on a page when using a mouse wheel.')))

        page.add_row(Gtk.Label(label=_('Fraction of page to scroll '
            'per space key press (in percent):')),
            self._create_pref_spinner('smart scroll percentage',
            0.01, 1, 100, 1, 5, 0,
            _('Sets the percentage by which the page '
            'will be scrolled down or up when the space key is pressed.')))

        page.add_row(Gtk.Label(label=_('Number of "steps" to take before flipping the page:')),
            self._create_pref_spinner('number of key presses before page turn',
            1, 1, 100, 1, 3, 0,
            _('Set the number of "steps" needed to flip to the next or previous page.  Less steps will allow for very fast page turning but you might find yourself accidentally turning pages.')))

        page.new_section(_('Double page mode'))

        page.add_row(self._create_pref_check_button(
            _('Flip two pages in double page mode'),
            'double step in double page mode',
            _('Flip two pages, instead of one, each time we flip pages in double page mode.')))

        page.add_row(Gtk.Label(label=_('Show only one page where appropriate:')),
            self._create_doublepage_as_one_control())

        page.add_row(Gtk.Label(_('Page auto-resizing:')),
            self._create_double_page_autoresize_control())

        page.add_row(Gtk.Label(label=_('Space between two pages (in pixels):')),
            self._create_pref_spinner('space between two pages',
            1, 0, 2, 1, 2, 0, None))

        page.new_section(_('Files'))

        page.add_row(self._create_pref_check_button(
            _('Automatically open the last viewed file on startup'),
            'auto load last file',
            _('Automatically open, on startup, the file that was open when MComix was last closed.')))

        page.add_row(Gtk.Label(label=_('Store information about recently opened files:')),
            self._create_store_recent_combobox())

        page.add_row(self._create_pref_check_button(
            _('Save As opens at the last directory saved into'),
            'store last saved in directory',
            _('Open the Save As dialog at the directory in which the last file was saved.')))

        return page

    def _init_display_tab(self):
        # ----------------------------------------------------------------
        # The "Display" tab.
        # ----------------------------------------------------------------
        page = preferences_page._PreferencePage()

        page.new_section(_('Fullscreen'))

        page.add_row(self._create_pref_check_button(
            _('Use fullscreen by default'),
            'default fullscreen', None))

        page.add_row(self._create_pref_check_button(
            _('Automatically hide all toolbars in fullscreen'),
            'hide all in fullscreen', None))

        page.new_section(_('Fit to size mode'))

        page.add_row(Gtk.Label(label=_('Fixed width for wide pages:')),
            self._create_pref_spinner('fit to size width wide',
            1, 10, constants.RENDER_SIZE_LIMIT, 10, 50, 0, None))

        page.add_row(Gtk.Label(label=_('Fixed height for wide pages:')),
            self._create_pref_spinner('fit to size height wide',
            1, 10, constants.RENDER_SIZE_LIMIT, 10, 50, 0, None))

        page.add_row(Gtk.Label(label=_('Fixed width for other pages:')),
            self._create_pref_spinner('fit to size width other',
            1, 10, constants.RENDER_SIZE_LIMIT, 10, 50, 0, None))

        page.add_row(Gtk.Label(label=_('Fixed height for other pages:')),
            self._create_pref_spinner('fit to size height other',
            1, 10, constants.RENDER_SIZE_LIMIT, 10, 50, 0, None))

        page.new_section(_('Slideshow'))

        page.add_row(Gtk.Label(label=_('Slideshow delay (in seconds):')),
            self._create_pref_spinner('slideshow delay',
            1000.0, 0.01, 3600.0, 0.1, 1, 2, None))

        page.add_row(Gtk.Label(label=_('Slideshow step (in pixels):')),
            self._create_pref_spinner('number of pixels to scroll per slideshow event',
            1, -500, 500, 1, 1, 0,
            _('Specify the number of pixels to scroll while in slideshow mode. A positive value will scroll forward, a negative value will scroll backwards, and a value of 0 will cause the slideshow to always flip to a new page.')))

        page.add_row(self._create_pref_check_button(
            _('During a slideshow automatically open the next archive'),
            'slideshow can go to next archive',
            _('While in slideshow mode allow the next archive to automatically be opened.')))

        page.new_section(_('Rotation'))

        page.add_row(self._create_pref_check_button(
            _('Automatically rotate images according to their metadata'),
            'auto rotate from exif',
            _('Automatically rotate images when an orientation is specified in the image metadata, such as in an Exif tag.')))

        page.new_section(_('Image quality'))

        page.add_row(Gtk.Label(label=_('Scaling mode')),
            self._create_scaling_quality_combobox())

        upscale_widgets = [self._create_pref_check_button(
            _('Neural upscaling for small pages'),
            'neural upscaling',
            _('Enlarge small pages with a neural network (ONNX model, needs a DirectX 12 GPU and onnxruntime-windowsml).'))]
        page.add_row(upscale_widgets[-1])

        upscale_widgets += [Gtk.Label(label=_('Upscaling model:')),
                            self._create_upscale_model_chooser()]
        page.add_row(*upscale_widgets[-2:])

        model_help = Gtk.Label(label=_(
            'The network that enlarges pages. Keep the bundled default '
            '(SPAN-F-YC, 2x) unless you converted another model with '
            'tools/export_onnx.py. Prefer ".fp16.onnx" files: they are '
            'much faster.'))
        model_help.set_line_wrap(True)
        model_help.set_max_width_chars(60)
        model_help.get_style_context().add_class('dim-label')
        page.add_row(model_help)

        upscale_widgets += [Gtk.Label(label=_('Only upscale pages smaller than (pixels):')),
                            self._create_pref_spinner('neural upscaling max input size',
                            1, 64, 8192, 64, 256, 0, None)]
        page.add_row(*upscale_widgets[-2:])

        upscale_widgets.append(self._create_pref_check_button(
            _('Upscale pages even when they are not enlarged'),
            'neural upscaling always', None))
        page.add_row(upscale_widgets[-1])

        upscale_widgets += [Gtk.Label(label=_('GPU runtime:')),
                            self._create_combobox(
                                ((_('Automatic (fastest available)'), upscale.RUNTIME_AUTO),
                                 (_('DirectML'), upscale.RUNTIME_DIRECTML)),
                                'neural upscaling runtime')]
        upscale_widgets[-1].set_tooltip_text(_(
            'Automatic uses a faster runtime from Windows ML (e.g. NVIDIA '
            'TensorRT-RTX) when Windows has installed one for your GPU, and '
            'DirectML otherwise.'))
        page.add_row(*upscale_widgets[-2:])
        page.add_row(self._create_upscale_runtime_status())

        if not upscale.gpu_available():
            # Neural upscaling never runs on the CPU.
            for widget in upscale_widgets:
                widget.set_sensitive(False)
            model_help.set_text(_(
                'Neural upscaling is unavailable: it needs a DirectX 12 GPU '
                'and the onnxruntime-windowsml package.'))

        return page

    def _create_upscale_model_chooser(self):
        chooser = Gtk.FileChooserButton(title=_('Select upscaling model'),
                                        action=Gtk.FileChooserAction.OPEN)
        file_filter = Gtk.FileFilter()
        file_filter.set_name(_('ONNX models'))
        file_filter.add_pattern('*.onnx')
        chooser.add_filter(file_filter)
        chooser.set_tooltip_text(_('An ONNX super-resolution model made with tools/export_onnx.py.'))
        if upscale.model_path():
            chooser.set_filename(upscale.model_path())
        chooser.connect('file-set', self._upscale_model_cb)

        default_button = Gtk.Button(label=_('Default'))
        default_button.set_tooltip_text(_('Use the bundled model (SPAN-F-YC, 2x).'))
        default_button.set_sensitive(os.path.isfile(upscale.DEFAULT_MODEL))
        default_button.connect('clicked', self._upscale_model_default_cb, chooser)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.pack_start(chooser, True, True, 0)
        box.pack_start(default_button, False, False, 0)
        return box

    def _upscale_model_cb(self, chooser):
        filename = chooser.get_filename() or ''
        # Store '' for the default model, so it follows MComix updates.
        if os.path.normcase(filename) == os.path.normcase(upscale.DEFAULT_MODEL):
            filename = ''
        prefs['neural upscaling model'] = filename
        self._window.imagehandler.upscaler.configure()
        self._window.draw_image()

    def _create_upscale_runtime_status(self):
        """ Returns a row telling which GPU runtime upscaling uses, with a
        button to install a faster one when Windows offers it. """
        status = Gtk.Label()
        status.set_line_wrap(True)
        status.set_max_width_chars(60)
        status.set_xalign(0)
        status.get_style_context().add_class('dim-label')
        install = Gtk.Button()
        install.set_no_show_all(True)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.pack_start(status, True, True, 0)
        box.pack_start(install, False, False, 0)

        in_use = self._window.imagehandler.upscaler.runtime_label
        status.set_text(_('In use: %s') % in_use if in_use else '')

        def show(installed, installable):
            from mcomix.upscale_backends import windows_ml
            text = [status.get_text()] if in_use else []
            if installed and windows_ml.label(installed) != in_use:
                text.append(_('Available: %s') % windows_ml.label(installed))
            elif installable:
                text.append(_('Windows can install %s for this GPU.')
                            % windows_ml.label(installable))
                install.set_label(_('Install'))
                install.set_tooltip_text(_('Download and install %s from Microsoft '
                                           '(may take a few minutes).')
                                         % windows_ml.label(installable))
                install.show()
            status.set_text('  '.join(text))
            return False

        def query():
            from mcomix.upscale_backends import windows_ml
            installed = windows_ml.installed_provider()
            installable = None if installed else windows_ml.installable_provider()
            GLib.idle_add(show, installed, installable)

        def do_install(button):
            from mcomix.upscale_backends import windows_ml
            name = windows_ml.installable_provider()
            if name is None:
                return
            button.set_sensitive(False)
            status.set_text(_('Installing %s...') % windows_ml.label(name))

            def run():
                try:
                    windows_ml.install(name)
                    message = _('%s is installed.') % windows_ml.label(name)
                except Exception as e:
                    message = _('Could not install %(name)s: %(error)s') % {
                        'name': windows_ml.label(name), 'error': e}

                def done():
                    status.set_text(message)
                    button.hide()
                    self._window.imagehandler.upscaler.configure()
                    return False
                GLib.idle_add(done)
            threading.Thread(target=run, name='upscale-install', daemon=True).start()

        install.connect('clicked', do_install)
        threading.Thread(target=query, name='upscale-runtime-query', daemon=True).start()
        return box

    def _upscale_model_default_cb(self, button, chooser):
        chooser.set_filename(upscale.DEFAULT_MODEL)
        prefs['neural upscaling model'] = ''
        self._window.imagehandler.upscaler.configure()
        self._window.draw_image()

    def _init_advanced_tab(self):
        # ----------------------------------------------------------------
        # The "Advanced" tab.
        # ----------------------------------------------------------------

        page = preferences_page._PreferencePage()

        page.new_section(_('File order'))

        page.add_row(Gtk.Label(label=_('Sort files and directories by:')),
            self._create_sort_by_control())

        page.add_row(Gtk.Label(label=_('Sort archives by:')),
            self._create_archive_sort_by_control())

        page.new_section(_('Extraction and cache'))

        page.add_row(self._create_pref_check_button(
            _('Read archive pages on demand'),
            'lazy archive reading',
            _('Read pages directly from archives that allow it (e.g. ZIP/CBZ, non-solid RAR/CBR), instead of extracting the whole archive to a temporary folder. Takes effect for the next opened archive.')))

        page.add_row(Gtk.Label(label=_('Maximum number of concurrent extraction threads:')),
            self._create_pref_spinner('max extract threads',
            1, 1, 16, 1, 4, 0,
            _('Set the maximum number of concurrent threads for formats that support it.')))

        page.add_row(self._create_pref_check_button(
            _('Store thumbnails for opened files'),
            'create thumbnails',
            _('Store thumbnails for opened files according to the freedesktop.org specification. These thumbnails are shared by many other applications, such as most file managers.')))

        page.add_row(Gtk.Label(label=_('Maximum number of pages to store in the cache:')),
            self._create_pref_spinner('max pages to cache',
            1, -1, 500, 1, 3, 0,
            _('Set the max number of pages to cache. A value of -1 will cache the entire archive.')))

        page.new_section(_('Magnifying Lens'))

        page.add_row(Gtk.Label(label=_('Magnifying lens size (in pixels):')),
            self._create_pref_spinner('lens size',
            1, 50, 400, 1, 10, 0,
            _('Set the size of the magnifying lens. It is a square with a side of this many pixels.')))

        page.add_row(Gtk.Label(label=_('Magnification factor:')),
            self._create_pref_spinner('lens magnification',
            1, 1.1, 10.0, 0.1, 1.0, 1,
            _('Set the magnification factor of the magnifying lens.')))

        page.new_section(_('Comments'))

        page.add_row(Gtk.Label(label=_('Comment extensions:')),
            self._create_extensions_entry())

        page.new_section(_('Animated images'))

        page.add_row(Gtk.Label(_('Animation mode:')),
            self._create_animation_mode_combobox())

        page.add_row(self._create_pref_check_button(
            _('Enlarge animated WebP images'), 'enlarge animated webp',
            _('Allow animated WebP pages to grow beyond their original dimensions. '
              'Turn this off to reduce animation memory use; shrinking to fit still works.')))
        page.add_row(
            Gtk.Label(label=_('Maximum animated WebP display size (pixels):')),
            self._create_pref_spinner(
                'animated webp max size', 1, 0, 16384, 256, 1024, 0,
                _('Limit the longest display edge, preserving aspect ratio. '
                  'Larger native images are also reduced. Use 0 for unlimited.')))
        page.add_row(
            Gtk.Label(label=_('Animation cache limit (MiB):')),
            self._create_pref_spinner(
                'animation cache limit', 1, 8, 1024, 8, 32, 0,
                _('Memory shared across visible WebP animations. Decoder memory and '
                  'compressed file data use additional memory.')))
        animation_help = Gtk.Label(label=_(
            'Example: a decoded 600 × 900 frame uses about 2.1 MiB; four frames '
            'use 8.2 MiB, plus resized output buffers. A 2000 × 1333 output buffer '
            'alone uses about 10.2 MiB. The compressed WebP file size does not '
            'indicate playback memory use.'))
        animation_help.set_line_wrap(True)
        animation_help.set_max_width_chars(60)
        animation_help.get_style_context().add_class('dim-label')
        page.add_row(animation_help)

        return page

    def _init_shortcuts_tab(self):
        # ----------------------------------------------------------------
        # The "Shortcuts" tab.
        # ----------------------------------------------------------------
        km = keybindings.keybinding_manager(self._window)
        page = keybindings_editor.KeybindingEditorWindow(km)
        self.shortcuts = page
        return page

    def _tab_page_changed(self, notebook, page_ptr, page_num):
        """ Dynamically switches the "Reset" button's text and tooltip
        depending on the currently selected tab page. """
        new_page = notebook.get_nth_page(page_num)
        if new_page == self.shortcuts:
            self.reset_button.set_label(_("_Reset keys"))
            self.reset_button.set_tooltip_text(
                _("Resets all keyboard shortcuts to their default values."))
            self.reset_button.set_sensitive(True)
        else:
            self.reset_button.set_label(_('Clear _dialog choices'))
            self.reset_button.set_tooltip_text(
                _('Clears all dialog choices that you have previously chosen not to be asked again.'))
            self.reset_button.set_sensitive(len(prefs['stored dialog choices']) > 0)

    def _response(self, dialog, response):
        if response == Gtk.ResponseType.CLOSE:
            _close_dialog()

        elif response == constants.RESPONSE_REVERT_TO_DEFAULT:
            if self.notebook.get_nth_page(self.notebook.get_current_page()) == self.shortcuts:
                # "Shortcuts" page is active, reset all keys to their default value
                km = keybindings.keybinding_manager(self._window)
                km.clear_all()
                self._window._event_handler.register_key_events()
                km.save()
                self.shortcuts.refresh_model()
            else:
                # Reset stored choices
                prefs['stored dialog choices'] = {}
                self.reset_button.set_sensitive(False)

        else:
            # Other responses close the dialog, e.g. clicking the X icon on the dialog.
            _close_dialog()

    def _create_language_control(self):
        """ Creates and returns the combobox for language selection. """
        # Source: http://en.wikipedia.org/wiki/List_of_ISO_639-1_codes
        languages = [
            (_('Auto-detect (Default)'), 'auto'),
            ('Català', 'ca'),  # Catalan
            ('čeština', 'cs'),  # Czech
            ('Deutsch', 'de'),  # German
            ('ελληνικά', 'el'),  # Greek
            ('English', 'en'),  # English
            ('Español', 'es'),  # Spanish
            ('فارسی', 'fa'),  # Persian
            ('Français', 'fr'),  # French
            ('Galego', 'gl'),  # Galician
            ('עברית', 'he'),  # Hebrew
            ('Hrvatski jezik', 'hr'),  # Croatian
            ('Magyar', 'hu'),  # Hungarian
            ('Bahasa Indonesia', 'id'),  # Indonesian
            ('Italiano', 'it'),  # Italian
            ('日本語', 'ja'),  # Japanese
            ('한국어', 'ko'),  # Korean
            ('Nederlands', 'nl'),  # Dutch
            ('Język polski', 'pl'),  # Polish
            ('Português', 'pt_BR'),  # Portuguese
            ('pусский язык', 'ru'),  # Russian
            ('Svenska', 'sv'),  # Swedish
            ('українська мова', 'uk'),  # Ukrainian
            ('简体中文', 'zh_CN'),  # Chinese (simplified)
            ('正體中文', 'zh_TW')]  # Chinese (traditional)
        languages.sort(key=operator.itemgetter(0))

        return self._create_combobox(languages, 'language')

    def _create_doublepage_as_one_control(self):
        """ Creates the ComboBox control for selecting virtual double page options. """
        items = (
                (_('Never'), 0),
                (_('Only for title pages'), constants.SHOW_DOUBLE_AS_ONE_TITLE),
                (_('Only for wide images'), constants.SHOW_DOUBLE_AS_ONE_WIDE),
                (_('Always'), constants.SHOW_DOUBLE_AS_ONE_TITLE | constants.SHOW_DOUBLE_AS_ONE_WIDE))

        box = self._create_combobox(items, 'virtual double page for fitting images')

        box.set_tooltip_text(
            _("When showing the first page of an archive, or an image's width "
              "exceeds its height, only a single page will be displayed."))

        return box

    def _create_double_page_autoresize_control(self):
        """ Creates the ComboBox control for selecting double page autoresize options. """
        items = (
                (_('Prefer same scale'), constants.DOUBLE_PAGE_AUTORESIZE_SCALE),
                (_('Prefer same size'), constants.DOUBLE_PAGE_AUTORESIZE_SIZE),
                (_('Fit to same size'), constants.DOUBLE_PAGE_AUTORESIZE_FIT_SIZE))

        box = self._create_combobox(items, 'double page autoresize')

        box.set_tooltip_text(
            _("Maintain relative size or fit to same size."))

        return box

    def _create_sort_by_control(self):
        """ Creates the ComboBox control for selecting file sort by options. """
        sortkey_items = (
                (_('No sorting'), 0),
                (_('File name'), constants.SORT_NAME),
                (_('File name (GLib)'), constants.SORT_NAME_GLIB),
                (_('File size'), constants.SORT_SIZE),
                (_('Last modified'), constants.SORT_LAST_MODIFIED))

        sortkey_box = self._create_combobox(sortkey_items, 'sort by')

        sortorder_items = (
                (_('Ascending'), constants.SORT_ASCENDING),
                (_('Descending'), constants.SORT_DESCENDING))

        sortorder_box = self._create_combobox(sortorder_items, 'sort order')

        box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
        box.pack_start(sortkey_box, True, True, 0)
        box.pack_start(sortorder_box, True, True, 0)

        label = _("Files will be opened and displayed according to the sort order "
              "specified here. This option does not affect ordering within archives.")
        sortkey_box.set_tooltip_text(label)
        sortorder_box.set_tooltip_text(label)

        return box

    def _create_archive_sort_by_control(self):
        """ Creates the ComboBox control for selecting archive sort by options. """
        sortkey_items = (
                (_('No sorting'), 0),
                (_('Natural order'), constants.SORT_NAME),
                (_('Literal order'), constants.SORT_NAME_LITERAL),
                (_('GLib order'), constants.SORT_NAME_GLIB))

        sortkey_box = self._create_combobox(sortkey_items, 'sort archive by')

        sortorder_items = (
                (_('Ascending'), constants.SORT_ASCENDING),
                (_('Descending'), constants.SORT_DESCENDING))

        sortorder_box = self._create_combobox(sortorder_items, 'sort archive order')

        box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
        box.pack_start(sortkey_box, True, True, 0)
        box.pack_start(sortorder_box, True, True, 0)

        label = _("Files within archives will be sorted according to the order specified here. "
                  "Natural order will sort numbered files based on their natural order, "
                  "i.e. 1, 2, ..., 10, while literal order uses standard C sorting, "
                  "i.e. 1, 2, 34, 5. "
                  "GLib order will sort files using collate keys generated by GLib/GTK, "
                  "used by many other GTK applications.")
        sortkey_box.set_tooltip_text(label)
        sortorder_box.set_tooltip_text(label)

        return box

    def _create_store_recent_combobox(self):
        """ Creates the combobox for "Store recently opened files". """
        items = (
                (_('Never'), False),
                (_('Always'), True))

        box = self._create_combobox(items, 'store recent file info')
        box.set_tooltip_text(
            _('Add information about all files opened from within MComix to the shared recent files list.'))
        return box

    def _store_recent_changed(self, last_value, value):
        """ Called when option "Store recently opened files" was changed. """
        self._window.filehandler.last_read_page.set_enabled(value)

        # If "Never" was selected, ask to purge recent files.
        if (bool(last_value) is True and value is False
            and (self._window.uimanager.recent.count() > 0
                 or self._window.filehandler.last_read_page.count() > 0)):

            dialog = message_dialog.MessageDialog(self, Gtk.DialogFlags.MODAL,
                Gtk.MessageType.INFO, Gtk.ButtonsType.YES_NO)
            dialog.set_default_response(Gtk.ResponseType.YES)
            dialog.set_text(
                _('Delete information about recently opened files?'),
                _('This will remove all entries from the "Recent" menu,'
                  ' and clear information about last read pages.'))
            response = dialog.run()

            if response == Gtk.ResponseType.YES:
                self._window.uimanager.recent.remove_all()
                self._window.filehandler.last_read_page.clear_all()

    def _create_scaling_quality_combobox(self):
        """ Creates combo box for image scaling quality """
        items = (
                (_('Normal (fast)'), int(GdkPixbuf.InterpType.TILES)),
                (_('Bilinear'), int(GdkPixbuf.InterpType.BILINEAR)),
                (_('Lanczos (sharp)'), image_tools.SCALING_LANCZOS))

        box = self._create_combobox(items, 'scaling quality')
        box.set_tooltip_text(
            _('Changes how pages are scaled. Lanczos is the sharpest and smooths '
              'fine patterns best when shrinking pages, but takes a few more '
              'milliseconds per page; the magnifier and thumbnails use bilinear with it.'))

        return box

    def _create_animation_mode_combobox(self):
        """ Creates combo box for animation mode """
        items = (
                (_('Never'), constants.ANIMATION_DISABLED),
                (_('Normal'), constants.ANIMATION_NORMAL))

        box = self._create_combobox(items, 'animation mode')
        box.set_tooltip_text(
            _('Controls how animated images should be displayed.'))

        return box

    def _create_combobox(self, options, preference):
        """ Creates a new dropdown combobox for <preference> and populates it
        with the items passed in C{options}. The item holding the current
        value is pre-selected.

        @param options: List of tuples: (Option display text, option value)
        @returns Gtk.ComboBox
        """
        assert options and len(options[0]) == 2, "Invalid format for options."

        # Use the first list item to determine typing of model fields.
        # First field is textual description, second field is value.
        model = Gtk.ListStore(GObject.TYPE_STRING, type(options[0][1]))
        for text, value in options:
            model.append((text, value))

        box = Gtk.ComboBox(model=model)
        renderer = Gtk.CellRendererText()
        box.pack_start(renderer, True)
        box.add_attribute(renderer, "text", 0)

        # Set active box option
        iter = model.get_iter_first()
        while iter:
            if model.get_value(iter, 1) == prefs[preference]:
                box.set_active_iter(iter)
                break
            else:
                iter = model.iter_next(iter)

        box.connect('changed', self._combobox_cb, preference)

        return box

    def _combobox_cb(self, combobox, preference):
        """Callback for all combobox-type preferences."""
        iter = combobox.get_active_iter()
        if not combobox.get_model().iter_is_valid(iter):
            return
        value = combobox.get_model().get_value(iter, 1)
        last_value = prefs[preference]
        prefs[preference] = value

        if (preference in ('scaling quality', 'animation mode', 'neural upscaling runtime')
                and value == last_value):
            return

        if preference == 'virtual double page for fitting images':
            self._window.imagehandler.do_cacheing()
            self._window.draw_image()

        elif preference in ('double page autoresize', 'scaling quality'):
            self._window.draw_image()

        elif preference in ('sort by', 'sort order', 'sort archive by',
                            'sort archive order', 'animation mode'):
            self._window.filehandler.refresh_file()

        elif preference == 'store recent file info':
            self._store_recent_changed(last_value, value)

        elif preference == 'neural upscaling runtime':
            self._window.imagehandler.upscaler.configure()
            self._window.draw_image()


    def _create_extensions_entry(self):
        entry = Gtk.Entry()
        entry.set_size_request(200, -1)
        entry.set_text(', '.join(prefs['comment extensions']))
        entry.connect('activate', self._entry_cb)
        entry.connect('focus_out_event', self._entry_cb)
        entry.set_tooltip_text(
            _('Treat all files found within archives, that have one of these file endings, as comments.'))
        return entry


    def _create_pref_check_button(self, label, prefkey, tooltip_text):
        button = Gtk.CheckButton(label)
        button.set_active(prefs[prefkey])
        button.connect('toggled', self._check_button_cb, prefkey)
        if tooltip_text:
            button.set_tooltip_text(tooltip_text)
        return button


    def _create_binary_pref_radio_buttons(self, label1, prefkey1, tooltip_text1,
        label2, prefkey2, tooltip_text2):
        button1 = Gtk.RadioButton(label=label1)
        button1.connect('toggled', self._check_button_cb, prefkey1)
        if tooltip_text1:
            button1.set_tooltip_text(tooltip_text1)
        button2 = Gtk.RadioButton(group=button1, label=label2)
        button2.connect('toggled', self._check_button_cb, prefkey2)
        if tooltip_text2:
            button2.set_tooltip_text(tooltip_text2)
        button2.set_active(prefs[prefkey2])
        return button1, button2


    def _create_color_button(self, prefkey):
        rgba = image_tools.color_to_floats_rgba(prefs[prefkey])
        button = Gtk.ColorButton.new_with_rgba(Gdk.RGBA(*rgba))
        button.connect('color_set', self._color_button_cb, prefkey)
        return button


    def _check_button_cb(self, button, preference):
        """Callback for all checkbutton-type preferences."""

        prefs[preference] = button.get_active()

        if preference == 'color box bg' and button.get_active():

            if not prefs['smart bg'] or not self._window.filehandler.file_loaded:
                self._window.set_bg_colour(prefs['bg colour'])

        elif preference == 'smart bg' and button.get_active():
            # draw_image() will set the main background to the smart background
            self._window.draw_image()

        elif preference == 'color box thumb bg' and button.get_active():
            prefs['smart thumb bg'] = False
            self._window.thumbnailsidebar.change_thumbnail_background_color(prefs['thumb bg colour'])

        elif preference == 'smart thumb bg' and button.get_active():
            prefs['color box thumb bg'] = False
            if self._window.imagehandler.page_is_available():
                pixbuf_count = 2 if self._window.displayed_double() else 1 # XXX limited to at most 2 pages
                bg_colour = self._window.imagehandler.get_pixbuf_auto_background(pixbuf_count)
                self._window.thumbnailsidebar.change_thumbnail_background_color(bg_colour)

        elif preference in ('checkered bg for transparent images',
                            'auto rotate from exif', 'enlarge animated webp'):
            if preference == 'auto rotate from exif':
                self._window.imagehandler.do_cacheing()
            self._window.draw_image()

        elif (preference == 'hide all in fullscreen' and
            self._window.is_fullscreen):
            self._window.draw_image()

        elif preference in ('neural upscaling', 'neural upscaling always'):
            self._window.imagehandler.upscaler.configure()
            self._window.draw_image()

        elif preference == 'show page numbers on thumbnails':
            self._window.thumbnailsidebar.toggle_page_numbers_visible()

        elif preference == 'archive thumbnail as icon':
            self._window.update_icon(True)

    def _color_button_cb(self, colorbutton, preference):
        """Callback for the background colour selection button."""

        colour = colorbutton.get_color()

        if preference == 'bg colour':
            prefs['bg colour'] = colour.red, colour.green, colour.blue

            if not prefs['smart bg'] or not self._window.filehandler.file_loaded:
                self._window.set_bg_colour(prefs['bg colour'])

        elif preference == 'thumb bg colour':

            prefs['thumb bg colour'] = colour.red, colour.green, colour.blue

            if not prefs['smart thumb bg'] or not self._window.filehandler.file_loaded:
                self._window.thumbnailsidebar.change_thumbnail_background_color( prefs['thumb bg colour'] )


    def _create_pref_spinner(self, prefkey, scale, lower, upper, step_incr,
        page_incr, digits, tooltip_text):
        value = prefs[prefkey] / scale
        adjustment = Gtk.Adjustment(value, lower, upper, step_incr, page_incr)
        spinner = Gtk.SpinButton.new(adjustment, 0.0, digits)
        spinner.set_size_request(80, -1)
        spinner.connect('value_changed', self._spinner_cb, prefkey)
        if tooltip_text:
            spinner.set_tooltip_text(tooltip_text)
        return spinner


    def _spinner_cb(self, spinbutton, preference):
        """Callback for spinner-type preferences."""
        value = spinbutton.get_value()

        if preference == 'lens magnification':
            prefs[preference] = value
        elif preference == 'slideshow delay':
            prefs[preference] = int(round(value * 1000))
        elif preference == 'smart scroll percentage':
            prefs[preference] = value / 100.0
        else:
            prefs[preference] = int(value)

        if preference == 'slideshow delay':
            self._window.slideshow.update_delay()

        elif preference == 'thumbnail size':
            self._window.thumbnailsidebar.resize()
            self._window.draw_image()

        elif preference == 'max pages to cache':
            self._window.imagehandler.do_cacheing()

        elif preference in ('animation cache limit', 'animated webp max size'):
            self._window.draw_image()

        elif preference == 'number of key presses before page turn':
            self._window._event_handler._extra_scroll_events = 0

        elif preference in ('fit to size width wide', 'fit to size height wide',
            'fit to size width other', 'fit to size height other',):
            self._window.change_zoom_mode()

        elif preference == 'space between two pages':
            self._window.update_space()

        elif preference == 'neural upscaling max input size':
            self._window.imagehandler.upscaler.configure()
            self._window.draw_image()


    def _entry_cb(self, entry, event=None):
        """Callback for entry-type preferences."""
        text = entry.get_text()
        extensions = [e.strip() for e in text.split(',')]
        prefs['comment extensions'] = [e for e in extensions if e]
        self._window.filehandler.update_comment_extensions()

def open_dialog(action, window):
    """Create and display the preference dialog."""

    global _dialog

    # if the dialog window is not created then create the window
    if _dialog is None:
        _dialog = _PreferencesDialog(window)
    else:
        # if the dialog window already exists bring it to the forefront of the screen
        _dialog.present()

def _close_dialog():

    global _dialog

    # if the dialog window exists then destroy it
    if _dialog is not None:
        _dialog.destroy()
        _dialog = None


# vim: expandtab:sw=4:ts=4
