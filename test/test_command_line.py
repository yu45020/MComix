"""Command-line options that choose where the opened book starts."""

import pytest

from mcomix import run


def test_entry_and_page_are_read_with_the_path() -> None:
    opts, args = run.parse_arguments(['--entry', 'vol2/012.jpg', '--page', '12', 'book.zip'])
    assert (opts.entry, opts.page, args) == ('vol2/012.jpg', 12, ['book.zip'])
    opts, args = run.parse_arguments(['book.zip'])
    assert (opts.entry, opts.page) == ('', None)


@pytest.mark.parametrize('argv', [['--page', '0', 'book.zip'], ['--page', '3'],
                                  ['--entry', '01.jpg']])
def test_invalid_page_or_missing_path_is_rejected(argv: list) -> None:
    with pytest.raises(SystemExit):
        run.parse_arguments(argv)
