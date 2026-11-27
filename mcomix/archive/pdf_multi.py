# -*- coding: utf-8 -*-

"""Shim module to conditionally load the MuPdfArchive class."""

from __future__ import annotations

from mcomix import log
from mcomix.archive.archive_base import BaseArchive
from mcomix._vendor.packaging.version import LegacyVersion

PYMUPDF_VERSION_REQUIRED = "1.19.2"


class _UnavailableMuPDFArchive(BaseArchive):
    # is_available must return False, not be missing: _get_handler selects a
    # handler without one, which would hide pdf_external.
    @staticmethod
    def is_available() -> bool:
        return False


# On import, this code tests for a compatible version of the pymupdf
# module, and exports as "PdfMultiArchive" either the MuPDFArchive class
# from native_pdf.parent, or a class that is never available.

try:
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf
        except ImportError:
            import fitz_old as pymupdf

    _version_attr: str = getattr(pymupdf, "pymupdf_version", pymupdf.VersionFitz)
    if LegacyVersion(_version_attr) < LegacyVersion(PYMUPDF_VERSION_REQUIRED):
        raise ImportError(f"PyMuPDF {PYMUPDF_VERSION_REQUIRED} or later required, "
                          f"{_version_attr} found")
    from mcomix.archive.native_pdf.parent import MuPDFArchive

    log.info("Native PDF handler loaded, PyMuPDF version %s", _version_attr)
    PdfMultiArchive: type[BaseArchive] = MuPDFArchive
except ImportError as ex:
    log.info("Can't enable pdf_multi: %s", str(ex))
    PdfMultiArchive = _UnavailableMuPDFArchive

# vim: expandtab:sw=4:ts=4
