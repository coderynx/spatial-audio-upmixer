import numpy as np
from scipy.signal import butter, sosfilt

from upmixer.config import UpmixConfig
from upmixer.formats import ChannelLabel, InputFormat, OutputFormat


def _lfe_filter(
    signal: np.ndarray, sr: int, cutoff_hz: float, gain: float, order: int
) -> np.ndarray:
    sos = butter(order, cutoff_hz / (sr / 2.0), btype="low", output="sos")
    return sosfilt(sos, signal) * gain


def _elevation_eq(
    signal: np.ndarray,
    sr: int,
    low_rolloff_hz: float,
    low_rolloff_gain: float,
    high_shelf_hz: float,
    high_shelf_gain: float,
) -> np.ndarray:
    """Elevation EQ: sub-bass rolloff + high-frequency presence lift.

    Mirrors HeightFilter._build_elevation_mask in the time domain.
    Section 1: HP-based attenuation below low_rolloff_hz
    Section 2: shelf boost above high_shelf_hz
    Midrange preserved at unity.
    """
    nyq = sr / 2.0

    # Sub-bass rolloff: blend original with HPF output
    sos_lp_bass = butter(1, low_rolloff_hz / nyq, btype="low", output="sos")
    low_component = sosfilt(sos_lp_bass, signal)
    # attenuate only the sub-bass portion
    bass_shaped = signal - low_component * (1.0 - low_rolloff_gain)

    # High shelf: blend with HPF signal boosted by (high_shelf_gain - 1)
    sos_hp = butter(2, high_shelf_hz / nyq, btype="high", output="sos")
    hp = sosfilt(sos_hp, bass_shaped)
    return bass_shaped + hp * (high_shelf_gain - 1.0)


class MultichannelUpmixer:
    """Upmix multichannel audio to a higher format.

    Passes through existing channels unchanged and derives missing channels
    using gain-only remixing — no decorrelation, no delays.
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

        # Center
        if "C" not in out and FL is not None and FR is not None:
            out["C"] = 0.35 * (FL + FR)
            C = out["C"]

        # LFE
        if "LFE" not in out:
            src = C if C is not None else ((FL + FR) * 0.5 if FL is not None else None)
            if src is not None:
                out["LFE"] = _lfe_filter(
                    src, sr, cfg.lfe_cutoff_hz, cfg.lfe_gain, cfg.lfe_filter_order
                )

        # Surround: simple gain from front channels (clean, no decorrelation)
        if "SL" not in out:
            src = FL if FL is not None else (BL if BL is not None else None)
            if src is not None:
                out["SL"] = cfg.surround_gain * src
                SL = out["SL"]
        if "SR" not in out:
            src = FR if FR is not None else (BR if BR is not None else None)
            if src is not None:
                out["SR"] = cfg.surround_gain * src
                SR = out["SR"]

        # Back surround: simple gain from surround
        if fmt.has_back:
            if "BL" not in out and SL is not None:
                out["BL"] = cfg.back_gain * SL
                BL = out["BL"]
            if "BR" not in out and SR is not None:
                out["BR"] = cfg.back_gain * SR
                BR = out["BR"]

        # Height channels: high-shelf only, no decorrelation, no delay
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
                out["TFR"] = cfg.height_gain * _elevation_eq(h_src_R, **eq_kwargs)

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
                    out["TBR"] = cfg.height_gain * _elevation_eq(hb_src_R, **eq_kwargs)

        return {label.value: out[label.value] for label in fmt.channels}
