"""Handler shells that skip __init__ but keep its always-present state."""

from mcomix import file_handler, image_handler
from mcomix.render_cache import RenderCache


def file_handler_shell() -> file_handler.FileHandler:
    """Return a FileHandler without GTK/extractor setup; tests add the rest."""
    handler = object.__new__(file_handler.FileHandler)
    handler._resume_dialog = None
    handler._retired_archives = []
    return handler


def image_handler_shell() -> image_handler.ImageHandler:
    """Return an ImageHandler without its worker/upscaler; tests add the rest."""
    handler = object.__new__(image_handler.ImageHandler)
    handler._render_cache = RenderCache()
    handler._animations = None
    return handler
