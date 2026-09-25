# -*- coding: utf-8 -*-

"""Shim module to conditionally load the MuPdfArchive class."""

from __future__ import annotations

import os

from mcomix import log
from mcomix.archive.archive_base import BaseArchive, DisabledArchive
from mcomix.version_tools import LegacyVersion

PYMUPDF_VERSION_REQUIRED = "1.19.2"


class DisabledError(RuntimeError):
    """An exception raised when pdf_multi is disabled by the environment."""

    pass


class UnsupportedMuPDFVersionError(ImportError):
    """An exception raised when the pymupdf module is too old."""

    def __init__(
        self,
        message: str | None = None,
        name: str | None = None,
        path: str | None = None,
        found_version: str | None = None,
    ) -> None:
        self.found_version = found_version
        self.minimum_version = PYMUPDF_VERSION_REQUIRED
        if message is None:
            message = f"PyMuPDF {self.minimum_version} or later required"
            if self.found_version is not None:
                message += f", {self.found_version} found"
        super().__init__(message, name=name, path=path)


class DisabledMuPDFArchive(DisabledArchive):
    """Subclass of DisabledArchive used when MuPDFArchive is unavailable.

    This class will masquerade as MuPDFArchive for purposes of upstream
    reporting, so that the correct class is logged as being unavailable."""

    __name__ = "MuPDFArchive"


# On import, this code tests for a compatible version of the pymupdf
# module, and exports as "PdfMultiArchive" either the MuPDFArchive class
# from native_pdf.parent, or the DisabledMuPDFArchive class (aliased to
# 'MuPDFArchive' for caller-side reporting purposes)

try:
    if os.environ.get("MCOMIX_DISABLE_PDF_MULTI") is not None:
        raise DisabledError("MCOMIX_DISABLE_PDF_MULTI set in environment")
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf
        except ImportError:
            import fitz_old as pymupdf

    _version_attr: str = getattr(pymupdf, "pymupdf_version", pymupdf.VersionFitz)
    pymupdf_version = LegacyVersion(_version_attr)
    required_version = LegacyVersion(PYMUPDF_VERSION_REQUIRED)

    if pymupdf_version < required_version:
        raise UnsupportedMuPDFVersionError(found_version=_version_attr)
    from mcomix.archive.native_pdf.parent import MuPDFArchive

    log.info("Native PDF handler loaded, PyMuPDF version %s", _version_attr)
    PdfMultiArchive: type[BaseArchive] = MuPDFArchive
except (DisabledError, ImportError) as ex:
    log.info("Can't enable pdf_multi: %s", str(ex))
    PdfMultiArchive = DisabledMuPDFArchive

# vim: expandtab:sw=4:ts=4
