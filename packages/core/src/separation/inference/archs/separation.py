"""Dual-path recurrent layers used by the SCNet inference architecture."""
from __future__ import annotations

import torch
from torch import nn
from torch.nn.modules.rnn import LSTM


class FeatureConversion(nn.Module):
    """Convert a real feature map to/from the split-complex representation."""

    def __init__(self, channels: int, inverse: bool) -> None:
        super().__init__()
        self.inverse = inverse
        self.channels = channels

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if self.inverse:
            x = x.float()
            real = x[:, : self.channels // 2]
            imaginary = x[:, self.channels // 2 :]
            return torch.fft.irfft(torch.complex(real, imaginary), dim=3, norm="ortho")

        x = torch.fft.rfft(x.float(), dim=3, norm="ortho")
        return torch.cat([x.real, x.imag], dim=1)


class DualPathRNN(nn.Module):
    """Run bidirectional LSTMs over frequency and time paths."""

    def __init__(self, d_model: int, expand: int, bidirectional: bool = True) -> None:
        super().__init__()
        self.d_model = d_model
        self.hidden_size = d_model * expand
        self.bidirectional = bidirectional
        self.lstm_layers = nn.ModuleList(
            [LSTM(d_model, self.hidden_size, bidirectional=bidirectional, batch_first=True) for _ in range(2)]
        )
        direction_factor = 2 if bidirectional else 1
        self.linear_layers = nn.ModuleList(
            [nn.Linear(self.hidden_size * direction_factor, d_model) for _ in range(2)]
        )
        self.norm_layers = nn.ModuleList([nn.GroupNorm(1, d_model) for _ in range(2)])

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        batch, channels, frequencies, frames = x.shape

        residual = x
        x = self.norm_layers[0](x)
        x = x.transpose(1, 3).contiguous().view(batch * frames, frequencies, channels)
        x, _ = self.lstm_layers[0](x)
        x = self.linear_layers[0](x)
        x = x.view(batch, frames, frequencies, channels).transpose(1, 3)
        x = x + residual

        residual = x
        x = self.norm_layers[1](x)
        x = x.transpose(1, 2).contiguous().view(batch * frequencies, channels, frames)
        x, _ = self.lstm_layers[1](x.transpose(1, 2))
        x = self.linear_layers[1](x)
        x = x.transpose(1, 2).contiguous().view(batch, frequencies, channels, frames)
        return x.transpose(1, 2) + residual


class SeparationNet(nn.Module):
    """Stack dual-path RNNs with alternating frequency transforms."""

    def __init__(self, channels: int, expand: int = 1, num_layers: int = 6) -> None:
        super().__init__()
        self.num_layers = num_layers
        self.dp_modules = nn.ModuleList(
            [DualPathRNN(channels * (2 if i % 2 else 1), expand) for i in range(num_layers)]
        )
        self.feature_conversion = nn.ModuleList(
            [
                FeatureConversion(channels * 2, inverse=i % 2 == 1)
                for i in range(num_layers)
            ]
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        for layer, conversion in zip(self.dp_modules, self.feature_conversion):
            x = conversion(layer(x))
        return x
