import numpy as np
import upmixer_dsp

from upmixer.config import UpmixConfig
from upmixer.analysis.coherence import CoherenceEstimator
from upmixer.analysis.spatial import SpatialPlan
from upmixer.analysis.stft import STFTAnalyzer
from upmixer.decomposition.direct_ambient import center_weight
from upmixer.formats import ChannelLabel, InputFormat, OutputFormat
from upmixer.utils import (
    elevation_eq as _elevation_eq,
    velvet_send,
    HEIGHT_VELVET_SEED,
    ITU_CENTER_COEFF,
    SURROUND_VELVET_SEED,
)


def _extract_center(
    FL: np.ndarray, FR: np.ndarray, cfg: UpmixConfig, sr: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (C, residual FL, residual FR) for a front pair with no center.

    Extraction is full (no partial gain) and C carries the extracted mid
    divided by the BS.775 fold coefficient: only that pairing keeps both the
    stereo fold-down of the result identical to the input fronts and the
    front-triple energy equal to the input pair's. See
    `docs/standards/spatial_layouts_bs775_bs2051.md` § "Deriving a missing
    centre".
    """
    stft = STFTAnalyzer(cfg, sr)
    X_L = stft.forward(FL)
    X_R = stft.forward(FR)

    estimator = CoherenceEstimator(cfg)
    state = estimator.create_state(stft.n_freq_bins)
    directness = np.empty(X_L.shape, dtype=np.float64)
    for i in range(X_L.shape[1]):
        estimator.estimate_frame(X_L[:, i], X_R[:, i], state)
        directness[:, i] = estimator.directness_frame(state)

    extracted = center_weight(X_L, X_R, directness, cfg.epsilon) * (X_L + X_R) * 0.5
    n = len(FL)
    return (
        stft.inverse(extracted / ITU_CENTER_COEFF, n),
        stft.inverse(X_L - extracted, n),
        stft.inverse(X_R - extracted, n),
    )


def _lfe_filter(
    signal: np.ndarray, sr: int, cutoff_hz: float, gain: float, order: int
) -> np.ndarray:
    filtered = upmixer_dsp.lfe_lowpass(
        np.ascontiguousarray(signal, dtype=np.float64), sr, cutoff_hz, order
    )
    return filtered * gain


class MultichannelUpmixer:
    """Upmix multichannel audio to a higher format.

    Passes through existing channels unchanged. Derives missing channels
    using gain remixing plus velvet-noise decorrelation — one side of a pair
    per derived channel, so neither side of a pair is a plain copy of its
    source and their fold-down cannot cancel.

    A missing center is the one exception to pass-through: it is extracted
    subtractively from FL/FR by coherence (see `_extract_center` and
    `upmixer.decomposition`), so FL/FR are replaced by the residual fronts
    rather than replaying the centered content alongside C. Every other
    derivation, including LFE, reads the original FL/FR — a residual front
    has centered content missing, which those sends want to keep.
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
        self, input_channels: dict[ChannelLabel, np.ndarray], spatial_plan: SpatialPlan | None = None
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
            out["C"], out["FL"], out["FR"] = _extract_center(FL, FR, cfg, sr)
            C = out["C"]

        if "LFE" not in out:
            src = (
                (FL + FR) * 0.5
                if FL is not None and FR is not None
                else C
            )
            if src is not None:
                out["LFE"] = _lfe_filter(
                    src, sr, cfg.lfe_cutoff_hz, cfg.lfe_gain, cfg.lfe_filter_order
                )

        def surround(signal: np.ndarray, side: str) -> np.ndarray:
            return velvet_send(signal, sr, side, SURROUND_VELVET_SEED)

        def height(signal: np.ndarray, side: str) -> np.ndarray:
            return velvet_send(signal, sr, side, HEIGHT_VELVET_SEED)

        if "SL" not in out:
            src = FL if FL is not None else (BL if BL is not None else None)
            if src is not None:
                out["SL"] = cfg.surround_gain * surround(src, "left")
                SL = out["SL"]
        if "SR" not in out:
            src = FR if FR is not None else (BR if BR is not None else None)
            if src is not None:
                out["SR"] = cfg.surround_gain * surround(src, "right")
                SR = out["SR"]

        if fmt.has_back:
            if "BL" not in out and SL is not None:
                out["BL"] = cfg.back_gain * surround(SL, "left")
                BL = out["BL"]
            if "BR" not in out and SR is not None:
                out["BR"] = cfg.back_gain * surround(SR, "right")
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
                out["TFL"] = cfg.height_gain * _elevation_eq(
                    height(h_src_L, "left"), **eq_kwargs
                )
            if "TFR" not in out:
                out["TFR"] = cfg.height_gain * _elevation_eq(
                    height(h_src_R, "right"), **eq_kwargs
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
                    out["TBL"] = cfg.height_gain * _elevation_eq(
                        height(hb_src_L, "left"), **eq_kwargs
                    )
                if "TBR" not in out:
                    out["TBR"] = cfg.height_gain * _elevation_eq(
                        height(hb_src_R, "right"), **eq_kwargs
                    )

        result = {label.value: out[label.value] for label in fmt.channels}
        if spatial_plan is not None:
            n = len(next(iter(result.values())))
            points = np.arange(n)
            plan_points = np.arange(len(spatial_plan.front)) * spatial_plan.hop_size
            def envelope(values: np.ndarray) -> np.ndarray:
                return np.interp(points, plan_points, values) if len(values) else np.ones(n)
            detail = 1.0 + 0.4125 * envelope(spatial_plan.detail)
            for label in fmt.channels:
                # Existing programme channels always remain untouched.
                if label in input_channels:
                    continue
                if label in {ChannelLabel.FL, ChannelLabel.FR, ChannelLabel.C}:
                    result[label.value] *= envelope(spatial_plan.front)
                elif label in {ChannelLabel.BL, ChannelLabel.BR}:
                    result[label.value] *= envelope(spatial_plan.back) * detail
                elif label in {ChannelLabel.SL, ChannelLabel.SR}:
                    result[label.value] *= envelope(spatial_plan.surround) * detail
                elif label in {ChannelLabel.TFL, ChannelLabel.TFR, ChannelLabel.TBL, ChannelLabel.TBR}:
                    result[label.value] *= envelope(spatial_plan.height) * detail
        return result
