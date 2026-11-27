"""image_tools.py - Various image manipulations."""

import itertools
import operator
from dataclasses import dataclass
from gi.repository import GLib, GdkPixbuf, Gdk, Gtk
import PIL
from PIL import Image
from PIL import ImageEnhance
from PIL import ImageOps
from PIL import features
from io import BytesIO
from typing import Optional, Sequence, Tuple, Union

from mcomix.preferences import prefs
from mcomix import constants
from mcomix import log
from mcomix import tools
from mcomix.i18n import _

PIL_VERSION = ('Pillow', PIL.__version__)


def _avif_supported() -> bool:
    try:
        return features.check_module('avif')
    except ValueError:
        # Pillow before 11.2 has no AVIF module.
        return False


# Unfortunately gdk_pixbuf_version is not exported, so show the GTK+ version instead.
log.info(f'GDK version: {GdkPixbuf.PIXBUF_VERSION}, GTK+: {Gtk.get_major_version()}.{Gtk.get_minor_version()}, GLib: {GLib.MAJOR_VERSION}.{GLib.MINOR_VERSION}')
log.info('PIL version: %s [%s], AVIF: %s', PIL_VERSION[0], PIL_VERSION[1], _avif_supported())

# Fallback pixbuf for missing images.
_missing_icon_dialog = Gtk.Dialog()
_missing_icon_pixbuf = _missing_icon_dialog.render_icon(
        Gtk.STOCK_MISSING_IMAGE, Gtk.IconSize.LARGE_TOOLBAR)
MISSING_IMAGE_ICON = _missing_icon_pixbuf
assert MISSING_IMAGE_ICON

GTK_GDK_COLOR_BLACK = Gdk.color_parse('black')
GTK_GDK_COLOR_WHITE = Gdk.color_parse('white')


def axis_to_gdkpixbuf_flip_horizontal(i):
    return (True, False)[i]

def angle_to_gdkpixbuf_rotation(deg):
    if deg == 0:
        return GdkPixbuf.PixbufRotation.NONE
    elif deg == 90:
        return GdkPixbuf.PixbufRotation.CLOCKWISE
    elif deg == 180:
        return GdkPixbuf.PixbufRotation.UPSIDEDOWN
    elif deg == 270:
        return GdkPixbuf.PixbufRotation.COUNTERCLOCKWISE
    raise ValueError("illegal angle: " + str(deg))

def rotate_pixbuf(src, rotation):
    return src if rotation == 0 else src.rotate_simple(angle_to_gdkpixbuf_rotation(rotation))

def flip_pixbuf(src, axis):
    return src.flip(horizontal=axis_to_gdkpixbuf_flip_horizontal(axis))

def get_fitting_size(source_size, target_size,
                     keep_ratio=True, scale_up=False):
    """ Return a scaled version of <source_size>
    small enough to fit in <target_size>.

    Both <source_size> and <target_size>
    must be (width, height) tuples.

    If <keep_ratio> is True, aspect ratio is kept.

    If <scale_up> is True, <source_size> is scaled up
    when smaller than <target_size>.
    """
    width, height = target_size
    src_width, src_height = source_size
    if not scale_up and src_width <= width and src_height <= height:
        width, height = src_width, src_height
    else:
        if keep_ratio:
            if float(src_width) / width > float(src_height) / height:
                height = int(max(src_height * width / src_width, 1))
            else:
                width = int(max(src_width * height / src_height, 1))
    return (width, height)


#: 'scaling quality' value for Lanczos resampling with Pillow, only used to
#: draw pages. It is GdkPixbuf's HYPER value, which GdkPixbuf deprecated
#: (lower quality than BILINEAR), so former "Hyperbolic" settings become Lanczos.
SCALING_LANCZOS = int(GdkPixbuf.InterpType.HYPER)
_LANCZOS = getattr(Image, 'Resampling', Image).LANCZOS


def gdk_scaling_quality(quality: Optional[int] = None) -> int:
    """ Returns the GdkPixbuf interpolation for scaling <quality> (default:
    the preference). Lanczos is only used for pages; elsewhere bilinear. """
    if quality is None:
        quality = prefs['scaling quality']
    return int(GdkPixbuf.InterpType.BILINEAR) if quality == SCALING_LANCZOS else quality


def fit_pixbuf_to_rectangle(src, rect, rotation):
    """ Resizes page <src> for display, with the scaling quality preference. """
    return fit_in_rectangle(src, rect[0], rect[1],
                            rotation=rotation,
                            keep_ratio=False,
                            scale_up=True,
                            scaling_quality=prefs['scaling quality'])

def fit_in_rectangle(src, width, height, keep_ratio=True, scale_up=False, rotation=0, scaling_quality=None):
    """Scale (and return) a pixbuf so that it fits in a rectangle with
    dimensions <width> x <height>.

    If <rotation> is 90, 180 or 270 we rotate <src> first so that the
    rotated pixbuf is fitted in the rectangle.

    Unless <scale_up> is True we don't stretch images smaller than the
    given rectangle.

    If <keep_ratio> is True, the image ratio is kept, and the result
    dimensions may be smaller than the target dimensions.

    If <src> has an alpha channel it gets a checkboard background.

    <scaling_quality> is a GdkPixbuf interpolation or SCALING_LANCZOS
    (opaque images only; others use bilinear). It defaults to the
    preference, with bilinear instead of Lanczos.
    """
    width = max(width, 1)
    height = max(height, 1)

    if tools.rotation_swaps_axes(rotation):
        width, height = height, width

    if scaling_quality is None:
        scaling_quality = gdk_scaling_quality()

    src_width = src.get_width()
    src_height = src.get_height()

    width, height = get_fitting_size((src_width, src_height),
                                     (width, height),
                                     keep_ratio=keep_ratio,
                                     scale_up=scale_up)

    if src.get_has_alpha():
        composite_color_args = get_composite_color_args(0
            if prefs['checkered bg for transparent images'] else 1)
        if width == src_width and height == src_height:
            # Using anything other than nearest interpolation will result in a
            # modified image if no resizing takes place (even if it's opaque).
            scaling_quality = GdkPixbuf.InterpType.NEAREST
        src = src.composite_color_simple(width, height, gdk_scaling_quality(scaling_quality),
                                         255, *composite_color_args)
    elif width != src_width or height != src_height:
        if scaling_quality == SCALING_LANCZOS:
            src = pil_to_pixbuf(pixbuf_to_pil(src).resize((width, height), _LANCZOS))
        else:
            src = src.scale_simple(width, height, scaling_quality)

    src = rotate_pixbuf(src, rotation)

    return src


def fit_pil_to_rectangle(im: Image.Image, rect: Sequence[int],
                         rotation: int) -> GdkPixbuf.Pixbuf:
    """ Same as fit_pixbuf_to_rectangle(), for an opaque PIL image,
    resized with LANCZOS. """
    width, height = max(int(rect[0]), 1), max(int(rect[1]), 1)
    if tools.rotation_swaps_axes(rotation):
        width, height = height, width
    if im.size != (width, height):
        im = im.resize((width, height), _LANCZOS)
    return rotate_pixbuf(pil_to_pixbuf(im), rotation)


def add_border(pixbuf, thickness, colour=0x000000FF):
    """Return a pixbuf from <pixbuf> with a <thickness> px border of
    <colour> added.
    """
    canvas = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8,
        pixbuf.get_width() + thickness * 2,
        pixbuf.get_height() + thickness * 2)
    canvas.fill(colour)
    pixbuf.copy_area(0, 0, pixbuf.get_width(), pixbuf.get_height(),
        canvas, thickness, thickness)
    return canvas


def get_most_common_edge_colour(pixbufs):
    """Return the most commonly occurring pixel value along the left and
    right edges of the static pixbuf <pixbufs>. The return value is a
    sequence, (r, g, b), with 16 bit values. If <pixbufs> is a tuple, the
    left edge is taken from the first image and the right edge from the
    second.

    Note: This could be done more cleanly with subpixbuf(), but that
    doesn't work as expected together with get_pixels().
    """

    def group_colors(colors):
        """ Rounds the (count, color) pairs in C{colors} to steps of 10,
        i.e. 128, 83, 10 becomes 130, 80, 10. This compensates for dirty colors
        where no clear dominating color can be made out.

        @return: The color that appears most often in the prominent group, the
        first run of colors with the same rounding that has the largest count."""
        groups = [list(group) for _, group in itertools.groupby(colors,
            key=lambda pair: tuple(min(255, (v + 5) // 10 * 10) for v in pair[1]))]
        prominent = max(groups, key=lambda group: sum(count for count, _ in group))
        return max(prominent, key=operator.itemgetter(0))[1]

    def get_edge_pixbuf(pixbuf, left):
        width = pixbuf.get_width()
        height = pixbuf.get_height()
        edge = min(2, width, height)
        subpix = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB,
                pixbuf.get_has_alpha(), 8, edge, height)
        pixbuf.copy_area(0 if left else width - edge, 0, edge, height, subpix, 0, 0)
        return subpix

    if not isinstance(pixbufs, (tuple, list)):
        pixbufs = (pixbufs, pixbufs)
    ungrouped_colors = []
    for pixbuf, left in zip(pixbufs, (True, False)):
        im = pixbuf_to_pil(get_edge_pixbuf(pixbuf, left))
        ungrouped_colors.extend(im.getcolors(im.size[0] * im.size[1]))

    ungrouped_colors.sort(key=operator.itemgetter(1))
    most_used = group_colors(ungrouped_colors)[:3]
    return [color * 257 for color in most_used]

def pil_to_pixbuf(im, keep_orientation=False):
    """Return a pixbuf created from the PIL <im>."""
    if im.mode.startswith('RGB'):
        has_alpha = im.mode == 'RGBA'
    elif im.mode in ('LA', 'P'):
        has_alpha = True
    else:
        has_alpha = False
    target_mode = 'RGBA' if has_alpha else 'RGB'
    if im.mode != target_mode:
        im = im.convert(target_mode)
    pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(im.tobytes()), GdkPixbuf.Colorspace.RGB,
        has_alpha, 8,
        im.size[0], im.size[1],
        (4 if has_alpha else 3) * im.size[0]
    )
    if keep_orientation:
        # Keep orientation metadata.
        exif = im.getexif()
        orientation = exif.get(274, None)
        if orientation is None:
            # Maybe it's a PNG? Try alternative method.
            orientation = _get_png_implied_rotation(im)
        if orientation is not None:
            setattr(pixbuf, 'orientation', str(orientation))
    return pixbuf

def pixbuf_to_pil(pixbuf):
    """Return a PIL image created from <pixbuf>."""
    dimensions = pixbuf.get_width(), pixbuf.get_height()
    stride = pixbuf.get_rowstride()
    pixels = pixbuf.get_pixels()
    mode = 'RGBA' if pixbuf.get_has_alpha() else 'RGB'
    im = Image.frombuffer(mode, dimensions, pixels, 'raw', mode, stride, 1)
    return im

def is_animation(pixbuf):
    return isinstance(pixbuf, GdkPixbuf.PixbufAnimation)

def static_image(pixbuf):
    """ Returns a non-animated version of the specified pixbuf. """
    if is_animation(pixbuf):
        return pixbuf.get_static_image()
    return pixbuf

def set_from_pixbuf(image, pixbuf):
    if is_animation(pixbuf):
        return image.set_from_animation(pixbuf)
    else:
        return image.set_from_pixbuf(pixbuf)

#: An image file path, or the contents of an image file.
ImageSource = Union[str, bytes]


@dataclass(frozen=True)
class WebPAnimation:
    source: ImageSource
    frames: int
    loops: int
    duration: float

def _describe_source(source: ImageSource) -> str:
    """ Returns a description of <source> for log messages. """
    if isinstance(source, bytes):
        return '%u bytes' % len(source)
    return source

def _open_pil(source: ImageSource) -> Image.Image:
    """ Opens <source> with PIL. """
    if isinstance(source, bytes):
        return Image.open(BytesIO(source))
    return Image.open(source)

def _gdk_loader(data: bytes,
                size: Optional[Tuple[int, int]] = None) -> GdkPixbuf.PixbufLoader:
    """ Feeds <data> to a new PixbufLoader and returns it once closed.
    If <size> is given, the image is decoded to fit in it (keeping the
    aspect ratio), which lets some loaders (e.g. JPEG) decode faster. """
    loader = GdkPixbuf.PixbufLoader()
    if size is not None:
        def size_prepared(loader, src_width, src_height):
            # Same rounding as GdkPixbuf.Pixbuf.new_from_file_at_size.
            width, height = size
            if height * src_width > width * src_height:
                height = int(0.5 + width * src_height / src_width)
            else:
                width = int(0.5 + height * src_width / src_height)
            loader.set_size(max(width, 1), max(height, 1))
        loader.connect('size-prepared', size_prepared)
    loader.write(data)
    loader.close()
    return loader

def _gdk_load_animation(source: ImageSource) -> GdkPixbuf.PixbufAnimation:
    if isinstance(source, bytes):
        animation = _gdk_loader(source).get_animation()
        if animation is None:
            raise GLib.Error('no animation in image data')
        return animation
    return GdkPixbuf.PixbufAnimation.new_from_file(source)

def _gdk_load(source: ImageSource) -> GdkPixbuf.Pixbuf:
    if isinstance(source, bytes):
        pixbuf = _gdk_loader(source).get_pixbuf()
        if pixbuf is None:
            raise GLib.Error('no image in image data')
        return pixbuf
    return GdkPixbuf.Pixbuf.new_from_file(source)

def _gdk_load_at_size(source: ImageSource, width: int, height: int) -> GdkPixbuf.Pixbuf:
    if isinstance(source, bytes):
        pixbuf = _gdk_loader(source, size=(width, height)).get_pixbuf()
        if pixbuf is None:
            raise GLib.Error('no image in image data')
        return pixbuf
    return GdkPixbuf.Pixbuf.new_from_file_at_size(source, width, height)

def load_pixbuf(source: ImageSource):
    """ Loads a pixbuf from a given image file path, or from
    the contents of an image file. """
    pixbuf = None
    last_error = None
    image_format, dimensions, providers = get_image_info(source)
    if image_format == 'WEBP':
        providers = (constants.IMAGEIO_PIL, constants.IMAGEIO_GDKPIXBUF)
    for provider in providers:
        try:
            # TODO use dynamic dispatch instead of "if" chain
            if provider == constants.IMAGEIO_GDKPIXBUF:
                if prefs['animation mode'] != constants.ANIMATION_DISABLED:
                    try:
                        pixbuf = _gdk_load_animation(source)
                        if pixbuf.is_static_image():
                            pixbuf = pixbuf.get_static_image()
                    except GLib.GError:
                        # NOTE: Broken JPEGs sometimes result in this exception.
                        # However, one may be able to load them using
                        # Gdk.pixbuf_new_from_file, so we need to continue.
                        pass
                if pixbuf is None:
                    pixbuf = _gdk_load(source)
            elif provider == constants.IMAGEIO_PIL:
                with _open_pil(source) as im:
                    pixbuf = pil_to_pixbuf(im, keep_orientation=True)
                    if im.format == 'WEBP' and getattr(im, 'is_animated', False):
                        pixbuf.webp_animation = WebPAnimation(
                            source, getattr(im, 'n_frames'), im.info.get('loop', 0),
                            max(1, im.info.get('duration', 100)) / 1000)
            else:
                raise TypeError()
        except Exception as e:
            # current provider could not load image
            last_error = e
        if pixbuf is not None:
            # stop loop on success
            log.debug("provider %s succeeded in loading %s", provider, _describe_source(source))
            break
        log.debug("provider %s failed to load %s", provider, _describe_source(source))
    if pixbuf is None:
        # raising necessary because caller expects pixbuf to be not None
        raise last_error or TypeError()
    return pixbuf

def load_pixbuf_size(source: ImageSource, width: int, height: int):
    """ Loads a pixbuf from a given image file path, or from the
    contents of an image file, and scale it to fit inside (width, height). """
    # TODO similar to load_pixbuf, should be merged using callbacks etc.
    pixbuf = None
    last_error = None
    image_format, image_dimensions, providers = get_image_info(source)
    for provider in providers:
        try:
            # TODO use dynamic dispatch instead of "if" chain
            if provider == constants.IMAGEIO_GDKPIXBUF:
                # If we could not get the image info, still try to load
                # the image to let GdkPixbuf raise the appropriate exception.
                if (0, 0) == image_dimensions:
                    pixbuf = _gdk_load(source)
                # Work around GdkPixbuf bug: https://bugzilla.gnome.org/show_bug.cgi?id=735422
                # (currently https://gitlab.gnome.org/GNOME/gdk-pixbuf/issues/45)
                elif 'GIF' == image_format:
                    pixbuf = _gdk_load(source)
                else:
                    # Don't upscale if smaller than target dimensions!
                    image_width, image_height = image_dimensions
                    if image_width <= width and image_height <= height:
                        width, height = image_width, image_height
                    pixbuf = _gdk_load_at_size(source, width, height)
            elif provider == constants.IMAGEIO_PIL:
                im = _open_pil(source)
                im.draft(None, (width, height))
                pixbuf = pil_to_pixbuf(im, keep_orientation=True)
            else:
                raise TypeError()
        except Exception as e:
            # current provider could not load image
            last_error = e
        if pixbuf is not None:
            # stop loop on success
            log.debug("provider %s succeeded in loading %s at size %s",
                      provider, _describe_source(source), (width, height))
            break
        log.debug("provider %s failed to load %s at size %s",
                  provider, _describe_source(source), (width, height))
    if pixbuf is None:
        # raising necessary because caller expects pixbuf to be not None
        raise last_error or TypeError()
    return fit_in_rectangle(pixbuf, width, height,
                            scaling_quality=GdkPixbuf.InterpType.BILINEAR)

def enhance(pixbuf, brightness=1.0, contrast=1.0, saturation=1.0,
  sharpness=1.0, autocontrast=False, invert_color=False):
    """Return a modified pixbuf from <pixbuf> where the enhancement operations
    corresponding to each argument has been performed. A value of 1.0 means
    no change. If <autocontrast> is True it overrides the <contrast> value,
    but only if the image mode is supported by ImageOps.autocontrast (i.e.
    it is L or RGB.)
    """
    im = pixbuf_to_pil(pixbuf)
    if brightness != 1.0:
        im = ImageEnhance.Brightness(im).enhance(brightness)
    if autocontrast and im.mode in ('L', 'RGB'):
        im = ImageOps.autocontrast(im, cutoff=0.1)
    elif contrast != 1.0:
        im = ImageEnhance.Contrast(im).enhance(contrast)
    if saturation != 1.0:
        im = ImageEnhance.Color(im).enhance(saturation)
    if sharpness != 1.0:
        im = ImageEnhance.Sharpness(im).enhance(sharpness)
    if invert_color:
        im = ImageOps.invert(im)
    return pil_to_pixbuf(im)

def _get_png_implied_rotation(pixbuf_or_image):
    """Same as <get_implied_rotation> for PNG files.

    Lookup for Exif data in the tEXt chunk.
    """
    if isinstance(pixbuf_or_image, GdkPixbuf.Pixbuf):
        raw_exif = pixbuf_or_image.get_option('tEXt::Raw profile type exif')
    elif isinstance(pixbuf_or_image, Image.Image):
        raw_exif = pixbuf_or_image.info.get('Raw profile type exif')
    else:
        raise ValueError()
    if raw_exif is None:
        return None
    exif_lines = raw_exif.split('\n')
    if len(exif_lines) < 4 or 'exif' != exif_lines[1]:
        # Not valid Exif data.
        return None
    try:
        size = int(exif_lines[2])
        data = bytes.fromhex(''.join(exif_lines[3:]))
        if size != len(data):
            return None
        exif = Image.Exif()
        exif.load(data)
        orientation = exif.get(274, None) # Orientation tag
    except Exception:
        # Pillow raises assorted errors (struct.error, SyntaxError, ...) on
        # malformed Exif; the page must still draw unrotated.
        return None
    if orientation is not None:
        orientation = str(orientation)
    return orientation

def get_implied_rotation(pixbuf):
    """Return the implied rotation in degrees: 0, 90, 180, or 270.

    The implied rotation is the angle (in degrees) that the raw pixbuf should
    be rotated in order to be displayed "correctly". E.g. a photograph taken
    by a camera that is held sideways might store this fact in its Exif data,
    and the pixbuf loader will set the orientation option correspondingly.
    """
    pixbuf = static_image(pixbuf)
    orientation = getattr(pixbuf, 'orientation', None)
    if orientation is None:
        orientation = pixbuf.get_option('orientation')
    if orientation is None:
        # Maybe it's a PNG? Try alternative method.
        orientation = _get_png_implied_rotation(pixbuf)
    if orientation == '3':
        return 180
    elif orientation == '6':
        return 90
    elif orientation == '8':
        return 270
    return 0


def get_size_rotation(width, height):
    """ Determines the rotation to be applied.
    Returns the degree of rotation (0, 90, 180, 270). """

    if width != height:
        arp = prefs['auto rotate depending on size']
        if height > width:
            if arp == constants.AUTOROTATE_HEIGHT_90:
                return 90
            elif arp == constants.AUTOROTATE_HEIGHT_270:
                return 270
        else: # width > height
            if arp == constants.AUTOROTATE_WIDTH_90:
                return 90
            elif arp == constants.AUTOROTATE_WIDTH_270:
                return 270
    return 0

def combine_pixbufs( pixbuf1, pixbuf2, are_in_manga_mode ):
    if are_in_manga_mode:
        r_source_pixbuf = pixbuf1
        l_source_pixbuf = pixbuf2
    else:
        l_source_pixbuf = pixbuf1
        r_source_pixbuf = pixbuf2

    has_alpha = False

    if l_source_pixbuf.get_property( 'has-alpha' ) or \
       r_source_pixbuf.get_property( 'has-alpha' ):
        has_alpha = True

    bits_per_sample = 8

    l_source_pixbuf_width = l_source_pixbuf.get_property( 'width' )
    r_source_pixbuf_width = r_source_pixbuf.get_property( 'width' )

    l_source_pixbuf_height = l_source_pixbuf.get_property( 'height' )
    r_source_pixbuf_height = r_source_pixbuf.get_property( 'height' )

    new_width = l_source_pixbuf_width + r_source_pixbuf_width

    new_height = max( l_source_pixbuf_height, r_source_pixbuf_height )

    new_pix_buf = GdkPixbuf.Pixbuf.new(colorspace=GdkPixbuf.Colorspace.RGB,
                                       has_alpha=has_alpha,
                                       bits_per_sample=bits_per_sample,
                                       width=new_width, height=new_height)

    l_source_pixbuf.copy_area( 0, 0, l_source_pixbuf_width,
                                     l_source_pixbuf_height,
                                     new_pix_buf, 0, 0 )

    r_source_pixbuf.copy_area( 0, 0, r_source_pixbuf_width,
                                     r_source_pixbuf_height,
                                     new_pix_buf, l_source_pixbuf_width, 0 )

    return new_pix_buf

def is_image_file(path):
    """Return True if the file at <path> is an image file recognized by PyGTK.
    """
    return _SUPPORTED_IMAGE_REGEX.search(path) is not None

def convert_rgb16list_to_rgba8int(c):
    return 0x000000FF | (c[0] >> 8 << 24) | (c[1] >> 8 << 16) | (c[2] >> 8 << 8)

def rgb_to_y_601(color):
    return color[0] * 0.299 + color[1] * 0.587 + color[2] * 0.114

def text_color_for_background_color(bgcolor):
    return GTK_GDK_COLOR_BLACK if rgb_to_y_601(bgcolor) >= \
        65535.0 / 2.0 else GTK_GDK_COLOR_WHITE

def color_to_floats_rgba(color, alpha=1.0):
    return [c / 65535.0 for c in color[:3]] + [alpha]

def get_composite_color_args(variant):
    return ((8, 0x777777, 0x999999), (1024, 0xFFFFFF, 0xFFFFFF))[variant]

_GDK_FORMATS = None

def _get_gdk_formats():
    """ Returns the set of (upper case) format names GdkPixbuf can load. """
    global _GDK_FORMATS
    if _GDK_FORMATS is None:
        _GDK_FORMATS = {fmt.get_name().upper()
                        for fmt in GdkPixbuf.Pixbuf.get_formats()}
    return _GDK_FORMATS

def _get_image_info_from_data(data):
    """ Same as get_image_info(), for the contents of an image file. """
    try:
        # Only the header is read.
        im = _open_pil(data)
        # Multi-picture JPEGs (e.g. from cameras) are still JPEGs.
        image_format = 'JPEG' if 'MPO' == im.format else im.format
        image_dimensions = im.size
    except Exception:
        im = None
    if im is not None:
        if image_format in _get_gdk_formats():
            providers = (constants.IMAGEIO_GDKPIXBUF, constants.IMAGEIO_PIL)
        else:
            providers = (constants.IMAGEIO_PIL, constants.IMAGEIO_GDKPIXBUF)
        return (image_format, image_dimensions, providers)
    try:
        # Unknown to PIL: decode with GdkPixbuf.
        loader = _gdk_loader(data)
        pixbuf = loader.get_pixbuf()
        return (loader.get_format().get_name().upper(),
                (pixbuf.get_width(), pixbuf.get_height()),
                (constants.IMAGEIO_GDKPIXBUF, constants.IMAGEIO_PIL))
    except Exception:
        return (_('Unknown filetype'), (0, 0), ())

def get_image_info(path):
    """Return information about and select preferred providers for loading
    the image specified by C{path}, an image file path or the contents
    of an image file. The result is a tuple
    C{(format, (width, height), providers)}.
    """
    if isinstance(path, bytes):
        return _get_image_info_from_data(path)
    image_format = None
    image_dimensions = None
    providers = ()
    try:
        gdk_image_info = GdkPixbuf.Pixbuf.get_file_info(path)
    except Exception:
        gdk_image_info = None

    if gdk_image_info is not None and gdk_image_info[0] is not None:
        image_format = gdk_image_info[0].get_name().upper()
        image_dimensions = gdk_image_info[1], gdk_image_info[2]
        # Prefer loading via GDK/Pixbuf if Gdk.pixbuf_get_file_info appears
        # to be able to handle this path.
        providers = (constants.IMAGEIO_GDKPIXBUF, constants.IMAGEIO_PIL)
    else:
        try:
            im = Image.open(path)
            image_format = im.format
            image_dimensions = im.size
            providers = (constants.IMAGEIO_PIL, constants.IMAGEIO_GDKPIXBUF)
        except Exception:
            # Unreadable, unidentified, or over Pillow's decompression bomb
            # limit, as in _get_image_info_from_data.
            pass
    if image_format is None:
        image_format = _('Unknown filetype')
        image_dimensions = (0, 0)
    return (image_format, image_dimensions, providers)

def get_supported_formats():
    global _SUPPORTED_IMAGE_FORMATS
    if _SUPPORTED_IMAGE_FORMATS is None:

        # Step 1: Collect PIL formats
        # Make sure all supported formats are registered.
        Image.init()
        # Not all PIL formats register a mime type,
        # fill in the blanks ourselves.
        supported_formats_pil = {
            'BMP': (['image/bmp', 'image/x-bmp', 'image/x-MS-bmp'], []),
            'ICO': (['image/x-icon', 'image/x-ico', 'image/x-win-bitmap'], []),
            'PCX': (['image/x-pcx'], []),
            'PPM': (['image/x-portable-pixmap'], []),
            'TGA': (['image/x-tga'], []),
        }
        for name, mime in list(Image.MIME.items()):
            mime_types, extensions = supported_formats_pil.get(name, ([], []))
            supported_formats_pil[name] = mime_types + [mime], extensions
        for ext, name in list(Image.EXTENSION.items()):
            assert '.' == ext[0]
            mime_types, extensions = supported_formats_pil.get(name, ([], []))
            supported_formats_pil[name] = mime_types, extensions + [ext[1:]]
        # Remove formats with no mime type or extension.
        for name in list(supported_formats_pil.keys()):
            mime_types, extensions = supported_formats_pil[name]
            if not mime_types or not extensions:
                del supported_formats_pil[name]
        # Remove archives/videos formats.
        for name in (
            'MPEG',
            'PDF',
        ):
            if name in supported_formats_pil:
                del supported_formats_pil[name]

        # Step 2: Collect GDK Pixbuf formats
        supported_formats_gdk = {}
        for format in GdkPixbuf.Pixbuf.get_formats():
            name = format.get_name().upper()
            if name in supported_formats_gdk:
                # The list of supported formats can sometimes contain duplicated entries
                continue

            supported_formats_gdk[name] = (
                format.get_mime_types(),
                format.get_extensions(),
            )

        # Step 3: merge format collections
        supported_formats = {}
        for provider in (supported_formats_gdk, supported_formats_pil):
            for name in list(provider.keys()):
                mime_types, extentions = provider[name]
                new_name = name.upper()
                new_mime_types, new_extensions = supported_formats.get( \
                    new_name, (set(), set()))
                new_mime_types.update([x.lower() for x in mime_types])
                new_extensions.update([x.lower() for x in extentions])
                supported_formats[new_name] = (new_mime_types, new_extensions)

        _SUPPORTED_IMAGE_FORMATS = supported_formats
    return _SUPPORTED_IMAGE_FORMATS

_SUPPORTED_IMAGE_FORMATS = None
# Set supported image extensions regexp from list of supported formats.
# Only used internally.
_SUPPORTED_IMAGE_REGEX = tools.formats_to_regex(get_supported_formats())
log.debug("_SUPPORTED_IMAGE_REGEX='%s'", _SUPPORTED_IMAGE_REGEX.pattern)

# vim: expandtab:sw=4:ts=4
