"""Export a wrapped fine-tuned SPAN checkpoint with the documented command."""

from pathlib import Path


def test_export_finetuned_span(tmp_path: Path, monkeypatch, exporter) -> None:
    import numpy as np
    import onnx
    import onnxruntime as ort
    import torch
    from spandrel.architectures.SPAN.__arch.span import SPAN

    previous_threads = torch.get_num_threads()
    torch.set_num_threads(2)
    try:
        torch.manual_seed(5)
        model = SPAN(num_in_ch=3, num_out_ch=3, feature_channels=4, upscale=2,
                     img_range=17, rgb_mean=(0.2, 0.3, 0.4))
        state = model.state_dict()
        for key in state:
            if '.eval_conv.' in key:
                state[key] = torch.full_like(state[key], 1000)
        checkpoint = tmp_path / 'finetuned.pth'
        torch.save({'params_ema': {'module.' + k: v for k, v in state.items()}}, checkpoint)
        # Runtime timing is hardware-specific; numerical checks use CPU ONNX.
        monkeypatch.setattr(exporter, 'directml_ms', lambda path: 1.0)
        exporter.main([str(checkpoint), '--precision', 'fp32', '--opset', '18',
                       '--io', 'float32', '--img-range', '17', '--rgb-mean', '0.2', '0.3', '0.4'])
        output = checkpoint.with_suffix('.fp32.onnx')
        graph = onnx.load(str(output))
        metadata = {p.key: p.value for p in graph.metadata_props}
        assert metadata['mcomix.runtimes'] == 'windowsml,directml'
        assert metadata['span.img_range'] == '17.0'
        assert graph.opset_import[0].version == 18
        assert not checkpoint.with_suffix('.fp16.onnx').exists()
        assert not any('.sk.' in p.name or '.conv.' in p.name for p in graph.graph.initializer)
        session = ort.InferenceSession(str(output), providers=['CPUExecutionProvider'])
        with torch.no_grad():
            for shape in ((1, 3, 3, 5), (2, 3, 17, 23)):
                x = torch.rand(*shape)
                expected = model(x).numpy()  # Evaluate the actual training branches.
                actual = session.run(None, {'input': x.numpy()})[0]
                np.testing.assert_allclose(actual, expected, rtol=1e-4, atol=1e-5)
    finally:
        torch.set_num_threads(previous_threads)
