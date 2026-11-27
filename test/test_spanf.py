"""Numerical checks for the optional development-only SPAN-F exporter."""

from pathlib import Path
from typing import Iterator

import pytest

torch = pytest.importorskip('torch')

from tools.spanf import Conv3XC, SPANF  # noqa: E402
from tools.spanf_yc import SPANFYC, load_spanf_yc  # noqa: E402


@pytest.fixture(scope='module', autouse=True)
def limit_threads() -> Iterator[None]:
    previous = torch.get_num_threads()
    torch.set_num_threads(2)
    yield
    torch.set_num_threads(previous)


@pytest.mark.parametrize('height,width', [(1, 1), (2, 5), (17, 23)])
def test_conv_fusion_including_borders(height: int, width: int) -> None:
    torch.manual_seed(7)
    conv = Conv3XC(3, 5).double()
    # Nonzero first-layer bias makes padding placement matter at the border.
    with torch.no_grad():
        conv.conv[0].bias.fill_(0.7)
        x = torch.randn(2, 3, height, width, dtype=torch.float64)
        expected = conv(x)
        conv.eval()
        torch.testing.assert_close(conv(x), expected, rtol=1e-12, atol=1e-12)
        conv.switch_to_deploy()
        torch.testing.assert_close(conv(x), expected, rtol=1e-12, atol=1e-12)


@pytest.mark.parametrize('activation', ['tanh', 'silu'])
@pytest.mark.parametrize('scale', [2, 3])
def test_training_deployment_and_checkpoint_round_trip(scale: int, activation: str) -> None:
    torch.manual_seed(3)
    model = SPANFYC(feature_channels=4, upscale=scale, chroma_kernel=3, activation=activation)
    x = torch.rand(2, 3, 9, 13)
    with torch.no_grad():
        expected = model(x)
        # Cached deployment weights must never override learned branches.
        for module in model.modules():
            if isinstance(module, Conv3XC):
                module.eval_conv.weight.fill_(100)
                module.eval_conv.bias.fill_(100)
        loaded = load_spanf_yc(model.state_dict(), activation)
        deployed = loaded.to_deploy()
        for actual in (loaded(x), deployed(x),
                       load_spanf_yc(deployed.state_dict(), activation)(x)):
            assert actual.shape == (2, 3, 9 * scale, 13 * scale)
            torch.testing.assert_close(actual, expected, rtol=1e-5, atol=1e-6)
        assert any('.conv.' in key for key in model.state_dict())
        assert not any('.conv.' in key or '.sk.' in key for key in deployed.state_dict())
        del model.block_1.c1_r.sk.bias
        with pytest.raises(RuntimeError, match='Missing key'):
            load_spanf_yc(model.state_dict())


def test_activation_variants() -> None:
    torch.manual_seed(5)
    tanh = SPANF(feature_channels=4)
    silu = SPANF(feature_channels=4, activation='silu')
    silu.load_state_dict(tanh.state_dict())
    block = tanh.block_2
    assert isinstance(block.act1, torch.nn.Tanh)
    assert isinstance(silu.block_2.act1, torch.nn.SiLU)
    x = torch.randn(1, 4, 5, 6)
    with torch.no_grad():
        out = block.c3_r(block.act1(block.c2_r(block.act1(block.c1_r(x)))))
        torch.testing.assert_close(block(x), (out + x) * torch.tanh(out))
        assert not torch.allclose(tanh(x[:, :3]), silu(x[:, :3]))
    with pytest.raises(ValueError, match='activation'):
        SPANF(activation='relu')


@pytest.mark.parametrize('activation', ['tanh', 'silu'])
def test_export_onnx_spanf_yc_checkpoint(tmp_path: Path, monkeypatch, exporter,
                                         activation: str) -> None:
    import numpy as np
    import onnx
    import onnxruntime as ort
    torch.manual_seed(11)
    model = SPANFYC(feature_channels=4, chroma_kernel=3, activation=activation)
    checkpoint = tmp_path / 'spanf.pth'
    torch.save(model.state_dict(), checkpoint)
    # Runtime timing is hardware-specific; numerical checks use CPU ONNX.
    monkeypatch.setattr(exporter, 'directml_ms', lambda path: 1.0)
    exporter.main([str(checkpoint), '--io', 'float32', '--precision', 'fp32',
                   '--spanf-activation', activation])
    output = checkpoint.with_suffix('.fp32.onnx')
    metadata = {p.key: p.value for p in onnx.load(str(output)).metadata_props}
    assert metadata['spanf.activation'] == activation
    assert metadata['mcomix.scale'] == '2'
    session = ort.InferenceSession(str(output), providers=['CPUExecutionProvider'])
    with torch.no_grad():
        x = torch.rand(1, 3, 11, 7)
        np.testing.assert_allclose(session.run(None, {'input': x.numpy()})[0],
                                   model.eval()(x).numpy(), rtol=1e-4, atol=1e-5)


def test_export_rejects_old_spanf_checkpoint(tmp_path: Path, exporter) -> None:
    checkpoint = tmp_path / 'old_spanf.pth'
    torch.save(SPANF(feature_channels=4).state_dict(), checkpoint)
    with pytest.raises(SystemExit, match='2'):
        exporter.main([str(checkpoint)])
    assert not checkpoint.with_suffix('.fp32.onnx').exists()


def test_yc_checkpoint_rejects_invalid_dimensions() -> None:
    state = SPANFYC(feature_channels=4).state_dict()
    state['chroma_logits.weight'] = state['chroma_logits.weight'][:-1]
    with pytest.raises(ValueError, match='dimensions'):
        load_spanf_yc(state)
