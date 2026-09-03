"""Apple MLX port of ordinary SCNet.

Adapted from openmirlab/scnet-infer (MIT, copyright 2026 openmirlab
contributors), revision ``a5437e37c8b942baf74529f35a719aa70dfa9bdc``.
The SCNet math follows starrytong/SCNet revision
``e0e3f4037dad3fc9437499051e73aed466bd2766`` and MSST revision
``83d495dfc81b2ede9bc62f4209619f8bdfd14995``.  This attribution covers
source code only; checkpoint licensing is not asserted here.
"""

from __future__ import annotations

import math

import mlx.core as mx
from mlx import nn
from mlx_spectro import get_transform_mlx


def _cf_to_cl(x):
    return mx.moveaxis(x, 1, -1)


def _cl_to_cf(x):
    return mx.moveaxis(x, -1, 1)


class Conv1dCF(nn.Module):
    """Channels-first wrapper for MLX's channels-last Conv1d."""

    def __init__(self, in_channels, out_channels, kernel_size, stride=1, padding=0, groups=1, bias=True):
        super().__init__()
        self.conv = nn.Conv1d(
            in_channels, out_channels, kernel_size, stride=stride,
            padding=padding, groups=groups, bias=bias,
        )

    def __call__(self, x):
        return _cl_to_cf(self.conv(_cf_to_cl(x)))


class Conv2dCF(nn.Module):
    """Channels-first wrapper for MLX's channels-last Conv2d."""

    def __init__(self, in_channels, out_channels, kernel_size, stride=1, padding=0, bias=True):
        super().__init__()
        self.conv = nn.Conv2d(
            in_channels, out_channels, kernel_size, stride=stride,
            padding=padding, bias=bias,
        )

    def __call__(self, x):
        return _cl_to_cf(self.conv(_cf_to_cl(x)))


class ConvTranspose2dCF(nn.Module):
    """Channels-first wrapper for MLX's channels-last ConvTranspose2d."""

    def __init__(self, in_channels, out_channels, kernel_size, stride=1):
        super().__init__()
        self.conv = nn.ConvTranspose2d(in_channels, out_channels, kernel_size, stride=stride)

    def __call__(self, x):
        return _cl_to_cf(self.conv(_cf_to_cl(x)))


class GroupNormCF(nn.Module):
    """Channels-first wrapper for MLX GroupNorm."""

    def __init__(self, num_groups, num_channels, eps=1e-5):
        super().__init__()
        self.norm = nn.GroupNorm(
            num_groups, num_channels, eps=eps, pytorch_compatible=True,
        )

    def __call__(self, x):
        return _cl_to_cf(self.norm(_cf_to_cl(x)))


class Swish(nn.Module):
    def __call__(self, x):
        return x * mx.sigmoid(x)


class ConvolutionModule(nn.Module):
    """Residual depthwise-separable convolution stack."""

    def __init__(self, channels, depth=2, compress=4, kernel=3):
        super().__init__()
        if kernel % 2 != 1:
            raise ValueError("SCNet convolution kernel must be odd")
        self.depth = abs(depth)
        hidden = int(channels / compress)
        for index in range(self.depth):
            layer = nn.Module()
            layer.norm1 = nn.GroupNorm(1, channels, eps=1e-5, pytorch_compatible=True)
            layer.conv1 = nn.Conv1d(channels, hidden * 2, kernel, padding=kernel // 2)
            layer.conv2 = nn.Conv1d(hidden, hidden, kernel, padding=kernel // 2, groups=hidden)
            layer.norm2 = nn.GroupNorm(1, hidden, eps=1e-5, pytorch_compatible=True)
            layer.conv3 = nn.Conv1d(hidden, channels, 1)
            setattr(self, f"layers_{index}", layer)

    def __call__(self, x):
        y = _cf_to_cl(x)
        for index in range(self.depth):
            layer = getattr(self, f"layers_{index}")
            delta = layer.norm1(y)
            delta = layer.conv1(delta)
            delta = nn.glu(delta, axis=-1)
            delta = layer.conv2(delta)
            delta = layer.norm2(delta)
            delta = delta * mx.sigmoid(delta)
            y = y + layer.conv3(delta)
        return _cl_to_cf(y)


class FusionLayer(nn.Module):
    def __init__(self, channels, kernel_size=3, stride=1, padding=1):
        super().__init__()
        self.conv = Conv2dCF(
            channels * 2, channels * 2, kernel_size, stride=stride, padding=padding,
        )

    def __call__(self, x, skip=None):
        if skip is not None:
            x = x + skip
        x = mx.concatenate([x, x], axis=1)
        return nn.glu(self.conv(x), axis=1)


class SDlayer(nn.Module):
    """Split frequency bands and downsample each band independently."""

    def __init__(self, channels_in, channels_out, band_configs):
        super().__init__()
        self.strides = [config["stride"] for config in band_configs.values()]
        self.kernels = [config["kernel"] for config in band_configs.values()]
        for index, config in enumerate(band_configs.values()):
            setattr(
                self,
                f"band_{index}",
                Conv2dCF(
                    channels_in,
                    channels_out,
                    (config["kernel"], 1),
                    stride=(config["stride"], 1),
                ),
            )
        self.SR_low = band_configs["low"]["SR"]
        self.SR_mid = band_configs["mid"]["SR"]

    def __call__(self, x):
        frequencies = x.shape[2]
        low = math.ceil(frequencies * self.SR_low)
        mid = math.ceil(frequencies * (self.SR_low + self.SR_mid))
        splits = ((0, low), (low, mid), (mid, frequencies))
        outputs, original_lengths = [], []
        for index, (start, end) in enumerate(splits):
            stride, kernel = self.strides[index], self.kernels[index]
            band = x[:, :, start:end, :]
            original_lengths.append(end - start)
            current = band.shape[2]
            total = kernel - stride if stride == 1 else (stride - current % stride) % stride
            left = total // 2
            band = mx.pad(band, [(0, 0), (0, 0), (left, total - left), (0, 0)])
            outputs.append(getattr(self, f"band_{index}")(band))
        return outputs, original_lengths


class SUlayer(nn.Module):
    """Upsample sparse frequency bands in the decoder."""

    def __init__(self, channels_in, channels_out, band_configs):
        super().__init__()
        for index, config in enumerate(band_configs.values()):
            setattr(
                self,
                f"band_{index}",
                ConvTranspose2dCF(
                    channels_in,
                    channels_out,
                    (config["kernel"], 1),
                    stride=(config["stride"], 1),
                ),
            )

    def __call__(self, x, lengths, origin_lengths):
        splits = ((0, lengths[0]), (lengths[0], lengths[0] + lengths[1]), (lengths[0] + lengths[1], None))
        outputs = []
        for index, (start, end) in enumerate(splits):
            band = x[:, :, start:end, :] if end is not None else x[:, :, start:, :]
            band = getattr(self, f"band_{index}")(band)
            distance = abs(origin_lengths[index] - band.shape[2]) // 2
            outputs.append(band[:, :, distance:distance + origin_lengths[index], :])
        return mx.concatenate(outputs, axis=2)


class SDblock(nn.Module):
    """Sparse downsample block with per-band residual processing."""

    def __init__(self, channels_in, channels_out, band_configs, conv_config, depths=(3, 2, 1), kernel_size=3):
        super().__init__()
        self.SDlayer = SDlayer(channels_in, channels_out, band_configs)
        self.num_conv_modules = len(depths)
        for index, depth in enumerate(depths):
            setattr(
                self,
                f"conv_module_{index}",
                ConvolutionModule(channels_out, depth, **conv_config),
            )
        self.globalconv = Conv2dCF(
            channels_out, channels_out, kernel_size,
            stride=1, padding=(kernel_size - 1) // 2,
        )

    def __call__(self, x):
        bands, original_lengths = self.SDlayer(x)
        processed = []
        for index, band in enumerate(bands):
            batch, channels, frequencies, frames = band.shape
            value = mx.transpose(band, (0, 2, 1, 3))
            value = mx.reshape(value, (batch * frequencies, channels, frames))
            value = nn.gelu(getattr(self, f"conv_module_{index}")(value))
            value = mx.reshape(value, (batch, frequencies, channels, frames))
            processed.append(mx.transpose(value, (0, 2, 1, 3)))
        lengths = [band.shape[2] for band in processed]
        full_band = mx.concatenate(processed, axis=2)
        return self.globalconv(full_band), full_band, lengths, original_lengths


class TrunkSTFT:
    """Rectangular STFT/ISTFT matching SCNet's Torch call convention."""

    def __init__(self, *, n_fft, hop_length, win_length, normalized):
        self.n_fft = n_fft
        self.hop_length = hop_length
        self.win_length = win_length
        self._transform = get_transform_mlx(
            n_fft=n_fft,
            hop_length=hop_length,
            win_length=win_length,
            window_fn="rect",
            window=None,
            periodic=True,
            center=True,
            normalized=normalized,
        )

    def stft(self, x):
        with mx.stream(mx.cpu):
            result = self._transform.stft(x.astype(mx.float32))
            mx.eval(result)
        return result

    def istft(self, spec):
        length = self.hop_length * (spec.shape[-1] - 1)
        return self._transform.istft(spec, length=length).astype(mx.float32)


def stft_encode(stft, audio_channels, x):
    """Convert ``(B,C,L)`` audio to SCNet's interleaved real spectrum."""
    x = x.astype(mx.float32)
    length = x.shape[-1]
    padding = stft.hop_length - length % stft.hop_length
    if (length + padding) // stft.hop_length % 2 == 0:
        padding += stft.hop_length
    x = mx.pad(x, [(0, 0), (0, 0), (0, padding)])
    flat = mx.reshape(x, (-1, x.shape[-1]))
    spec = stft.stft(flat)
    real_imag = mx.stack([spec.real, spec.imag], axis=-1)
    frequencies, frames = real_imag.shape[1:3]
    real_imag = mx.transpose(real_imag, (0, 3, 1, 2))
    spec_cf = mx.reshape(
        real_imag,
        (real_imag.shape[0] // audio_channels, 2 * audio_channels, frequencies, frames),
    )
    return spec_cf, padding


def reshape_to_realimag(y, batch, n, frequencies, frames):
    y = mx.reshape(y, (batch, n, -1, frequencies, frames))
    y = mx.reshape(y, (-1, 2, frequencies, frames))
    return mx.transpose(y, (0, 2, 3, 1))


def realimag_to_complex(x):
    return x[..., 0].astype(mx.complex64) + 1j * x[..., 1].astype(mx.complex64)


def stft_decode(stft, x):
    return stft.istft(realimag_to_complex(x))


class BiLSTM(nn.Module):
    """Bidirectional Torch LSTM represented by two MLX LSTM cells."""

    def __init__(self, input_size, hidden_size):
        super().__init__()
        self.forward_cell = nn.LSTM(input_size, hidden_size)
        self.backward_cell = nn.LSTM(input_size, hidden_size)

    def __call__(self, x):
        forward, _ = self.forward_cell(x)
        backward, _ = self.backward_cell(x[:, ::-1, :])
        return mx.concatenate([forward, backward[:, ::-1, :]], axis=-1)


class DualPathRNN(nn.Module):
    """Frequency-then-time bidirectional LSTM paths."""

    def __init__(self, d_model, expand, bidirectional=True):
        super().__init__()
        self.d_model = d_model
        self.hidden_size = d_model * expand
        self.bidirectional = bidirectional
        self.norm_0 = GroupNormCF(1, d_model)
        self.norm_1 = GroupNormCF(1, d_model)
        self.lstm_0 = BiLSTM(d_model, self.hidden_size)
        self.lstm_1 = BiLSTM(d_model, self.hidden_size)
        self.linear_0 = nn.Linear(self.hidden_size * 2, d_model)
        self.linear_1 = nn.Linear(self.hidden_size * 2, d_model)

    def __call__(self, x):
        batch, channels, frequencies, frames = x.shape

        residual = x
        y = self.norm_0(x)
        y = mx.transpose(y, (0, 3, 2, 1))
        y = mx.reshape(y, (batch * frames, frequencies, channels))
        y = self.linear_0(self.lstm_0(y))
        y = mx.reshape(y, (batch, frames, frequencies, channels))
        x = mx.transpose(y, (0, 3, 2, 1)) + residual

        residual = x
        y = self.norm_1(x)
        y = mx.transpose(y, (0, 2, 1, 3))
        y = mx.reshape(y, (batch * frequencies, channels, frames))
        y = mx.transpose(y, (0, 2, 1))
        y = self.linear_1(self.lstm_1(y))
        y = mx.transpose(y, (0, 2, 1))
        y = mx.reshape(y, (batch, frequencies, channels, frames))
        return mx.transpose(y, (0, 2, 1, 3)) + residual


class FeatureConversion(nn.Module):
    """Alternate split-complex rFFT and inverse-rFFT feature maps."""

    def __init__(self, channels, inverse):
        super().__init__()
        self.inverse = inverse
        self.channels = channels

    def __call__(self, x):
        if self.inverse:
            half = self.channels // 2
            real = x[:, :half, :, :].astype(mx.float32)
            imag = x[:, half:, :, :].astype(mx.float32)
            spec = real.astype(mx.complex64) + 1j * imag.astype(mx.complex64)
            return mx.fft.irfft(spec, axis=3, norm="ortho").astype(mx.float32)
        with mx.stream(mx.cpu):
            spec = mx.fft.rfft(x.astype(mx.float32), axis=3, norm="ortho")
            mx.eval(spec)
        return mx.concatenate([spec.real, spec.imag], axis=1).astype(mx.float32)


class SeparationNet(nn.Module):
    def __init__(self, channels, expand=1, num_layers=6):
        super().__init__()
        self.num_layers = num_layers
        for index in range(num_layers):
            d_model = channels * (2 if index % 2 else 1)
            setattr(self, f"dp_{index}", DualPathRNN(d_model, expand))
            setattr(
                self,
                f"feature_conversion_{index}",
                FeatureConversion(channels * 2, inverse=index % 2 != 0),
            )

    def __call__(self, x):
        for index in range(self.num_layers):
            x = getattr(self, f"dp_{index}")(x)
            x = getattr(self, f"feature_conversion_{index}")(x)
        return x


def _band_configs(band_sr, band_stride, band_kernel):
    return {
        key: {"SR": band_sr[index], "stride": band_stride[index], "kernel": band_kernel[index]}
        for index, key in enumerate(("low", "mid", "high"))
    }


class SCNetMLX(nn.Module):
    """Ordinary SCNet with rectangular trunk STFT and LSTM separation."""

    def __init__(
        self,
        sources=("drums", "bass", "other", "vocals"),
        audio_channels=2,
        dims=(4, 32, 64, 128),
        nfft=4096,
        hop_size=1024,
        win_size=4096,
        normalized=True,
        band_SR=(0.175, 0.392, 0.433),
        band_stride=(1, 4, 16),
        band_kernel=(3, 4, 16),
        conv_depths=(3, 2, 1),
        compress=4,
        conv_kernel=3,
        num_dplayer=6,
        expand=1,
    ):
        super().__init__()
        self.sources = list(sources)
        self.audio_channels = audio_channels
        self.dims = list(dims)
        bands = _band_configs(band_SR, band_stride, band_kernel)
        conv_config = {"compress": compress, "kernel": conv_kernel}
        self.stft = TrunkSTFT(
            n_fft=nfft, hop_length=hop_size, win_length=win_size, normalized=normalized,
        )

        self.num_stages = len(dims) - 1
        decoders = []
        for index in range(self.num_stages):
            setattr(
                self,
                f"encoder_{index}",
                SDblock(dims[index], dims[index + 1], bands, conv_config, depths=conv_depths),
            )
            channels_out = dims[index] if index else dims[index] * len(self.sources)
            decoders.insert(
                0,
                (
                    FusionLayer(dims[index + 1]),
                    SUlayer(dims[index + 1], channels_out, bands),
                ),
            )
        for index, (fusion, upscale) in enumerate(decoders):
            setattr(self, f"decoder_fusion_{index}", fusion)
            setattr(self, f"decoder_su_{index}", upscale)
        self.separation_net = SeparationNet(
            channels=dims[-1], expand=expand, num_layers=num_dplayer,
        )

    def __call__(self, x):
        batch = x.shape[0]
        y, padding = stft_encode(self.stft, self.audio_channels, x)
        frequencies, frames = y.shape[2:]
        skips, lengths, original_lengths = [], [], []
        for index in range(self.num_stages):
            y, skip, layer_lengths, layer_original_lengths = getattr(self, f"encoder_{index}")(y)
            skips.append(skip)
            lengths.append(layer_lengths)
            original_lengths.append(layer_original_lengths)

        y = self.separation_net(y)
        for index in range(self.num_stages):
            y = getattr(self, f"decoder_fusion_{index}")(y, skips.pop())
            y = getattr(self, f"decoder_su_{index}")(y, lengths.pop(), original_lengths.pop())

        pairs = reshape_to_realimag(y, batch, self.dims[0], frequencies, frames)
        waveform = stft_decode(self.stft, pairs)
        waveform = mx.reshape(waveform, (batch, len(self.sources), self.audio_channels, -1))
        return waveform[:, :, :, :-padding] if padding else waveform


__all__ = [
    "BiLSTM",
    "Conv1dCF",
    "Conv2dCF",
    "ConvTranspose2dCF",
    "FeatureConversion",
    "SCNetMLX",
    "TrunkSTFT",
    "stft_decode",
    "stft_encode",
]
