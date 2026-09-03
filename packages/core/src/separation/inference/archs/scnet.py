"""SCNet sparse-compression source-separation architecture.

The module follows the inference model in ZFTurbo's
Music-Source-Separation-Training revision 83d495d.  Training and distributed
execution are intentionally omitted; keeping module names and tensor layouts
unchanged preserves compatibility with its published checkpoints.
"""
from __future__ import annotations

import math
from collections import deque

import torch
from torch import nn
from torch.nn import functional as F

from .separation import SeparationNet


class Swish(nn.Module):
    """Swish activation used inside SCNet convolution modules."""

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x * x.sigmoid()


class ConvolutionModule(nn.Module):
    """Residual depthwise-convolution stack used by each sparse band."""

    def __init__(self, channels: int, depth: int = 2, compress: int = 4, kernel: int = 3) -> None:
        super().__init__()
        if kernel % 2 != 1:
            raise ValueError("SCNet convolution kernel must be odd")
        hidden_size = int(channels / compress)
        self.depth = abs(depth)
        self.layers = nn.ModuleList()
        for _ in range(self.depth):
            padding = kernel // 2
            self.layers.append(
                nn.Sequential(
                    nn.GroupNorm(1, channels),
                    nn.Conv1d(channels, hidden_size * 2, kernel, padding=padding),
                    nn.GLU(1),
                    nn.Conv1d(hidden_size, hidden_size, kernel, padding=padding, groups=hidden_size),
                    nn.GroupNorm(1, hidden_size),
                    Swish(),
                    nn.Conv1d(hidden_size, channels, 1),
                )
            )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        for layer in self.layers:
            x = x + layer(x)
        return x


class FusionLayer(nn.Module):
    """Fuse a decoder tensor with its encoder skip connection."""

    def __init__(self, channels: int, kernel_size: int = 3, stride: int = 1, padding: int = 1) -> None:
        super().__init__()
        self.conv = nn.Conv2d(channels * 2, channels * 2, kernel_size, stride=stride, padding=padding)

    def forward(self, x: torch.Tensor, skip: torch.Tensor | None = None) -> torch.Tensor:
        if skip is not None:
            x += skip
        x = x.repeat(1, 2, 1, 1)
        return F.glu(self.conv(x), dim=1)


class SDlayer(nn.Module):
    """Split frequencies into sparse bands and downsample each band."""

    def __init__(self, channels_in: int, channels_out: int, band_configs: dict) -> None:
        super().__init__()
        self.convs = nn.ModuleList()
        self.strides: list[int] = []
        self.kernels: list[int] = []
        for config in band_configs.values():
            self.convs.append(
                nn.Conv2d(
                    channels_in,
                    channels_out,
                    (config["kernel"], 1),
                    (config["stride"], 1),
                    (0, 0),
                )
            )
            self.strides.append(config["stride"])
            self.kernels.append(config["kernel"])
        self.SR_low = band_configs["low"]["SR"]
        self.SR_mid = band_configs["mid"]["SR"]

    def forward(self, x: torch.Tensor) -> tuple[list[torch.Tensor], list[int]]:
        _, _, frequencies, _ = x.shape
        splits = [
            (0, math.ceil(frequencies * self.SR_low)),
            (
                math.ceil(frequencies * self.SR_low),
                math.ceil(frequencies * (self.SR_low + self.SR_mid)),
            ),
            (math.ceil(frequencies * (self.SR_low + self.SR_mid)), frequencies),
        ]

        outputs: list[torch.Tensor] = []
        original_lengths: list[int] = []
        for conv, stride, kernel, (start, end) in zip(
            self.convs, self.strides, self.kernels, splits
        ):
            extracted = x[:, :, start:end, :]
            original_lengths.append(end - start)
            current_length = extracted.shape[2]
            total_padding = (
                kernel - stride
                if stride == 1
                else (stride - current_length % stride) % stride
            )
            pad_left = total_padding // 2
            padded = F.pad(extracted, (0, 0, pad_left, total_padding - pad_left))
            outputs.append(conv(padded))
        return outputs, original_lengths


class SUlayer(nn.Module):
    """Upsample sparse frequency bands in the decoder."""

    def __init__(self, channels_in: int, channels_out: int, band_configs: dict) -> None:
        super().__init__()
        self.convtrs = nn.ModuleList(
            [
                nn.ConvTranspose2d(
                    channels_in,
                    channels_out,
                    [config["kernel"], 1],
                    [config["stride"], 1],
                )
                for config in band_configs.values()
            ]
        )

    def forward(
        self,
        x: torch.Tensor,
        lengths: list[int],
        origin_lengths: list[int],
    ) -> torch.Tensor:
        splits = [
            (0, lengths[0]),
            (lengths[0], lengths[0] + lengths[1]),
            (lengths[0] + lengths[1], None),
        ]
        outputs = []
        for index, (convtr, (start, end)) in enumerate(zip(self.convtrs, splits)):
            out = convtr(x[:, :, start:end, :])
            distance = abs(origin_lengths[index] - out.shape[2]) // 2
            outputs.append(out[:, :, distance : distance + origin_lengths[index], :])
        return torch.cat(outputs, dim=2)


class SDblock(nn.Module):
    """Sparse downsample block with per-band residual processing."""

    def __init__(
        self,
        channels_in: int,
        channels_out: int,
        band_configs: dict,
        conv_config: dict,
        depths: list[int],
        kernel_size: int = 3,
    ) -> None:
        super().__init__()
        self.SDlayer = SDlayer(channels_in, channels_out, band_configs)
        self.conv_modules = nn.ModuleList(
            [ConvolutionModule(channels_out, depth, **conv_config) for depth in depths]
        )
        self.globalconv = nn.Conv2d(
            channels_out, channels_out, kernel_size, 1, (kernel_size - 1) // 2
        )

    def forward(
        self, x: torch.Tensor
    ) -> tuple[torch.Tensor, torch.Tensor, list[int], list[int]]:
        bands, original_lengths = self.SDlayer(x)
        processed = []
        for conv, band in zip(self.conv_modules, bands):
            batch, channels, frequencies, frames = band.shape
            value = conv(
                band.permute(0, 2, 1, 3).reshape(-1, channels, frames)
            )
            value = value.view(batch, frequencies, channels, frames).permute(0, 2, 1, 3)
            processed.append(F.gelu(value))

        lengths = [band.shape[-2] for band in processed]
        full_band = torch.cat(processed, dim=2)
        return self.globalconv(full_band), full_band, lengths, original_lengths


class SCNet(nn.Module):
    """Sparse Compression Network for four-stem music separation."""

    def __init__(
        self,
        sources: list[str] | tuple[str, ...] = ("drums", "bass", "other", "vocals"),
        audio_channels: int = 2,
        dims: list[int] | tuple[int, ...] = (4, 32, 64, 128),
        nfft: int = 4096,
        hop_size: int = 1024,
        win_size: int = 4096,
        normalized: bool = True,
        band_SR: list[float] | tuple[float, ...] = (0.175, 0.392, 0.433),
        band_stride: list[int] | tuple[int, ...] = (1, 4, 16),
        band_kernel: list[int] | tuple[int, ...] = (3, 4, 16),
        conv_depths: list[int] | tuple[int, ...] = (3, 2, 1),
        compress: int = 4,
        conv_kernel: int = 3,
        num_dplayer: int = 6,
        expand: int = 1,
    ) -> None:
        super().__init__()
        self.sources = list(sources)
        self.audio_channels = audio_channels
        self.dims = list(dims)
        band_keys = ("low", "mid", "high")
        self.band_configs = {
            key: {"SR": band_SR[index], "stride": band_stride[index], "kernel": band_kernel[index]}
            for index, key in enumerate(band_keys)
        }
        self.hop_length = hop_size
        conv_config = {"compress": compress, "kernel": conv_kernel}
        self.stft_config = {
            "n_fft": nfft,
            "hop_length": hop_size,
            "win_length": win_size,
            "center": True,
            "normalized": normalized,
        }

        self.encoder = nn.ModuleList()
        self.decoder = nn.ModuleList()
        for index in range(len(self.dims) - 1):
            self.encoder.append(
                SDblock(
                    channels_in=self.dims[index],
                    channels_out=self.dims[index + 1],
                    band_configs=self.band_configs,
                    conv_config=conv_config,
                    depths=list(conv_depths),
                )
            )
            self.decoder.insert(
                0,
                nn.Sequential(
                    FusionLayer(channels=self.dims[index + 1]),
                    SUlayer(
                        channels_in=self.dims[index + 1],
                        channels_out=self.dims[index]
                        if index != 0
                        else self.dims[index] * len(self.sources),
                        band_configs=self.band_configs,
                    ),
                ),
            )

        self.separation_net = SeparationNet(
            channels=self.dims[-1], expand=expand, num_layers=num_dplayer
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Separate ``(batch, channels, samples)`` audio into source stems."""
        batch = x.shape[0]
        padding = self.hop_length - x.shape[-1] % self.hop_length
        if (x.shape[-1] + padding) // self.hop_length % 2 == 0:
            padding += self.hop_length
        x = F.pad(x, (0, padding))

        length = x.shape[-1]
        x = torch.stft(x.reshape(-1, length), **self.stft_config, return_complex=True)
        x = torch.view_as_real(x)
        x = x.permute(0, 3, 1, 2).reshape(
            x.shape[0] // self.audio_channels,
            x.shape[3] * self.audio_channels,
            x.shape[1],
            x.shape[2],
        )

        skips: deque[torch.Tensor] = deque()
        lengths: deque[list[int]] = deque()
        original_lengths: deque[list[int]] = deque()
        for layer in self.encoder:
            x, skip, layer_lengths, layer_original_lengths = layer(x)
            skips.append(skip)
            lengths.append(layer_lengths)
            original_lengths.append(layer_original_lengths)

        x = self.separation_net(x)
        for fusion, upscale in self.decoder:
            x = fusion(x, skips.pop())
            x = upscale(x, lengths.pop(), original_lengths.pop())

        frequencies = self.dims[0]
        x = x.view(batch, frequencies, -1, x.shape[-2], x.shape[-1])
        x = x.reshape(-1, 2, x.shape[-2], x.shape[-1]).permute(0, 2, 3, 1)
        x = torch.view_as_complex(x.contiguous())
        x = torch.istft(x, **self.stft_config)
        x = x.reshape(batch, len(self.sources), self.audio_channels, -1)
        return x[..., :-padding]
