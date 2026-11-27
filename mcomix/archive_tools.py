"""archive_tools.py - Archive tool functions."""

import os
import shutil
import zipfile
import tarfile
import tempfile

from mcomix import image_tools
from mcomix import constants
from mcomix import log
from mcomix.archive import (
    mobi,
    pdf_multi,
    pdf_external,
    sevenzip_external,
    tar,
    zip,
)
from mcomix import tools
from mcomix.i18n import _

# Handlers for each archive type.
_HANDLERS = {
    constants.ZIP: (
        zip.ZipArchive,
    ),
    constants.ZIP_EXTERNAL: (
        sevenzip_external.SevenZipArchive,
    ),
    constants.TAR: (
        tar.TarArchive,
    ),
    constants.GZIP: (
        tar.TarArchive,
    ),
    constants.BZIP2: (
        tar.TarArchive,
    ),
    constants.XZ: (
        tar.TarArchive,
    ),
    constants.RAR: (
        sevenzip_external.SevenZipArchive,
    ),
    constants.LHA: (
        sevenzip_external.SevenZipArchive,
    ),
    constants.SEVENZIP: (
        sevenzip_external.SevenZipArchive,
    ),
    constants.PDF: (
        pdf_multi.PdfMultiArchive,
        pdf_external.PdfArchive,
    ),
    constants.MOBI: (
        mobi.MobiArchive,
    ),
}

def _get_handler(archive_type):
    """ Return best archive class for format <archive_type> """

    for handler in _HANDLERS[archive_type]:
        if not hasattr(handler, 'is_available'):
            return handler
        if handler.is_available():
            return handler
        log.debug("Ignoring unavailable handler %s", handler.__name__)

_SUPPORTED_ARCHIVE_FORMATS = {
    name: (set(formats[0]), set(formats[1]))
    for name, formats, archive_type in (
        ('ZIP', constants.ZIP_FORMATS, constants.ZIP),
        ('Tar', constants.TAR_FORMATS, constants.TAR),
        ('RAR', constants.RAR_FORMATS, constants.RAR),
        ('7z', constants.SZIP_FORMATS, constants.SEVENZIP),
        ('LHA', constants.LHA_FORMATS, constants.LHA),
        ('PDF', constants.PDF_FORMATS, constants.PDF),
        ('MobiPocket', constants.MOBI_FORMATS, constants.MOBI),
    )
    if _get_handler(archive_type) is not None
}

def get_supported_formats():
    return _SUPPORTED_ARCHIVE_FORMATS

# Set supported archive extensions regexp from list of supported formats.
# Only used internally.
_SUPPORTED_ARCHIVE_REGEX = tools.formats_to_regex(get_supported_formats())
log.debug("_SUPPORTED_ARCHIVE_REGEX='%s'", _SUPPORTED_ARCHIVE_REGEX.pattern)

def is_archive_file(path):
    """Return True if the file at <path> is a supported archive file.
    """
    return _SUPPORTED_ARCHIVE_REGEX.search(path) is not None

def archive_mime_type(path):
    """Return the archive type of <path> or None for non-archives."""
    try:

        if os.path.isfile(path):

            if not os.access(path, os.R_OK):
                return None

            if zipfile.is_zipfile(path):
                if zip.is_py_supported_zipfile(path):
                    return constants.ZIP
                else:
                    return constants.ZIP_EXTERNAL

            fd = open(path, 'rb')
            magic = fd.read(5)
            fd.seek(60)
            magic2 = fd.read(8)
            fd.close()

            try:
                istarfile = tarfile.is_tarfile(path)
            except IOError:
                # Tarfile raises an error when accessing certain network shares
                istarfile = False

            if istarfile and os.path.getsize(path) > 0:
                # tarfile also reads LZMA streams as XZ.
                if magic[0:5] == b'\xFD7zXZ' or magic[0:5] == b']\x00\x00\x80\x00':
                    return constants.XZ
                elif magic.startswith(b'BZh'):
                    return constants.BZIP2
                elif magic.startswith(b'\037\213'):
                    return constants.GZIP
                else:
                    return constants.TAR

            if magic[0:4] == b'Rar!':
                return constants.RAR

            if magic[0:4] == b'7z\xBC\xAF':
                return constants.SEVENZIP

            if magic[2:4] == b'-l':
                return constants.LHA

            if magic[0:4] == b'%PDF':
                return constants.PDF

            if magic2 == b'BOOKMOBI':
                return constants.MOBI

    except Exception:
        log.warning(_('! Could not read %s'), path)

    return None

def get_archive_info(path):
    """Return a tuple (mime, num_pages, size) with info about the archive
    at <path>, or None if <path> doesn't point to a supported
    """
    cleanup = []
    try:
        tmpdir = tempfile.mkdtemp(prefix='mcomix_archive_info.')
        cleanup.append(lambda: shutil.rmtree(tmpdir, True))

        mime = archive_mime_type(path)
        archive = get_recursive_archive_handler(path, tmpdir, type=mime)
        if archive is None:
            return None
        cleanup.append(archive.close)

        files = archive.list_contents()
        num_pages = len(list(filter(image_tools.is_image_file, files)))
        size = os.stat(path).st_size

        return (mime, num_pages, size)
    finally:
        for fn in reversed(cleanup):
            fn()

def get_archive_handler(path, mimetype=None):
    """ Returns a fitting extractor handler for the archive passed
    in <path> (with optional mime type <mimetype>. Returns None if no matching
    extractor was found.
    """
    if mimetype is None:
        mimetype = archive_mime_type(path)
        if mimetype is None:
            return None

    handler = _get_handler(mimetype)
    if handler is None:
        return None

    log.debug('Archive handler %(handler)s for archive "%(archivename)s" was selected.',
              {'handler': handler.__name__, 'archivename': os.path.split(path)[1]})
    return handler(path)

def get_recursive_archive_handler(path, destination_dir, type=None):
    """ Same as <get_archive_handler> but the handler will transparently handle
    archives within archives.
    """
    archive = get_archive_handler(path, mimetype=type)
    if archive is None:
        return None
    # XXX: Deferred import to avoid circular dependency
    from mcomix.archive import archive_recursive
    return archive_recursive.RecursiveArchive(archive, destination_dir)
 
# vim: expandtab:sw=4:ts=4
