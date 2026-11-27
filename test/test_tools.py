import pytest

from mcomix import tools


@pytest.mark.parametrize('files,expected', [
    (['text_2.jpg', '2_text.jpg'], ['2_text.jpg', 'text_2.jpg']),
    (['Comic 001-01.jpg', 'Comic 001-00.jpg', 'zCover.jpg', 'Comic 001-03.jpg'],
     ['Comic 001-00.jpg', 'Comic 001-01.jpg', 'Comic 001-03.jpg', 'zCover.jpg']),
    # Superscript digits are text; other decimal digits are numbers.
    (['Vol 10', 'Vol 2', 'Vol 1²', 'Vol 1'], ['Vol 1', 'Vol 1²', 'Vol 2', 'Vol 10']),
    # Equal keys keep their order.
    (['Vol 10', 'Vol ٢', 'vol 02', 'Vol 2', 'Vol ١'],
     ['Vol ١', 'Vol ٢', 'vol 02', 'Vol 2', 'Vol 10']),
])
def test_alphanumeric_sort(files: list, expected: list) -> None:
    tools.alphanumeric_sort(files)
    assert files == expected
