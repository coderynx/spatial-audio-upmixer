//! `scipy.signal.stft(..., window="hann", boundary=None, padded=False)`
//! power spectra, as reference matching consumes them.

use super::fft::RealFft;

/// Periodic Hann, matching `get_window("hann", n)` (`fftbins=True`).
pub fn hann_periodic(n: usize) -> Vec<f64> {
    if n <= 1 {
        return vec![1.0; n];
    }
    (0..n)
        .map(|i| 0.5 - 0.5 * (2.0 * std::f64::consts::PI * i as f64 / n as f64).cos())
        .collect()
}

/// Per-frame power `|STFT|²`, laid out as `n_freqs` rows of `n_frames`.
///
/// `nperseg` is capped to the signal length the way SciPy auto-reduces it, so
/// short signals do not raise. With no boundary extension and no padding,
/// frames that would run past the end are simply not produced.
pub struct FramePower {
    pub n_freqs: usize,
    pub n_frames: usize,
    /// Row-major: `power[freq * n_frames + frame]`.
    pub power: Vec<f64>,
}

impl FramePower {
    pub fn at(&self, freq: usize, frame: usize) -> f64 {
        self.power[freq * self.n_frames + frame]
    }
}

pub fn frame_power(audio: &[f64], n_fft: usize) -> FramePower {
    let nperseg = n_fft.min(audio.len()).max(1);
    let noverlap = (3 * nperseg) / 4;
    let step = (nperseg - noverlap).max(1);
    let n_freqs = nperseg / 2 + 1;
    let n_frames = if audio.len() >= nperseg {
        (audio.len() - nperseg) / step + 1
    } else {
        0
    };

    let mut out = FramePower {
        n_freqs,
        n_frames,
        power: vec![0.0; n_freqs * n_frames],
    };
    if n_frames == 0 {
        return out;
    }

    let window = hann_periodic(nperseg);
    // SciPy's default `scaling="spectrum"` normalizes by the window sum.
    let scale = 1.0 / window.iter().sum::<f64>();
    let fft = RealFft::new(nperseg);
    let mut buffer = vec![0.0; nperseg];

    for frame in 0..n_frames {
        let start = frame * step;
        for i in 0..nperseg {
            buffer[i] = audio[start + i] * window[i];
        }
        for (freq, bin) in fft.rfft(&buffer).iter().enumerate() {
            let scaled = bin * scale;
            out.power[freq * n_frames + frame] = scaled.norm_sqr();
        }
    }
    out
}

/// Bin centre frequencies for a transform of `nperseg` samples.
pub fn frame_frequencies(audio_len: usize, n_fft: usize, sample_rate: u32) -> Vec<f64> {
    let nperseg = n_fft.min(audio_len).max(1);
    let n_freqs = nperseg / 2 + 1;
    (0..n_freqs)
        .map(|i| sample_rate as f64 * i as f64 / nperseg as f64)
        .collect()
}
