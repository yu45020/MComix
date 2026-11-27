"""main.py - Main window."""

import math
import os
import shutil

from gi.repository import Gdk, GdkPixbuf, Gtk, GLib

from mcomix import constants
from mcomix import cursor_handler
from mcomix import i18n
from mcomix import icons
from mcomix import enhance_backend
from mcomix import event
from mcomix import file_handler
from mcomix import image_handler
from mcomix import image_tools
from mcomix import lens
from mcomix import preferences
from mcomix.preferences import prefs
from mcomix import ui
from mcomix import slideshow
from mcomix import status
from mcomix import thumbbar
from mcomix import upscale
from mcomix import clipboard
from mcomix import pageselect
from mcomix import osd
from mcomix import keybindings
from mcomix import zoom
from mcomix import message_dialog
from mcomix import callback
from mcomix.library import backend, main_dialog
from mcomix import tools
from mcomix import box
from mcomix import layout
from mcomix import log
from mcomix.transform import Matrix, Transform
from mcomix.i18n import _

from typing import Any, List, Optional, Tuple, Union, cast


class MainWindow(Gtk.Window):

    """The main window, is created at start and terminates the
    program when closed.
    """

    def __init__(self, fullscreen=False, is_slideshow=slideshow,
            show_library=False, manga_mode=False, double_page=False,
            zoom_mode=None, open_path=None, open_page=0, open_entry=''):
        super(MainWindow, self).__init__(Gtk.WindowType.TOPLEVEL)

        # ----------------------------------------------------------------
        # Attributes
        # ----------------------------------------------------------------
        # Used to detect window fullscreen state transitions.
        self.was_fullscreen = False
        self.is_manga_mode = False
        self.previous_size = (None, None)
        self.was_out_of_focus = False
        # Set once shutdown starts; later quit requests are ignored.
        self._terminating = False

        self.layout = layout.create_dummy_layout()
        self.transforms: List[Matrix] = []
        self._waiting_for_redraw = False
        self._redraw_scroll_to: Optional[int] = None
        self._pending_decode_redraw = None
        self._pending_notice = ''

        self._main_layout = Gtk.Layout()
        # Wrap main layout into an event box so
        # we  can change its background color.
        self._event_box = Gtk.EventBox()
        self._event_box.add(self._main_layout)
        self._event_handler = event.EventHandler(self)
        self._vadjust = self._main_layout.get_vadjustment()
        self._hadjust = self._main_layout.get_hadjustment()
        self._scroll = (
            Gtk.Scrollbar.new(Gtk.Orientation.HORIZONTAL, self._hadjust),
            Gtk.Scrollbar.new(Gtk.Orientation.VERTICAL, self._vadjust),
        )

        self.filehandler = file_handler.FileHandler(self)
        self.filehandler.file_closed += self._on_file_closed
        self.filehandler.file_opened += self._on_file_opened
        self.imagehandler = image_handler.ImageHandler(self)
        self.imagehandler.page_available += self._page_available
        self.imagehandler.page_cached += self._page_cached
        self.imagehandler.upscaler.upscaled += self._page_upscaled
        self.imagehandler.upscaler.rendered_ready += self._page_rendered
        self.imagehandler.upscaler.model_failed += self._upscale_model_failed
        #: Upscaler keys of the displayed pages
        self._upscale_keys: List[upscale.PageKey] = []
        self.thumbnailsidebar = thumbbar.ThumbnailSidebar(self)

        self.statusbar = status.Statusbar()
        self.clipboard = clipboard.Clipboard(self)
        self.slideshow = slideshow.Slideshow(self)
        self.cursor_handler = cursor_handler.CursorHandler(self)
        self.enhancer = enhance_backend.ImageEnhancer(self)
        self.lens = lens.MagnifyingLens(self)
        self.osd = osd.OnScreenDisplay(self)
        self.zoom = zoom.ZoomModel()
        self.uimanager = ui.MainUI(self)
        self.menubar = self.uimanager.get_widget('/Menu')
        self.toolbar = self.uimanager.get_widget('/Tool')
        self.popup = self.uimanager.get_widget('/Popup')
        self.actiongroup = self.uimanager.get_action_groups()[0]

        self.images = [Gtk.Image(), Gtk.Image()] # XXX limited to at most 2 pages
        image_overlay = self._create_image_overlay()
        playback: Any = self.imagehandler.animations
        playback.notice_changed += self._animation_notice_changed

        # ----------------------------------------------------------------
        # Setup
        # ----------------------------------------------------------------
        self.set_title(constants.APPNAME)
        self.set_size_request(300, 300)  # Avoid making the window *too* small

        # Hook up keyboard shortcuts
        self._event_handler.register_key_events()

        # This is a hack to get the focus away from the toolbar so that
        # we don't activate it with space or some other key (alternative?)
        self.toolbar.set_focus_child(
            self.uimanager.get_widget('/Tool/expander'))
        self.toolbar.set_style(Gtk.ToolbarStyle.ICONS)
        self.toolbar.set_icon_size(Gtk.IconSize.LARGE_TOOLBAR)

        for img in self.images:
            self._main_layout.put(img, 0, 0)
        self.set_bg_colour(prefs['bg colour'])

        self._vadjust.step_increment = 15
        self._vadjust.page_increment = 1
        self._hadjust.step_increment = 15
        self._hadjust.page_increment = 1

        table = Gtk.Table(2, 2, False)
        table.attach(self.thumbnailsidebar, 0, 1, 2, 5, Gtk.AttachOptions.FILL,
            Gtk.AttachOptions.FILL|Gtk.AttachOptions.EXPAND, 0, 0)

        table.attach(image_overlay, 1, 2, 2, 3, Gtk.AttachOptions.FILL|Gtk.AttachOptions.EXPAND,
            Gtk.AttachOptions.FILL|Gtk.AttachOptions.EXPAND, 0, 0)
        table.attach(self._scroll[constants.PageAxis.HEIGHT], 2, 3, 2, 3, Gtk.AttachOptions.FILL|Gtk.AttachOptions.SHRINK,
            Gtk.AttachOptions.FILL|Gtk.AttachOptions.SHRINK, 0, 0)
        table.attach(self._scroll[constants.PageAxis.WIDTH], 1, 2, 4, 5, Gtk.AttachOptions.FILL|Gtk.AttachOptions.SHRINK,
            Gtk.AttachOptions.FILL, 0, 0)
        table.attach(self.menubar, 0, 3, 0, 1, Gtk.AttachOptions.FILL|Gtk.AttachOptions.SHRINK,
            Gtk.AttachOptions.FILL, 0, 0)
        table.attach(self.toolbar, 0, 3, 1, 2, Gtk.AttachOptions.FILL|Gtk.AttachOptions.SHRINK,
            Gtk.AttachOptions.FILL, 0, 0)
        table.attach(self.statusbar, 0, 3, 5, 6, Gtk.AttachOptions.FILL|Gtk.AttachOptions.SHRINK,
            Gtk.AttachOptions.FILL, 0, 0)

        if prefs['default double page'] or double_page:
            self.actiongroup.get_action('double_page').activate()

        if prefs['default manga mode'] or manga_mode:
            self.actiongroup.get_action('manga_mode').activate()

        # Determine zoom mode. If zoom_mode is passed, it overrides
        # the zoom mode preference.
        zoom_actions = { constants.ZoomMode.BEST: 'best_fit_mode',
                constants.ZoomMode.WIDTH: 'fit_width_mode',
                constants.ZoomMode.HEIGHT: 'fit_height_mode',
                constants.ZoomMode.SIZE: 'fit_size_mode',
                constants.ZoomMode.MANUAL: 'fit_manual_mode' }

        if zoom_mode is not None:
            zoom_action = zoom_actions[zoom_mode]
        else:
            zoom_action = zoom_actions[prefs['zoom mode']]

        if zoom_action == 'fit_manual_mode':
            # This little ugly hack is to get the activate call on
            # 'fit_manual_mode' to actually create an event (and callback).
            # Since manual mode is the default selected radio button action
            # it won't send an event if we activate it when it is already
            # the selected one.
            self.actiongroup.get_action('best_fit_mode').activate()

        self.actiongroup.get_action(zoom_action).activate()

        if prefs['stretch']:
            self.actiongroup.get_action('stretch').activate()

        if prefs['invert smart scroll']:
            self.actiongroup.get_action('invert_scroll').activate()

        if prefs['keep transformation']:
            prefs['keep transformation'] = False
            self.actiongroup.get_action('keep_transformation').activate()
        else:
            prefs['rotation'] = 0
            prefs['vertical flip'] = False
            prefs['horizontal flip'] = False

        # List of "toggles" than can be shown/hidden by the user.
        self._toggle_list = (
            # Preference        Action        Widget(s)
            ('show menubar'   , 'menubar'   , (self.menubar,)         ),
            ('show scrollbar' , 'scrollbar' , self._scroll            ),
            ('show statusbar' , 'statusbar' , (self.statusbar,)       ),
            ('show thumbnails', 'thumbnails', (self.thumbnailsidebar,)),
            ('show toolbar'   , 'toolbar'   , (self.toolbar,)         ),
        )

        # Each "toggle" widget "eats" part of the main layout visible area.
        self._toggle_axis = {
            self.thumbnailsidebar              : constants.PageAxis.WIDTH,
            self._scroll[constants.PageAxis.HEIGHT]: constants.PageAxis.WIDTH,
            self._scroll[constants.PageAxis.WIDTH] : constants.PageAxis.HEIGHT,
            self.statusbar                     : constants.PageAxis.HEIGHT,
            self.toolbar                       : constants.PageAxis.HEIGHT,
            self.menubar                       : constants.PageAxis.HEIGHT,
        }

        # Start with all "toggle" widgets hidden to avoid ugly transitions.
        for preference, action, widget_list in self._toggle_list:
            for widget in widget_list:
                widget.hide()

        toggleaction = self.actiongroup.get_action('hide_all')
        toggleaction.set_active(prefs['hide all'])

        # Sync each "toggle" widget active state with its preference.
        for preference, action, widget_list in self._toggle_list:
            self.actiongroup.get_action(action).set_active(prefs[preference])

        self.actiongroup.get_action('menu_autorotate_width').set_sensitive(False)
        self.actiongroup.get_action('menu_autorotate_height').set_sensitive(False)

        self.add(table)
        table.show()
        self._event_box.show_all()

        self._main_layout.set_events(Gdk.EventMask.BUTTON1_MOTION_MASK |
                                     Gdk.EventMask.BUTTON2_MOTION_MASK |
                                     Gdk.EventMask.BUTTON_PRESS_MASK |
                                     Gdk.EventMask.BUTTON_RELEASE_MASK |
                                     Gdk.EventMask.POINTER_MOTION_MASK)

        self._main_layout.drag_dest_set(Gtk.DestDefaults.ALL,
                                        [Gtk.TargetEntry.new('text/uri-list', 0, 0)],
                                        Gdk.DragAction.COPY |
                                        Gdk.DragAction.MOVE)

        self.connect('focus-in-event', self.gained_focus)
        self.connect('focus-out-event', self.lost_focus)
        self.connect('delete_event', self.close_program)
        self.connect('key_press_event', self._event_handler.key_press_event)
        self.connect('configure_event', self._event_handler.resize_event)
        self.connect('window-state-event', self._event_handler.window_state_event)

        self._main_layout.connect('button_release_event',
            self._event_handler.mouse_release_event)
        self._main_layout.connect('scroll_event',
            self._event_handler.scroll_wheel_event)
        self._main_layout.connect('button_press_event',
            self._event_handler.mouse_press_event)
        self._main_layout.connect('motion_notify_event',
            self._event_handler.mouse_move_event)
        self._main_layout.connect('drag_data_received',
            self._event_handler.drag_n_drop_event)

        self.uimanager.set_sensitivities()
        # Restore twice, before and after show(), to make sure it works...
        self.restore_window_geometry()
        self.show()
        self.restore_window_geometry()

        if prefs['default fullscreen'] or fullscreen:
            toggleaction = self.actiongroup.get_action('fullscreen')
            toggleaction.set_active(True)


        self._open_startup_file(open_path, open_page, open_entry)

        if is_slideshow:
            self.actiongroup.get_action('slideshow').activate()

        if show_library:
            self.actiongroup.get_action('library').activate()

        self.cursor_handler.auto_hide_on()
        # Make sure we receive *all* mouse motion events,
        # even if a modal dialog is being shown.
        def _on_event(event):
            if Gdk.EventType.MOTION_NOTIFY == event.type:
                self.cursor_handler.refresh()
            Gtk.main_do_event(event)
        Gdk.event_handler_set(_on_event)

    def _create_image_overlay(self) -> Any:
        notice = Gtk.InfoBar()
        notice.set_message_type(Gtk.MessageType.INFO)
        notice.set_valign(Gtk.Align.START)
        label = Gtk.Label(label=_(
            'Animation paused: memory limit reached. Reduce the window size '
            'or increase the animation cache limit.'))
        label.set_line_wrap(True)
        label.set_max_width_chars(60)
        notice.get_content_area().add(label)
        notice.add_button(_('Open settings'), Gtk.ResponseType.ACCEPT)
        notice.connect('response', lambda widget, response:
                       self.actiongroup.get_action('preferences').activate())
        notice.show_all()
        notice.set_no_show_all(True)
        notice.hide()
        self._animation_notice = notice
        overlay = Gtk.Overlay()
        overlay.add(self._event_box)
        overlay.add_overlay(notice)
        overlay.show()
        return overlay

    def _animation_notice_changed(self, limited: bool) -> None:
        self._animation_notice.set_visible(limited)

    def gained_focus(self, *args):
        def _delayed_unset_out_of_focus(_):
            self.was_out_of_focus = False
            return False

        if self.was_out_of_focus:
            # Since clicking into an unfocused window triggers the
            # focus event first, then the mouse event, the mouse event
            # can no longer detect that it should be skipped. Thus, delay
            # unsetting was_out_of_focus.
            GLib.idle_add(_delayed_unset_out_of_focus, None,
                          priority=GLib.PRIORITY_DEFAULT_IDLE)

    def lost_focus(self, *args):
        self.was_out_of_focus = True

    def draw_image(self, scroll_to=None):
        """Draw the current pages and update the titlebar and statusbar.
        """
        # A queued redraw uses the latest requested scroll position.
        if scroll_to is not None:
            self._redraw_scroll_to = scroll_to
        if not self._waiting_for_redraw:  # Don't stack up redraws.
            self._waiting_for_redraw = True
            GLib.idle_add(self._draw_image, priority=GLib.PRIORITY_HIGH_IDLE)

    def _update_toggle_preference(self, preference, toggleaction):
        ''' Update "toggle" widget corresponding <preference>.

        Note: the widget visibily itself is left unchanged. '''
        prefs[preference] = toggleaction.get_active()
        if 'hide all' == preference:
            self._update_toggles_sensitivity()
        # Since the size of the drawing area is dependent
        # on the visible "toggles", redraw the page.
        self.draw_image()

    def _should_toggle_be_visible(self, preference):
        ''' Return <True> if "toggle" widget for <preference> should be visible. '''
        if self.is_fullscreen:
            visible = not prefs['hide all in fullscreen']
        else:
            visible = not prefs['hide all']
        visible &= prefs[preference]
        if 'show thumbnails' == preference:
            visible &= self.filehandler.file_loaded
            visible &= self.imagehandler.get_number_of_pages() > 0
        return visible

    def _update_toggles_sensitivity(self):
        ''' Update each "toggle" widget sensitivity. '''
        sensitive = True
        if prefs['hide all']:
            sensitive = False
        elif prefs['hide all in fullscreen'] and self.is_fullscreen:
            sensitive = False
        for preference, action, widget_list in self._toggle_list:
            self.actiongroup.get_action(action).set_sensitive(sensitive)

    def _update_toggles_visibility(self):
        ''' Update each "toggle" widget visibility. '''
        for preference, action, widget_list in self._toggle_list:
            should_be_visible = self._should_toggle_be_visible(preference)
            for widget in widget_list:
                # No change in visibility?
                if should_be_visible != widget.get_visible():
                    (widget.show if should_be_visible else widget.hide)()

    def _draw_image(self) -> bool:
        """Run one redraw, allowing later requests even if rendering fails."""
        scroll_to, self._redraw_scroll_to = self._redraw_scroll_to, None
        try:
            self._draw_image_impl(scroll_to)
        except Exception:
            log.error(_('Could not draw page'), exc_info=True)
        finally:
            self._waiting_for_redraw = False
        return False

    def _draw_image_impl(self, scroll_to: Optional[int]) -> None:

        self._update_toggles_visibility()

        self.osd.clear()

        if not self.filehandler.file_loaded:
            self.imagehandler.stop_animations()
            self._pending_decode_redraw = None
            self._clear_main_area()
            return

        pixbuf_list = None
        if self.imagehandler.page_is_available():
            pixbuf_count = 2 if self.displayed_double() else 1
            pixbuf_list = self.imagehandler.get_pixbufs(pixbuf_count, block=False)
        if pixbuf_list is not None:
            self._pending_decode_redraw = None
            distribution_axis = constants.DISTRIBUTION_AXIS
            alignment_axis = constants.ALIGNMENT_AXIS
            do_not_transform = [isinstance(x, GdkPixbuf.PixbufAnimation)
                                for x in pixbuf_list]
            size_list = [[pixbuf.get_width(), pixbuf.get_height()]
                         for pixbuf in pixbuf_list]

            if self.is_manga_mode:
                orientation = constants.MANGA_ORIENTATION
            else:
                orientation = constants.WESTERN_ORIENTATION

            # Rotation handling:
            # - apply Exif rotation on individual images
            # - apply automatic rotation (size based) on whole page
            # - apply manual rotation on whole page
            if prefs['auto rotate from exif']:
                rotation_list = [image_tools.get_implied_rotation(pixbuf)
                                 for pixbuf in pixbuf_list]
            else:
                rotation_list = [0] * len(pixbuf_list)
            virtual_size = [0, 0]
            for i in range(pixbuf_count):
                if tools.rotation_swaps_axes(rotation_list[i]):
                    size_list[i].reverse()
                size = size_list[i]
                virtual_size[distribution_axis] += size[distribution_axis]
                virtual_size[alignment_axis] = max(virtual_size[alignment_axis],
                                                   size[alignment_axis])
            rotation = tools.compile_rotations(
                image_tools.get_size_rotation(*virtual_size), prefs['rotation'])
            if tools.rotation_swaps_axes(rotation):
                distribution_axis, alignment_axis = alignment_axis, distribution_axis
                orientation = list(orientation)
                orientation.reverse() # 2D only
                for i in range(pixbuf_count):
                    if do_not_transform[i]:
                        continue
                    size_list[i].reverse() # 2D only
            if rotation in (180, 270):
                orientation = tools.vector_opposite(orientation)
            for i in range(pixbuf_count):
                rotation_list[i] = tools.compile_rotations(rotation_list[i], rotation)
            if prefs['vertical flip'] and tools.rotation_swaps_axes(rotation):
                orientation = tools.vector_opposite(orientation)
            if prefs['horizontal flip'] and not tools.rotation_swaps_axes(rotation):
                orientation = tools.vector_opposite(orientation)

            def zoomed_size(viewport: tuple) -> tuple:
                sizes, distorted = cast(Tuple[List[List[int]], List[bool]], self.zoom.get_zoomed_size(
                    size_list, viewport, distribution_axis, do_not_transform,
                    prefs['double page autoresize'] in (
                        constants.DOUBLE_PAGE_AUTORESIZE_SIZE,
                        constants.DOUBLE_PAGE_AUTORESIZE_FIT_SIZE),
                    prefs['double page autoresize'] == constants.DOUBLE_PAGE_AUTORESIZE_FIT_SIZE))
                for index, pixbuf in enumerate(pixbuf_list):
                    if getattr(pixbuf, 'webp_animation', None) is not None:
                        if not prefs['enlarge animated webp']:
                            sizes[index] = list(image_tools.get_fitting_size(
                                size_list[index], sizes[index], scale_up=False))
                            distorted[index] = False
                        limit = cast(int, prefs['animated webp max size'])
                        if limit > 0 and max(sizes[index]) > limit:
                            sizes[index] = list(image_tools.get_fitting_size(
                                size_list[index], tuple(min(edge, limit) for edge in sizes[index]), scale_up=True))
                            distorted[index] = False
                return sizes, distorted

            self.layout = layout.FiniteLayout.create_finite_layout(
                pixbuf_count, orientation, prefs['space between two pages'], distribution_axis,
                alignment_axis, self._show_scrollbars, self.get_visible_area_size,
                zoomed_size)
            content_boxes = self.layout.get_content_boxes()
            scaled_sizes = list(map(box.Box.get_size, content_boxes))

            upscaler = self.imagehandler.upscaler
            first_page = self.imagehandler.get_current_page()
            animations = self.imagehandler.animations
            animations.configure([
                (i, (self.imagehandler._page_generation, first_page + i), pixbuf,
                 (tuple(scaled_sizes[i]), rotation_list[i],
                  prefs['horizontal flip'], prefs['vertical flip'],
                  self.enhancer.settings_key(), prefs['scaling quality'],
                  prefs['checkered bg for transparent images']))
                for i, pixbuf in enumerate(pixbuf_list)
                if getattr(pixbuf, 'webp_animation', None) is not None])
            self._upscale_keys = [self._upscale_key(first_page + i)
                                  for i in range(pixbuf_count)]
            next_pages = range(first_page + pixbuf_count, first_page + 2 * pixbuf_count)
            upscaler.set_wanted(self._upscale_keys + list(map(self._upscale_key, next_pages)))
            self.transforms = [Transform.ID] * pixbuf_count
            for i in range(pixbuf_count):
                if do_not_transform[i]:
                    continue
                self.transforms[i] += Transform.from_rotation(rotation_list[i]) # FIXME also include scales
                displayed = animations.displayed(i)
                if displayed is not None:
                    pixbuf_list[i] = displayed
                    continue
                if getattr(pixbuf_list[i], 'webp_animation', None) is not None:
                    upscaled = image_tools.fit_pixbuf_to_rectangle(
                        pixbuf_list[i], scaled_sizes[i], rotation_list[i])
                else:
                    upscaled = upscaler.render(self._upscale_keys[i], pixbuf_list[i],
                                               scaled_sizes[i], rotation_list[i])
                    if upscaled is None:
                        upscaled = self.imagehandler.render_conventional(
                            pixbuf_list[i], scaled_sizes[i], rotation_list[i])
                pixbuf_list[i] = upscaled
            for page in next_pages:
                self._prefetch_upscaling(page)

            for i in range(pixbuf_count):
                if do_not_transform[i]:
                    continue
                already_rendered = animations.displayed(i) is not None
                if prefs['horizontal flip']:
                    if not already_rendered:
                        pixbuf_list[i] = image_tools.flip_pixbuf(pixbuf_list[i], 0)
                    self.transforms[i] += Transform.from_flips(True, False)
                if prefs['vertical flip']:
                    if not already_rendered:
                        pixbuf_list[i] = image_tools.flip_pixbuf(pixbuf_list[i], 1)
                    self.transforms[i] += Transform.from_flips(False, True)
                if not already_rendered:
                    pixbuf_list[i] = self.enhancer.enhance(pixbuf_list[i])

            for i in range(pixbuf_count):
                if i in animations.pages:
                    animations.pages[i].display = pixbuf_list[i]
                image_tools.set_from_pixbuf(self.images[i], pixbuf_list[i])

            scales = tuple(map(lambda x, y: math.sqrt(tools.div(
                tools.volume(x), tools.volume(y))), scaled_sizes, size_list))

            resolutions = tuple(map(lambda sz, sc, ds: sz + [sc, ds], size_list,
                scales, self.layout.get_content_distorted()))
            if self.is_manga_mode:
                resolutions = tuple(reversed(resolutions))
            self.statusbar.set_resolution(resolutions)
            self.statusbar.update()

            smartbg = prefs['smart bg']
            smartthumbbg = prefs['smart thumb bg'] and prefs['show thumbnails']
            if smartbg or smartthumbbg:
                bg_colour = self.imagehandler.get_pixbuf_auto_background(pixbuf_count)
            if smartbg:
                self.set_bg_colour(bg_colour)
            if smartthumbbg:
                self.thumbnailsidebar.change_thumbnail_background_color(bg_colour)

            bin_window = self._main_layout.get_bin_window()
            bin_window.freeze_updates()
            try:
                self._main_layout.set_size(*(self.layout.get_union_box().get_size()))
                for i in range(pixbuf_count):
                    self._main_layout.move(self.images[i],
                        *content_boxes[i].get_position())

                for i in range(pixbuf_count):
                    self.images[i].show()
                for i in range(pixbuf_count, len(self.images)):
                    self.images[i].hide()

                # Reset orientation so scrolling behaviour is sane.
                if self.is_manga_mode:
                    self.layout.set_orientation(constants.MANGA_ORIENTATION)
                else:
                    self.layout.set_orientation(constants.WESTERN_ORIENTATION)

                if scroll_to is not None:
                    destination = (scroll_to,) * 2
                    if constants.SCROLL_TO_START == scroll_to:
                        index = constants.FIRST_INDEX
                    elif constants.SCROLL_TO_END == scroll_to:
                        index = constants.LAST_INDEX
                    else:
                        index = None
                    self.scroll_to_predefined(destination, index)
            finally:
                bin_window.thaw_updates()
            # The OSD paints at the scroll position, so wait for the layout.
            if self._pending_notice:
                self.statusbar.set_message(self._pending_notice)
                self.osd.show(self._pending_notice)
                self._pending_notice = ''
        else:
            self.imagehandler.stop_animations()
            # A decoded wide current page can become drawable before its
            # prospective partner is extracted. Track both kinds of wait.
            self._pending_decode_redraw = (self.imagehandler._page_generation,
                                           self.imagehandler.get_current_page(), scroll_to)
            # If the pixbuf for the current page(s) isn't available,
            # hide all images to clear any old pixbufs.
            # XXX How about calling self._clear_main_area?
            for i in range(len(self.images)):
                self.images[i].hide()
            self._show_scrollbars([False] * len(self._scroll))

    def _update_page_information(self) -> None:
        """ Updates the window with information that can be gathered
        even when the page pixbuf(s) aren't ready yet. """

        page_number = self.imagehandler.get_current_page()
        if not page_number:
            return
        double = self.displayed_double()

        def make_status(info):
            if not isinstance(info, tuple):
                return info
            if self.is_manga_mode:
                info = reversed(info)
            return ", ".join(info)

        filename = make_status(self.imagehandler.get_page_filename(double=double))
        filesize = make_status(self.imagehandler.get_page_filesize(double=double))
        self.statusbar.set_page_number(page_number,
                                       self.imagehandler.get_number_of_pages(),
                                       2 if double else 1)
        self.statusbar.set_filename(filename)
        self.statusbar.set_root(self.filehandler.get_base_filename())
        self.statusbar.set_filesize(filesize)
        self.statusbar.update()
        self.update_title()

    def update_icon(self, default=False):
        if (self.filehandler.archive_type is not None
            and prefs['archive thumbnail as icon']):
            pixbuf = self.imagehandler.get_thumbnail(1, 48, 48)
            pixbuf = self.enhancer.enhance(pixbuf)
            self.set_icon(pixbuf)
        elif (default):
            self.set_icon_list(icons.mcomix_icons())

    def _page_available(self, page):
        """ Called whenever a new page is ready for displaying. """
        # Refresh display when currently opened page becomes available.
        current_page = self.imagehandler.get_current_page()
        nb_pages = 2 if self.displayed_double() else 1
        if current_page <= page < (current_page + nb_pages):
            pending = self._pending_decode_redraw
            if pending is not None and pending[:2] == (self.imagehandler._page_generation, current_page):
                scroll_to = pending[2]
            else:
                # No redraw is waiting for this page: keep the reader's position.
                scroll_to = None
            self.draw_image(scroll_to=scroll_to)
            self._update_page_information()

        # Use first page as application icon when opening archives.
        if page == 1:
            self.update_icon(False)

    def _page_upscaled(self, key, image):
        """ Called when the upscaler has processed a page. """
        if image is not None:
            self._page_rendered(key)

    def _page_rendered(self, key: upscale.PageKey) -> None:
        """Refresh a visible neural rendering without replacing pending navigation."""
        if not self.filehandler.file_loaded or self._pending_decode_redraw is not None:
            return
        current = self.imagehandler.get_current_page()
        count = 2 if self.displayed_double() else 1
        if key in self._upscale_keys and key in [self._upscale_key(current + i) for i in range(count)]:
            self.draw_image()

    def _page_cached(self, page):
        """ Called when a page has been decoded. """
        if not self.filehandler.file_loaded:
            return
        current = self.imagehandler.get_current_page()
        pending = self._pending_decode_redraw
        if pending is not None and pending[:2] == (self.imagehandler._page_generation, current):
            if current <= page <= current + 1:
                self.draw_image(scroll_to=pending[2])
                self._update_page_information()
        # Prefetch the next pages, also when they are decoded after the
        # current ones have been drawn.
        first_page = self.imagehandler.get_current_page() + len(self._upscale_keys)
        if first_page <= page < first_page + len(self._upscale_keys):
            self._prefetch_upscaling(page)

    def _upscale_key(self, page):
        return self.imagehandler.upscaler.page_key(self.imagehandler.get_path_to_page(page))

    def _prefetch_upscaling(self, page):
        """ Requests neural upscaling of <page> in the background, if it is
        decoded. """
        pixbuf = self.imagehandler.get_cached_pixbuf(page)
        if pixbuf is not None:
            self.imagehandler.upscaler.prefetch(self._upscale_key(page), pixbuf)

    def show_notice(self, text: str) -> None:
        """Show text in the status bar and OSD once the page is drawn.

        Drawing clears the OSD and replaces the status bar text, so a notice
        given while a book opens would vanish before the user saw it.
        """
        self._pending_notice = text

    def _upscale_model_failed(self, path, message):
        self.osd.show(_('Neural upscaling disabled: %s') % message)

    def _on_file_opened(self):
        self.uimanager.set_sensitivities()
        number, count = self.filehandler.get_file_number()
        self.statusbar.set_file_number(number, count)
        self.statusbar.update()

    def _on_file_closed(self):
        self.clear()
        self.thumbnailsidebar.hide()
        self.thumbnailsidebar.clear()
        self.uimanager.set_sensitivities()
        self.set_icon_list(icons.mcomix_icons())

    def new_page(self, at_bottom=False):
        """Draw a *new* page correctly (as opposed to redrawing the same
        image with a new size or whatever).
        """
        if not prefs['keep transformation']:
            prefs['rotation'] = 0
            prefs['horizontal flip'] = False
            prefs['vertical flip'] = False

        if at_bottom:
            scroll_to = constants.SCROLL_TO_END
        else:
            scroll_to = constants.SCROLL_TO_START

        self.draw_image(scroll_to=scroll_to)

    @callback.Callback
    def page_changed(self):
        """ Called on page change. """
        self.thumbnailsidebar.load_thumbnails()
        self._update_page_information()

    def set_page(self, num, at_bottom=False):
        if num == self.imagehandler.get_current_page():
            return
        self.imagehandler.set_page(num)
        self.page_changed()
        self.new_page(at_bottom=at_bottom)
        self.slideshow.update_delay()

    def next_book(self):
        self._change_book(True)

    def previous_book(self):
        self._change_book(False)

    def _change_book(self, forward: bool) -> None:
        archive_open = self.filehandler.archive_type is not None
        archive_opened = False
        if (self.slideshow.is_running() and
                prefs['slideshow can go to next archive']) or \
                prefs['auto open next archive']:
            if forward:
                archive_opened = self.filehandler._open_next_archive()
            else:
                archive_opened = self.filehandler._open_previous_archive()

        # If "Auto open next archive" is disabled, do not change
        # directory if current file was an archive.
        if not archive_opened and \
                prefs['auto open next directory'] and \
                (not archive_open or prefs['auto open next archive']):
            if forward:
                self.filehandler.open_next_directory()
            else:
                self.filehandler.open_previous_directory()

    def flip_page(self, step, single_step=False):

        if not self.filehandler.file_loaded:
            return

        current_page = self.imagehandler.get_current_page()
        number_of_pages = self.imagehandler.get_number_of_pages()

        new_page = current_page + step
        if (1 == abs(step) and
            not single_step and
            prefs['default double page'] and
            prefs['double step in double page mode']):
            if +1 == step and not self.imagehandler.get_virtual_double_page():
                new_page += 1
            elif -1 == step and not self.imagehandler.get_virtual_double_page(new_page - 1):
                new_page -= 1

        if new_page <= 0:
            # Only switch to previous page when flipping one page before the
            # first one. (Note: check for (page number <= 1) to handle empty
            # archive case).
            if -1 == step and current_page <= 1:
                return self.previous_book()
            # Handle empty archive case.
            new_page = min(1, number_of_pages)
        elif new_page > number_of_pages:
            if 1 == step:
                return self.next_book()
            new_page = number_of_pages

        if new_page != current_page:
            self.set_page(new_page, at_bottom=(-1 == step))

    def first_page(self):
        number_of_pages = self.imagehandler.get_number_of_pages()
        if number_of_pages:
            self.set_page(1)

    def last_page(self):
        number_of_pages = self.imagehandler.get_number_of_pages()
        if number_of_pages:
            self.set_page(number_of_pages)

    def page_select(self, *args):
        pageselect.Pageselector(self)

    def rotate_90(self, *args):
        prefs['rotation'] = tools.compile_rotations(prefs['rotation'], 90)
        self.draw_image()

    def rotate_180(self, *args):
        prefs['rotation'] = tools.compile_rotations(prefs['rotation'], 180)
        self.draw_image()

    def rotate_270(self, *args):
        prefs['rotation'] = tools.compile_rotations(prefs['rotation'], 270)
        self.draw_image()

    def flip_horizontally(self, *args):
        prefs['horizontal flip'] = not prefs['horizontal flip']
        self.draw_image()

    def flip_vertically(self, *args):
        prefs['vertical flip'] = not prefs['vertical flip']
        self.draw_image()

    def change_double_page(self, toggleaction):
        prefs['default double page'] = toggleaction.get_active()
        self.imagehandler.do_cacheing()
        self._update_page_information()
        self.draw_image()

    def change_manga_mode(self, toggleaction):
        prefs['default manga mode'] = toggleaction.get_active()
        self.is_manga_mode = toggleaction.get_active()
        self._update_page_information()
        self.draw_image()

    def change_invert_scroll(self, toggleaction):
        prefs['invert smart scroll'] = toggleaction.get_active()

    @property
    def is_fullscreen(self):
        window_state = self.get_window().get_state()
        return 0 != (window_state & Gdk.WindowState.FULLSCREEN)

    def change_fullscreen(self, toggleaction):
        # Disable action until transition if complete.
        toggleaction.set_sensitive(False)
        if toggleaction.get_active():
            if self.previous_size != (None, None):
                self.save_window_geometry()
            self.fullscreen()
        else:
            self.unfullscreen()
        # No need to call draw_image explicitely,
        # as we'll be receiving a window state
        # change or resize event.

    def change_invert_color(self, toggleaction):
        prefs['invert color'] = not self.enhancer.invert_color
        self.enhancer.invert_color = prefs['invert color']
        self.enhancer.signal_update()

    def change_zoom_mode(self, radioaction=None, *args):
        if radioaction:
            prefs['zoom mode'] = radioaction.get_current_value()
        self.zoom.set_fit_mode(prefs['zoom mode'])
        self.zoom.set_scale_up(prefs['stretch'])
        self.zoom.reset_user_zoom()
        self.draw_image()

    def change_autorotation(self, radioaction=None, *args):
        """ Switches between automatic rotation modes, depending on which
        radiobutton is currently activated. """
        if radioaction:
            prefs['auto rotate depending on size'] = radioaction.get_current_value()
        self.draw_image()

    def change_stretch(self, toggleaction, *args):
        """ Toggles stretching small images. """
        prefs['stretch'] = toggleaction.get_active()
        self.zoom.set_scale_up(prefs['stretch'])
        self.draw_image()

    def change_toolbar_visibility(self, toggleaction):
        self._update_toggle_preference('show toolbar', toggleaction)

    def change_menubar_visibility(self, toggleaction):
        self._update_toggle_preference('show menubar', toggleaction)

    def change_statusbar_visibility(self, toggleaction):
        self._update_toggle_preference('show statusbar', toggleaction)

    def change_scrollbar_visibility(self, toggleaction):
        self._update_toggle_preference('show scrollbar', toggleaction)

    def change_thumbnails_visibility(self, toggleaction):
        self._update_toggle_preference('show thumbnails', toggleaction)

    def change_hide_all(self, toggleaction):
        self._update_toggle_preference('hide all', toggleaction)

    def change_keep_transformation(self, *args):
        prefs['keep transformation'] = not prefs['keep transformation']

    def manual_zoom_in(self, *args):
        self.zoom.zoom_in()
        self.draw_image()

    def manual_zoom_out(self, *args):
        self.zoom.zoom_out()
        self.draw_image()

    def manual_zoom_original(self, *args):
        self.zoom.reset_user_zoom()
        self.draw_image()

    def _show_scrollbars(self, request):
        """ Enables scroll bars depending on requests and preferences. """

        limit = self._should_toggle_be_visible('show scrollbar')
        for i in range(len(self._scroll)):
            if limit and request[i]:
                self._scroll[i].show()
            else:
                self._scroll[i].hide()

    def is_scrollable(self):
        """ Returns True if the current images do not fit into the viewport. """
        return not all(tools.smaller_or_equal(self.layout.get_union_box().get_size(),
            self.get_visible_area_size()))

    def scroll_with_flipping(self, x, y):
        """Returns true if able to scroll without flipping to
        a new page and False otherwise."""
        return self._event_handler._scroll_with_flipping(x, y)

    def scroll(self, x, y):
        """Scroll <x> px horizontally and <y> px vertically.

        Return True if call resulted in new adjustment values, False
        otherwise.
        """
        old_hadjust = self._hadjust.get_value()
        old_vadjust = self._vadjust.get_value()

        visible_width, visible_height = self.get_visible_area_size()

        hadjust_upper = max(0, self._hadjust.get_upper() - visible_width)
        vadjust_upper = max(0, self._vadjust.get_upper() - visible_height)

        new_hadjust = old_hadjust + x
        new_vadjust = old_vadjust + y

        new_hadjust = max(0, new_hadjust)
        new_vadjust = max(0, new_vadjust)

        new_hadjust = min(hadjust_upper, new_hadjust)
        new_vadjust = min(vadjust_upper, new_vadjust)

        self._vadjust.set_value(new_vadjust)
        self._hadjust.set_value(new_hadjust)
        self._scroll[0].queue_resize_no_redraw()
        self._scroll[1].queue_resize_no_redraw()

        return old_vadjust != new_vadjust or old_hadjust != new_hadjust

    def scroll_to_predefined(self, destination, index=None):
        self.layout.scroll_to_predefined(destination, index)
        self.update_viewport_position()

    def update_viewport_position(self):
        viewport_position = self.layout.get_viewport_box().get_position()
        self._hadjust.set_value(viewport_position[0]) # 2D only
        self._vadjust.set_value(viewport_position[1]) # 2D only
        self._scroll[0].queue_resize_no_redraw()
        self._scroll[1].queue_resize_no_redraw()

    def update_layout_position(self):
        self.layout.set_viewport_position(
            (int(round(self._hadjust.get_value())), int(round(self._vadjust.get_value()))))

    def clear(self):
        """Clear the currently displayed data (i.e. "close" the file)."""
        self.set_title(constants.APPNAME)
        self.statusbar.set_message('')
        self._pending_notice = ''
        self.draw_image()

    def _clear_main_area(self):
        for i in self.images:
            i.hide()
        for i in self.images:
            i.clear()
        self._show_scrollbars([False] * len(self._scroll))
        self.layout = layout.create_dummy_layout()
        self._main_layout.set_size(*self.layout.get_union_box().get_size())
        self.set_bg_colour(prefs['bg colour'])

    def displayed_double(self):
        """Return True if two pages are currently displayed."""
        return (self.imagehandler.get_current_page() and
                prefs['default double page'] and
                not self.imagehandler.get_virtual_double_page(cached_only=True) and
                self.imagehandler.get_current_page() != self.imagehandler.get_number_of_pages())

    def get_visible_area_size(self):
        """Return a 2-tuple with the width and height of the visible part
        of the main layout area.
        """
        dimensions = list(self.get_size())

        for preference, action, widget_list in self._toggle_list:
            for widget in widget_list:
                if widget.get_visible():
                    axis = self._toggle_axis[widget]
                    requisition = widget.size_request()
                    if constants.PageAxis.WIDTH == axis:
                        size = requisition.width
                    elif constants.PageAxis.HEIGHT == axis:
                        size = requisition.height
                    dimensions[axis] -= size

        return tuple(dimensions)

    def set_cursor(self, mode):
        """Set the cursor on the main layout area to <mode>. You should
        probably use the cursor_handler instead of using this method
        directly.
        """
        self._main_layout.get_bin_window().set_cursor(mode)

    def update_title(self):
        """Set the title acording to current state."""
        this_screen = 2 if self.displayed_double() else 1 # XXX limited to at most 2 pages
        # TODO introduce formatter to merge these string ops with the ops for status bar updates
        title = '['
        for i in range(this_screen):
            title += '%d' % (self.imagehandler.get_current_page() + i)
            if i < this_screen - 1:
                title += ','
        title += ' / %d]  %s' % (self.imagehandler.get_number_of_pages(),
            self.imagehandler.get_pretty_current_filename())

        if self.slideshow.is_running():
            title = '[%s] %s' % (_('SLIDESHOW'), title)

        self.set_title(i18n.to_display_string(title))

    def set_bg_colour(self, colour):
        """Set the background colour to <colour>. Colour is a sequence in the
        format (r, g, b). Values are 16-bit.
        """
        colour = colour[:3]
        self._event_box.modify_bg(Gtk.StateType.NORMAL, Gdk.Color(*colour))
        self._bg_colour = colour

    def get_bg_colour(self):
        return self._bg_colour

    def extract_page(self, *args):
        """Save the currently displayed images to disk, appending a number if a
        file with an identical name was already found in the target directory.
        """
        this_screen = 2 if self.displayed_double() else 1 # XXX limited to at most 2 pages
        for i in reversed(range(this_screen)) if self.is_manga_mode \
        else range(this_screen):
            file_path = self.imagehandler.get_path_to_page(
                self.imagehandler.get_current_page() + i)
            if not file_path:
                return
            file_name = os.path.split(file_path)[-1]

            if self.filehandler.archive_type is not None:
                # Prepend the archive base name to the filename being displayed
                archive_name = self.imagehandler.get_pretty_current_filename()
                file_name = (
                    os.path.splitext(archive_name)[0] + '_' + file_name)

            target_dir = prefs['path of last saved in filechooser'] + os.sep
            suggest_name = file_name
            attempt = 1
            while os.path.exists(target_dir + suggest_name):
                suggest_name = tools.append_number_to_filename(
                    file_name, number=attempt)
                attempt += 1

            save_dialog = Gtk.FileChooserDialog(_('Save page as'), self,
                Gtk.FileChooserAction.SAVE,
                (Gtk.STOCK_OK, Gtk.ResponseType.ACCEPT,
                Gtk.STOCK_CANCEL, Gtk.ResponseType.REJECT)
            )
            save_dialog.set_do_overwrite_confirmation(True)
            save_dialog.set_create_folders(True)
            save_dialog.set_current_name(suggest_name)
            save_dialog.set_current_folder(target_dir)

            if save_dialog.run() == Gtk.ResponseType.ACCEPT:
                target = save_dialog.get_filename()
                if target:
                    try:
                        if self.filehandler.archive_type is not None:
                            # Archive pages might be read on demand. Read
                            # first: opening <target> truncates it.
                            data = self.filehandler.read_file(file_path)
                            with open(target, 'wb') as fp:
                                fp.write(data)
                        else:
                            shutil.copy2(file_path, target)
                    except Exception as e:
                        log.warning(e)

                prefs['path of last saved in filechooser'] = \
                    save_dialog.get_current_folder() \
                    if prefs['store last saved in directory'] \
                    else constants.HOME_DIR

            save_dialog.destroy()

    def delete(self, *args):
        """ The currently opened file/archive will be deleted after showing
        a confirmation dialog. """

        current_file = self.imagehandler.get_real_path()
        dialog = message_dialog.MessageDialog(self, Gtk.DialogFlags.MODAL, Gtk.MessageType.QUESTION,
                Gtk.ButtonsType.NONE)
        dialog.set_should_remember_choice('delete-opend-file', (Gtk.ResponseType.OK,))
        dialog.set_text(
                _('Delete "%s"?') % os.path.basename(current_file),
                _('The file will be deleted from your harddisk.'))
        dialog.add_button(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL)
        dialog.add_button(Gtk.STOCK_DELETE, Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        result = dialog.run()

        if result == Gtk.ResponseType.OK:
            # Go to next page/archive, and delete current file
            if self.filehandler.archive_type is not None:
                self.filehandler.last_read_page.clear_page(current_file)

                next_opened = self.filehandler._open_next_archive()
                if not next_opened:
                    next_opened = self.filehandler._open_previous_archive()
                if not next_opened:
                    self.filehandler.close_file()

                self.filehandler.wait_for_file_release(current_file)
                if os.path.isfile(current_file):
                    os.unlink(current_file)
            else:
                if self.imagehandler.get_number_of_pages() > 1:
                    # Open the next/previous file: one page, so that a double
                    # step never leaves the book.
                    if self.imagehandler.get_current_page() >= self.imagehandler.get_number_of_pages():
                        self.flip_page(-1, single_step=True)
                    else:
                        self.flip_page(+1, single_step=True)
                    # Unlink the desired file
                    if os.path.isfile(current_file):
                        os.unlink(current_file)
                    # Refresh the directory
                    self.filehandler.refresh_file()
                else:
                    self.filehandler.close_file()
                    if os.path.isfile(current_file):
                        os.unlink(current_file)

    def show_info_panel(self):
        """ Shows an OSD displaying information about the current page. """

        if not self.filehandler.file_loaded:
            return

        text = ''
        filename = self.imagehandler.get_pretty_current_filename()
        if filename:
            text += '%s\n' % filename
        file_number, file_count = self.filehandler.get_file_number()
        if file_count:
            text += '(%d / %d)\n' % (file_number, file_count)
        else:
            text += '\n'
        page_number = self.imagehandler.get_current_page()
        number_of_pages = self.imagehandler.get_number_of_pages()
        if page_number:
            text += '%s %d / %d' % (_('Page'), page_number, number_of_pages)
        text = text.strip('\n')
        if text:
            self.osd.show(text)

    def minimize(self, *args):
        """ Minimizes the MComix window. """
        self.iconify()

    def _open_startup_file(self, open_path: Optional[Union[str, List[str]]],
                           open_page: int = 0, open_entry: str = '') -> None:
        """Prefer explicit paths, then Save and quit, then automatic restore.

        An ordinary open uses page zero and the history prompt policy. Saved
        startup positions are explicit pages, even when history is disabled.
        open_page and open_entry (command line) apply only to open_path.
        """
        restore_saved = prefs['previous quit was quit and save']
        prefs['previous quit was quit and save'] = False
        open_image = ''
        if open_path is None and restore_saved:
            fileinfo = self.filehandler.read_fileinfo_file()
            if fileinfo is not None and os.path.exists(fileinfo[0]):
                open_path, page_index, open_image = fileinfo
                open_page = page_index + 1
        last_path = prefs['path to last file']
        if open_path is None and prefs['auto load last file'] and \
                isinstance(last_path, str) and os.path.exists(last_path):
            open_path = last_path
            saved_page = prefs['page of last file']
            open_page = saved_page if type(saved_page) is int and saved_page > 0 else 1
            saved_image = prefs['image of last file']
            open_image = saved_image if isinstance(saved_image, str) else ''
        if open_path is not None:
            self.filehandler.open_file(open_path, start_page=open_page,
                                       start_image=open_image, start_entry=open_entry)

    def write_config_files(self) -> None:
        """Write each config file on its own, so one failure loses only it.
        Bookmarks are saved by each change."""
        for write in (self.filehandler.write_fileinfo_file,
                      preferences.write_preferences_file,
                      keybindings.keybinding_manager(self).save):
            try:
                write()
            except Exception as error:
                log.error(_('! Could not save settings: %s'), error)

    def save_and_terminate_program(self, *args):
        prefs['previous quit was quit and save'] = True

        self.terminate_program()

    def get_window_geometry(self):
        return self.get_position() + self.get_size()

    def save_window_geometry(self) -> None:
        x, y, width, height = self.get_window_geometry()
        prefs['window x'] = x
        prefs['window y'] = y
        prefs['window width'] = width
        prefs['window height'] = height
        prefs['window maximized'] = self.is_maximized()

    def restore_window_geometry(self):
        if self.get_window_geometry() == (prefs['window x'],
                                          prefs['window y'],
                                          prefs['window width'],
                                          prefs['window height']) \
           and self.is_maximized() == prefs['window maximized']:
            return False

        self.move(prefs['window x'], prefs['window y'])
        if prefs['window maximized']:
            self.maximize()
        else:
            self.resize(prefs['window width'], prefs['window height'])
        return True

    def update_space(self):
        self.draw_image()

    def close_program(self, *args):
        if not self.is_fullscreen:
            self.save_window_geometry()
        self.terminate_program()

    def terminate_program(self) -> None:
        """Fence new opens and wait for an active open before cleanup."""
        if self._terminating:
            return
        self._terminating = True
        self.filehandler.begin_shutdown(self._terminate_program)
        # An active open may be inside Gtk.Dialog.run(). Unwind its modal
        # loop now, so the deferred cleanup can run when that open returns.
        self._close_transient_dialogs()

    def _close_transient_dialogs(self) -> None:
        """Destroy owned dialogs, stopping their workers before file cleanup."""
        resume_dialog = self.filehandler._resume_dialog
        for dialog in Gtk.Window.list_toplevels():
            # FileHandler has sent CANCEL; its MessageDialog.run() still
            # reads the remember checkbox before destroying itself.
            if dialog is resume_dialog:
                continue
            parent = dialog.get_transient_for()
            while parent is not None and parent is not self:
                parent = parent.get_transient_for()
            if parent is self:
                dialog.destroy()

    def _terminate_program(self) -> None:
        """Stop owned workers before releasing their files and database."""
        self._close_transient_dialogs()

        self.hide()

        if Gtk.main_level() > 0:
            Gtk.main_quit()

        if prefs['auto load last file'] and self.filehandler.file_loaded:
            (prefs['path to last file'], prefs['page of last file'],
             prefs['image of last file']) = self.filehandler.get_book_position()

        else:
            prefs['path to last file'] = ''
            prefs['page of last file'] = 1
            prefs['image of last file'] = ''

        self.write_config_files()

        self.filehandler.close_file()
        # The upscaler outlives individual books and needs an explicit stop.
        self.imagehandler.upscaler.stop()
        if main_dialog._dialog is not None:
            main_dialog._dialog.close()
        backend.LibraryBackend().close()

        # Every persistent worker is stopped by its owner above. Finite
        # non-daemon packing/deletion threads retain normal Python exit
        # semantics; unrelated daemon jobs are not part of viewer shutdown.


#: Main window instance
__main_window = None


def main_window():
    """ Returns the global main window instance. """
    return __main_window


def set_main_window(window):
    global __main_window
    __main_window = window


# vim: expandtab:sw=4:ts=4
