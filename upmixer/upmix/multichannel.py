import numpy as np
from scipy.signal import butter, sosfilt

from upmixer.config import UpmixConfig
from upmixer.formats import ChannelLabel, InputFormat, OutputFormat
from upmixer.utils import (
    elevation_eq as _elevation_eq,
    haas_decorrelate,
    diffuse_send,
    _ITU_C_COEFF,
)


def _lfe_filter(
    signal: np.ndarray, sr: int, cutoff_hz: float, gain: float, order: int
) -> np.ndarray:
    sos = butter(order, cutoff_hz / (sr / 2.0), btype="low", output="sos")
    return sosfilt(sos, signal) * gain


class MultichannelUpmixer:
    """Upmix multichannel audio to a higher format.

    Passes through existing channels unchanged. Derives missing channels
    using gain remixing + Haas decorrelation (right spatial channels) +
    early-reflection diffusion (surround/height sources).
    """

    def __init__(
        self,
        config: UpmixConfig,
        input_fmt: InputFormat,
        output_fmt: OutputFormat,
        sample_rate: int,
    ):
        self._cfg = config
        self._input_fmt = input_fmt
        self._output_fmt = output_fmt
        self._sr = sample_rate

    def process(
        self, input_channels: dict[ChannelLabel, np.ndarray]
    ) -> dict[str, np.ndarray]:
        """Pass through existing channels and derive any missing output channels."""
        cfg = self._cfg
        sr = self._sr
        fmt = self._output_fmt

        out: dict[str, np.ndarray] = {
            label.value: arr.copy() for label, arr in input_channels.items()
        }

        FL = out.get("FL")
        FR = out.get("FR")
        C = out.get("C")
        SL = out.get("SL")
        SR = out.get("SR")
        BL = out.get("BL")
        BR = out.get("BR")

        if "C" not in out and FL is not None and FR is not None:
            out["C"] = (_ITU_C_COEFF * 0.5) * (FL + FR)
            C = out["C"]

        if "LFE" not in out:
            src = C if C is not None else ((FL + FR) * 0.5 if FL is not None else None)
            if src is not None:
                out["LFE"] = _lfe_filter(
                    src, sr, cfg.lfe_cutoff_hz, cfg.lfe_gain, cfg.lfe_filter_order
                )

        if "SL" not in out:
            src = FL if FL is not None else (BL if BL is not None else None)
            if src is not None:
                out["SL"] = cfg.surround_gain * diffuse_send(src, sr)
                SL = out["SL"]
        if "SR" not in out:
            src = FR if FR is not None else (BR if BR is not None else None)
            if src is not None:
                diffused = diffuse_send(src, sr)
                out["SR"] = cfg.surround_gain * haas_decorrelate(
                    diffused, int(sr * 23.0 / 1000.0)
                )
                SR = out["SR"]

        if fmt.has_back:
            if "BL" not in out and SL is not None:
                out["BL"] = cfg.back_gain * diffuse_send(SL, sr)
                BL = out["BL"]
            if "BR" not in out and SR is not None:
                out["BR"] = cfg.back_gain * haas_decorrelate(
                    diffuse_send(SR, sr), int(sr * 19.0 / 1000.0)
                )
                BR = out["BR"]

        if fmt.has_height:
            n = len(next(iter(out.values())))

            if FL is not None:
                sl_L = SL * 0.3 if SL is not None else np.zeros_like(FL)
                sl_R = SR * 0.3 if SR is not None else np.zeros_like(FR)
                h_src_L = FL * 0.5 + sl_L
                h_src_R = FR * 0.5 + sl_R
            elif SL is not None:
                h_src_L = SL
                h_src_R = SR if SR is not None else SL
            else:
                h_src_L = h_src_R = np.zeros(n)

            eq_kwargs = dict(
                sr=sr,
                low_rolloff_hz=cfg.height_low_rolloff_hz,
                low_rolloff_gain=cfg.height_low_rolloff_gain,
                high_shelf_hz=cfg.height_crossover_hz,
                high_shelf_gain=cfg.height_high_shelf_gain,
            )

            if "TFL" not in out:
                out["TFL"] = cfg.height_gain * _elevation_eq(h_src_L, **eq_kwargs)
            if "TFR" not in out:
                out["TFR"] = cfg.height_gain * _elevation_eq(
                    haas_decorrelate(h_src_R, int(sr * 17.0 / 1000.0)), **eq_kwargs
                )

            if fmt.n_height_channels == 4:
                if SL is not None:
                    bl_L = BL * 0.3 if BL is not None else np.zeros_like(SL)
                    bl_R = BR * 0.3 if BR is not None else np.zeros_like(SR)
                    hb_src_L = SL * 0.5 + bl_L
                    hb_src_R = SR * 0.5 + bl_R
                else:
                    hb_src_L, hb_src_R = h_src_L, h_src_R

                if "TBL" not in out:
                    out["TBL"] = cfg.height_gain * _elevation_eq(hb_src_L, **eq_kwargs)
                if "TBR" not in out:
                    out["TBR"] = cfg.height_gain * _elevation_eq(
                        haas_decorrelate(hb_src_R, int(sr * 13.0 / 1000.0)), **eq_kwargs
                    )

        return {label.value: out[label.value] for label in fmt.channels}
