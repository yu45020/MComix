"""Checkpoint-compatible SPAN-F and its Conv3XC deployment conversion.

Architecture: Amazingren/NTIRE2025_ESR, models/team24_SPANF.py (MIT,
Copyright (c) 2025 BinRen). Training branches follow the supplied
super_resolution/src/models/{span,spanf}.py implementations: gain 2,
padding before the first 1x1 convolution, and no input normalization.

The activation cannot be read from a state dictionary. 'tanh' (the default)
matches the current training script: Tanh between convolutions and tanh(out)
as attention. 'silu' is upstream's SiLU with sigmoid(out) - 0.5 attention.
"""

import copy

import torch
import torch.nn.functional as F
from torch import nn

ACTIVATIONS = ('tanh', 'silu')


class Conv3XC(nn.Module):
    """Train with a 1x1/3x3/1x1 stack and skip; evaluate with one 3x3."""

    def __init__(self, c_in: int, c_out: int, deploy: bool = False) -> None:
        super().__init__()
        if not deploy:
            self.sk = nn.Conv2d(c_in, c_out, 1)
            self.conv = nn.Sequential(
                nn.Conv2d(c_in, c_in * 2, 1),
                nn.Conv2d(c_in * 2, c_out * 2, 3),
                nn.Conv2d(c_out * 2, c_out, 1))
        self.eval_conv = nn.Conv2d(c_in, c_out, 3, padding=1)
        self.eval_conv.requires_grad_(False)

    @torch.no_grad()
    def update_params(self) -> None:
        """Recompute cached inference weights; saved eval_conv can be stale."""
        if not hasattr(self, 'conv'):
            return
        first, middle, last = self.conv
        # Compose the channel maps without reversing the spatial kernel.
        kernel = torch.einsum('om,mihw,ij->ojhw', last.weight[:, :, 0, 0],
                              middle.weight, first.weight[:, :, 0, 0])
        bias = last.weight[:, :, 0, 0] @ (
            middle.weight.sum((2, 3)) @ first.bias + middle.bias) + last.bias
        self.eval_conv.weight.copy_(kernel + F.pad(self.sk.weight, (1, 1, 1, 1)))
        self.eval_conv.bias.copy_(bias + self.sk.bias)

    def train(self, mode: bool = True) -> 'Conv3XC':
        super().train(mode)
        if not mode:
            self.update_params()
        return self

    def switch_to_deploy(self) -> None:
        """Fuse and discard the training branches, once weights are loaded."""
        self.eval()
        if hasattr(self, 'conv'):
            del self.conv
            del self.sk

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if self.training and hasattr(self, 'conv'):
            return self.conv(F.pad(x, (1, 1, 1, 1))) + self.sk(x)
        return self.eval_conv(x)


class SPAB1(nn.Module):
    def __init__(self, in_channels: int, mid_channels: int,
                 out_channels: int, deploy: bool = False,
                 activation: str = 'tanh') -> None:
        super().__init__()
        if activation not in ACTIVATIONS:
            raise ValueError(f'unknown SPAN-F activation {activation!r}')
        self.c1_r = Conv3XC(in_channels, mid_channels, deploy=deploy)
        self.c2_r = Conv3XC(mid_channels, mid_channels, deploy=deploy)
        self.c3_r = Conv3XC(mid_channels, out_channels, deploy=deploy)
        self.act1 = nn.Tanh() if activation == 'tanh' else nn.SiLU()
        self.tanh_attention = activation == 'tanh'
        self.residual = in_channels == out_channels

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out = self.c3_r(self.act1(self.c2_r(self.act1(self.c1_r(x)))))
        if not self.residual:
            return out
        attention = torch.tanh(out) if self.tanh_attention else torch.sigmoid(out) - 0.5
        return (out + x) * attention


class SPANF(nn.Module):
    def __init__(self, feature_channels: int = 32, upscale: int = 2,
                 deploy: bool = False, activation: str = 'tanh') -> None:
        super().__init__()
        self.scale = upscale
        c, s2 = feature_channels, 3 * upscale ** 2
        self.conv_near = nn.Conv2d(3, s2, 3, padding=1, groups=3, bias=False)
        with torch.no_grad():
            self.conv_near.weight.zero_()
            self.conv_near.weight[:, 0, 1, 1] = 1
        self.block_1 = SPAB1(3, c, c, deploy, activation)
        for i in range(2, 6):
            setattr(self, f'block_{i}', SPAB1(c, c, c, deploy, activation))
        self.conv_cat = nn.Conv2d(2 * c + s2, c, 1)
        self.conv_2 = Conv3XC(c, s2, deploy=deploy)
        self.depth_to_space = nn.PixelShuffle(upscale)

    def to_deploy(self) -> 'SPANF':
        """Return an exportable copy with only fused convolution parameters."""
        model = copy.deepcopy(self).eval()
        for module in model.modules():
            if isinstance(module, Conv3XC):
                module.switch_to_deploy()
        return model

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        first = self.block_1(x)
        out = first
        for i in range(2, 6):
            out = getattr(self, f'block_{i}')(out)
        out = self.conv_cat(torch.cat((self.conv_near(x), out, first), dim=1))
        return self.depth_to_space(self.conv_2(out))
