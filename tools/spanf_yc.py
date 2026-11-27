"""SPAN-F-YC: SPAN-F with predicted luma and kernel-resampled chroma.

Port of super_resolution/src/models/spanf_yc.py (a project design, not
upstream) onto tools/spanf.py's blocks, which add the deploy flag. Parameter
names and shapes match the training module, so its checkpoints load strictly.

Y = BT.601 luma, C = [R-G, B-G]. The trunk sees RGB; conv_2 predicts s^2 luma
sub-pixels and chroma_logits predicts, per sub-pixel, a softmax over a K x K
window of LR chroma (replicate padding). C_sr is a convex combination of C_lr,
so equal input channels give exactly equal output channels.
"""

import math
from typing import Mapping

import torch
import torch.nn.functional as F
from torch import nn

if __package__:
    from .spanf import ACTIVATIONS, SPAB1, SPANF, Conv3XC
else:
    from spanf import ACTIVATIONS, SPAB1, SPANF, Conv3XC

LUMA_WEIGHTS = (0.299, 0.587, 0.114)  # BT.601 (wR, wG, wB)


def is_spanf_yc(state: Mapping[str, torch.Tensor]) -> bool:
    return all(key in state for key in (
        'conv_near.weight', 'block_1.c1_r.eval_conv.weight',
        'block_5.c3_r.eval_conv.weight', 'conv_2.eval_conv.weight',
        'conv_cat.weight', 'chroma_logits.weight', 'chroma_logits.bias'))


def load_spanf_yc(state: Mapping[str, torch.Tensor], activation: str = 'tanh') -> 'SPANFYC':
    """Infer dimensions and strictly load a training or fused YC checkpoint."""
    channels, cat_channels, _, _ = state['conv_cat.weight'].shape
    near_channels, near_inputs, _, _ = state['conv_near.weight'].shape
    scale = math.isqrt(near_channels)
    logits, chroma_inputs, _, _ = state['chroma_logits.weight'].shape
    kernel = math.isqrt(logits // max(near_channels, 1))
    if any((scale < 1, near_channels != scale ** 2, near_inputs != 1,
            cat_channels != 2 * channels + near_channels,
            chroma_inputs != channels, kernel < 1, kernel % 2 == 0,
            logits != near_channels * kernel ** 2)):
        raise ValueError('Invalid RGB SPAN-F-YC checkpoint dimensions')
    deploy = not any('.conv.' in key or '.sk.' in key for key in state)
    model = SPANFYC(feature_channels=channels, upscale=scale,
                    chroma_kernel=kernel, deploy=deploy, activation=activation)
    model.load_state_dict(state, strict=True)
    return model.eval()


class SPANFYC(nn.Module):
    def __init__(self, feature_channels: int = 32, upscale: int = 2,
                 chroma_kernel: int = 5, deploy: bool = False,
                 activation: str = 'tanh') -> None:
        super().__init__()
        if chroma_kernel < 1 or chroma_kernel % 2 == 0:
            raise ValueError('chroma_kernel must be a positive odd integer')
        if activation not in ACTIVATIONS:
            raise ValueError(f'unknown SPAN-F activation {activation!r}')
        self.scale = upscale
        self.chroma_kernel = chroma_kernel
        c, s2, k2 = feature_channels, upscale ** 2, chroma_kernel ** 2
        self.conv_near = nn.Conv2d(1, s2, 3, padding=1, bias=False)
        with torch.no_grad():
            self.conv_near.weight.zero_()
            self.conv_near.weight[:, 0, 1, 1] = 1
        self.block_1 = SPAB1(3, c, c, deploy, activation)
        for i in range(2, 6):
            setattr(self, f'block_{i}', SPAB1(c, c, c, deploy, activation))
        self.conv_cat = nn.Conv2d(2 * c + s2, c, 1)
        self.conv_2 = Conv3XC(c, s2, deploy=deploy)
        self.depth_to_space = nn.PixelShuffle(upscale)
        self.chroma_logits = nn.Conv2d(c, s2 * k2, 1)
        with torch.no_grad():
            self.chroma_logits.bias.zero_()
            self.chroma_logits.bias.view(s2, k2)[:, k2 // 2] = 4.0

    to_deploy = SPANF.to_deploy

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        wr, wg, wb = LUMA_WEIGHTS
        r, g, b = x.unbind(1)
        y_lr = (wr * r + wg * g + wb * b)[:, None]
        c_lr = torch.stack([r - g, b - g], 1)
        first = self.block_1(x)
        out = first
        for i in range(2, 6):
            out = getattr(self, f'block_{i}')(out)
        feat = self.conv_cat(torch.cat((self.conv_near(y_lr), out, first), 1))
        y_sr = self.depth_to_space(self.conv_2(feat))[:, 0]
        cr, cb = self.depth_to_space(self._resample_chroma(c_lr, feat)).unbind(1)
        g_sr = y_sr - wr * cr - wb * cb
        return torch.stack([g_sr + cr, g_sr, g_sr + cb], 1)

    def _resample_chroma(self, c_lr: torch.Tensor, feat: torch.Tensor) -> torch.Tensor:
        """(N, 2, h, w) -> (N, 2 * s^2, h, w), PixelShuffle channel order.

        Same result as the training module's 5-D view/accumulate loop, but
        written without runtime shape arithmetic: ONNX Runtime places such
        integer nodes on the CPU, which the viewer's GPU-only adapter rejects.
        """
        k, s2 = self.chroma_kernel, self.scale ** 2
        k2 = k * k
        logits = self.chroma_logits(feat)
        padded = F.pad(c_lr, [k // 2] * 4, mode='replicate')

        def window(offset: int) -> slice:
            return slice(offset, offset - (k - 1) or None)
        # (N, K^2, h, w) per chroma component, channel q = window offset.
        shifted = [torch.cat([padded[:, comp:comp + 1, window(i), window(j)]
                              for i in range(k) for j in range(k)], 1)
                   for comp in range(2)]
        weights = [logits[:, t * k2:(t + 1) * k2].softmax(1) for t in range(s2)]
        return torch.cat([(w * s).sum(1, keepdim=True)
                          for s in shifted for w in weights], 1)
