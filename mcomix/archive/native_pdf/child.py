"""Child process code for native PDF multiprocessing."""
from __future__ import annotations

import io
import os
import re
from PIL import Image
from typing import Generator, List, Optional

try:
    import pymupdf
except ImportError:
    try:
        import fitz as pymupdf
    except ImportError:
        import fitz_old as pymupdf

from mcomix.constants import PDF_RENDER_DPI_DEF


# Will delimit the page name from the xref part of a file name
XREF_DELIMITER = '_mcmxref'


class MuPDFWorker:
    def __init__(self, filename: Optional[str]) -> None:
        self._complex_doc = False
        self._contents = []
        self._page_iterator = None
        self._listing_finished = False
        self._listing_error = None
        self.doc = pymupdf.open(filename)

    def close(self) -> None:
        """Release the document before its child process exits."""
        if self._page_iterator is not None:
            self._page_iterator.close()
            self._page_iterator = None
        self.doc.close()

    def _image_extension(self, page_num: int) -> str:
        """Return the filename extension for the page image."""
        # Rotated images are saved losslessly after applying page rotation.
        if self.doc[page_num].rotation:
            return 'png'
        return self._check_image_type(page_num)

    def _extract_as_image(self, page_num: int) -> bool:
        """Check whether the page can be extracted via image export."""
        if self._complex_doc:
            return False
        if not self._must_render_page(page_num) and self._can_extract_image(page_num):
            return True
        return False

    def _must_render_page(self, page_num: int) -> bool:
        """Determine if a page has any forced-render markers.

        Rendering to pixmap must be forced if any of these apply:
            - The page has any text content
            - The page has any drawing content
            - The page contains 0, or more than 1, embedded image
        """
        page = self.doc[page_num]
        if len(page.get_images()) != 1 or len(page.get_text()) > 0:
            self._complex_doc = True
            result = True
        else:
            result = False
        del page
        return result

    def _can_extract_image(self, page_num: int) -> bool:
        """Determine if a page has an extractable image.

        Makes a closer examination than _must_render_page(),
        by checking whether the page contains a single, full-page
        image, then actually extracting the first such image
        encountered to determine the filetype (extension).
        """
        page = self.doc[page_num]
        image_info = page.get_image_info()
        if len(image_info) != 1:
            return False
        info = image_info[0]
        img_rect = pymupdf.Rect(info.get("bbox", (0, 0, 0, 0))).irect
        page_rect = pymupdf.Rect(page.mediabox).irect
        page_area = page_rect.get_area()
        area_diff = abs(page_area - img_rect.get_area())
        is_full_page: bool = area_diff < 0.05 * page_area
        del page
        del image_info
        return is_full_page

    def _check_image_type(self, page_num: int) -> str:
        """Examine the page's embedded image for its file type.

        Probe each embedded image because one PDF can mix image formats.
        Unknown raw formats are converted to PNG by Document.extract_image.
        """
        extension = 'png'
        try:
            xref = self._get_image_xref(page_num)
            img = pymupdf.image_profile(self.doc.xref_stream_raw(xref))
            # If image_profile returns an empty dict, the image type is
            # "exotic" and not supported for direct extraction.
            # That doesn't mean that the images can't be extracted.
            # Document.extract_image will automatically convert to PNG,
            # when we call it to extract the xref. It'll be slower than
            # extraction without converting, but still very fast.
            if img:
                extension = img.get('ext', 'png')
                del img
        except (AttributeError, TypeError):
            # Some PyMuPDF releases cannot probe raw Python byte streams.
            # Ask the extractor for the authoritative format in that case.
            image = self.doc.extract_image(xref)
            if image:
                extension = image.get('ext', 'png')
        return extension

    def _get_image_xref(self, page_num: int) -> int:
        try:
            image_info = self.doc.get_page_images(page_num)
            xref = int(image_info[0][0])
            return xref
        except (TypeError, IndexError):
            return -1
        finally:
            image_info = None

    def iter_contents(self) -> Generator[str, None, None]:
        self._complex_doc = False
        for pg in range(self.doc.page_count):
            pagenum = f"page{pg + 1:04}"
            if self._extract_as_image(pg):
                xref = self._get_image_xref(pg)
                ext = self._image_extension(pg)
                filename = f"{pagenum}{XREF_DELIMITER}{xref:04}.{ext}"
            else:
                filename = f"{pagenum}.png"
            yield filename

    def list_pages(self, offset: int, limit: int) -> List[str]:
        """Keep identifiers stable across calls and send bounded IPC batches."""
        if self._listing_error is not None:
            raise RuntimeError(self._listing_error)
        if self._page_iterator is None and not self._listing_finished:
            self._page_iterator = self.iter_contents()
        while len(self._contents) < offset + limit and not self._listing_finished:
            try:
                self._contents.append(next(self._page_iterator))
            except StopIteration:
                self._page_iterator = None
                self._listing_finished = True
            except Exception as error:
                # A failed iterator must not turn into a successful partial
                # listing when another caller asks for the same document.
                self._page_iterator = None
                self._listing_error = str(error)
                raise
        return self._contents[offset:offset + limit]

    def extract_xref(self, page: int, xref: int, path: str) -> None:
        """Save the embedded PDF image for a given xref. The page is indexed starting with zero."""
        img = self.doc.extract_image(xref)
        if not img:
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        img_bytes = img.get("image", b"")
        del img

        # Applying PDF rotation must not re-encode embedded JPEGs at a
        # lower quality. Rotate decoded pixels into a lossless PNG instead.
        rotation = self.doc[page].rotation
        if rotation:
            with Image.open(io.BytesIO(img_bytes)) as pil_img:
                rotated = pil_img.rotate(-rotation, expand=True)
                try:
                    if rotated.mode not in ('1', 'L', 'LA', 'P', 'RGB', 'RGBA', 'I', 'I;16'):
                        converted = rotated.convert('RGB')
                        rotated.close()
                        rotated = converted
                    # PDF rotation has already been applied to the pixels.
                    # Do not propagate an embedded orientation tag as well.
                    rotated.info.pop('exif', None)
                    rotated.save(path, format='PNG')
                finally:
                    rotated.close()
        else:
            with open(path, 'wb') as out:
                out.write(img_bytes)
        del img_bytes

    def render_page(self, pg: int, path: str) -> None:
        """Render the page to an image file and save."""
        page = self.doc[pg]
        pixmap = page.get_pixmap(dpi=PDF_RENDER_DPI_DEF)
        pixmap.save(path)
        del pixmap
        del page

    def extract_file(self, filename: str, dest: str) -> None:
        match = re.fullmatch(r'page([0-9]+)(?:' + XREF_DELIMITER + r'([0-9]+))?\.([a-z0-9]+)', filename)
        if match is None or int(match.group(1)) < 1:
            raise ValueError('Invalid PDF page name: %s' % filename)
        outpath = os.path.join(dest, filename)
        page = int(match.group(1)) - 1
        if match.group(2) is not None:
            self.extract_xref(page, int(match.group(2)), outpath)
        else:
            self.render_page(page, outpath)
