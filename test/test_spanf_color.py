"""Gray-preserving invariant of the development-only SPAN-F-YC model."""

from typing import Iterator

import pytest

torch = pytest.importorskip('torch')

from tools.spanf_yc import SPANFYC  # noqa: E402


@pytest.fixture(scope='module', autouse=True)
def limit_threads() -> Iterator[None]:
    previous = torch.get_num_threads()
    torch.set_num_threads(2)
    yield
    torch.set_num_threads(previous)


@pytest.mark.parametrize('deploy', [False, True])
def test_yc_gray_in_mixed_batch_and_deployment(deploy: bool) -> None:
    torch.manual_seed(19)
    model = SPANFYC(feature_channels=4)
    if deploy:
        model = model.to_deploy()
    x = torch.rand(2, 3, 7, 11)
    x[0] = x[0, :1].expand_as(x[0])
    output = model(x)
    assert output.shape == (2, 3, 14, 22)
    assert torch.equal(output[0, 0], output[0, 1])
    assert torch.equal(output[0, 1], output[0, 2])
    assert not torch.equal(output[1, 0], output[1, 1])
    if not deploy:
        output.square().mean().backward()
        for weight in (model.conv_2.conv[2].weight, model.chroma_logits.weight):
            assert weight.grad is not None and weight.grad.abs().sum() > 0
