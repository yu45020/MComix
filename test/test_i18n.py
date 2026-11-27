"""Byte text from archives (comment files) decodes to the intended characters."""
import pytest

from mcomix import i18n


def test_utf8_wins_over_a_cjk_locale_guess_without_chardet(
        monkeypatch: pytest.MonkeyPatch) -> None:
    # chardet is optional; a CJK locale's guess must not win over UTF-8.
    monkeypatch.setattr(i18n, 'chardet', None)
    monkeypatch.setattr(i18n.locale, 'getpreferredencoding', lambda *args: 'cp936')
    assert i18n.to_unicode('café page'.encode('utf-8')) == 'café page'
