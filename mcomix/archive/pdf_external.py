# -*- coding: utf-8 -*-

""" PDF handler. """

from mcomix import log
from mcomix import process
from mcomix.i18n import _
from mcomix._vendor.packaging.version import LegacyVersion
from mcomix.archive import archive_base
from mcomix.constants import PDF_RENDER_DPI_DEF, PDF_RENDER_DPI_MAX

import math
import os
import re
import subprocess
import tempfile
from typing import List

_pdf_possible = None
_mutool_exec = None

class PdfArchive(archive_base.BaseArchive):

    """ Concurrent calls to extract welcome! """
    support_concurrent_extractions = True

    _fill_image_regex = re.compile(r'^\s*<fill_image\b.*\b(matrix|transform)="(?P<matrix>[^"]+)".*\bwidth="(?P<width>-?\d+)".*\bheight="(?P<height>-?\d+)".*/>\s*$')

    def __init__(self, archive):
        super(PdfArchive, self).__init__(archive)

    @staticmethod
    def _run_command(cmd: List[str], operation: str) -> subprocess.CompletedProcess:
        """Run a PDF helper and report its actual failure to the caller."""
        try:
            proc = subprocess.run(cmd, stdin=process.NULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  encoding='utf-8', errors='replace',
                                  creationflags=process._get_creationflags())
        except OSError as ex:
            raise archive_base.ArchiveError(_('%s failed: %s') % (operation, ex)) from ex
        if proc.returncode != 0:
            raise archive_base.ArchiveError(_('%s failed (exit status %d): %s')
                                            % (operation, proc.returncode, proc.stderr.strip()))
        return proc

    def iter_contents(self):
        proc = self._run_command(_mutool_exec + ['show', '--', self.archive, 'pages'],
                                 _('Listing PDF %s') % self.archive)
        for line in proc.stdout.splitlines():
            if line.startswith('page '):
                yield line.split()[1] + '.png'

    def extract(self, filename, destination_dir):
        os.makedirs(destination_dir, exist_ok=True)
        destination_path = self._destination_path(destination_dir, filename)
        page_num = int(filename[0:-4])
        # Try to find optimal DPI.
        cmd = _mutool_exec + ['draw', '-F', 'trace', '--', self.archive, str(page_num)]
        log.debug('finding optimal DPI for %s: %s', filename, ' '.join(cmd))
        proc = self._run_command(cmd, _('Inspecting PDF page %s') % filename)
        max_size = 0
        max_dpi = PDF_RENDER_DPI_DEF
        for line in proc.stdout.splitlines():
            match = self._fill_image_regex.match(line)
            if not match:
                continue
            try:
                matrix = [float(f) for f in match.group('matrix').split()]
                if len(matrix) != 6 or not all(math.isfinite(value) for value in matrix):
                    raise ValueError(_('invalid image transformation matrix'))
                for size, coeff1, coeff2 in (
                    (int(match.group('width')), matrix[0], matrix[1]),
                    (int(match.group('height')), matrix[2], matrix[3]),
                ):
                    render_size = math.hypot(coeff1, coeff2)
                    if size <= 0 or not math.isfinite(render_size) or render_size <= 0:
                        raise ValueError(_('invalid image dimensions'))
                    if size < max_size:
                        continue
                    dpi = size * 72 / render_size
                    max_dpi = max(1, int(min(dpi, PDF_RENDER_DPI_MAX)))
                    max_size = size
            except (ValueError, OverflowError) as ex:
                raise archive_base.ArchiveError(_('Invalid geometry for PDF page %s: %s')
                                                % (filename, ex)) from ex
        # A private output avoids leaving partial data or deleting a previous
        # successful extraction when the renderer fails.
        fd, output_path = tempfile.mkstemp(prefix='mcomix.pdf.', suffix='.png',
                                           dir=destination_dir)
        os.close(fd)
        try:
            cmd = _mutool_exec + ['draw', '-r', str(max_dpi), '-o', output_path, '--', self.archive, str(page_num)]
            log.debug('rendering %s: %s', filename, ' '.join(cmd))
            self._run_command(cmd, _('Rendering PDF page %s') % filename)
            if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
                raise archive_base.ArchiveError(_('Rendering PDF page %s produced no image') % filename)
            os.replace(output_path, destination_path)
        finally:
            try:
                os.unlink(output_path)
            except FileNotFoundError:
                pass

    @staticmethod
    def is_available():
        global _pdf_possible
        if _pdf_possible is not None:
            return _pdf_possible
        global _mutool_exec
        mutool = process.find_executable(('mutool',))
        _pdf_possible = False
        version = None
        if mutool is None:
            log.debug('mutool executable not found')
        else:
            # MuPDF older than 1.7 has no '-v' switch.
            proc = process.popen([mutool, '-v'],
                                 stdout=process.NULL,
                                 stderr=process.PIPE)
            try:
                output = proc.stderr.read()
                if output.startswith(b'mutool version '):
                    version = output[15:].rstrip().decode()
            finally:
                proc.stderr.close()
                proc.wait()
            # 'mutool draw' with the trace format needs MuPDF 1.8.
            if version is not None and LegacyVersion(version) >= LegacyVersion('1.8'):
                _mutool_exec = [mutool]
                _pdf_possible = True
        if _pdf_possible:
            log.info('Using MuPDF version: %s', version)
            log.debug('mutool: %s', mutool)
        else:
            log.info('MuPDF 1.8 or later not available.')
        return _pdf_possible

# vim: expandtab:sw=4:ts=4
