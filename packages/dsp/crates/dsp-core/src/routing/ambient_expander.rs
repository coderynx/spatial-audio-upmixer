//! Fixed-filter ambient expansion for the 7.1.4 prototype.

use super::decorrelate::{velvet_pair_seeded, VelvetFir, VelvetLine};

/// Canonical 7.1.4 destinations, their fixed seeds, and pair side.
pub const AMBIENT_EXPANDER_714: [(&str, u64, usize); 8] = [
    ("SL", 12, 1),
    ("SR", 13, 0),
    ("BL", 16, 0),
    ("BR", 21, 0),
    ("TFL", 39, 0),
    ("TFR", 49, 1),
    ("TBL", 55, 0),
    ("TBR", 81, 1),
];

const SOURCES: [usize; 8] = [0, 1, 0, 1, 2, 3, 2, 3];

/// One fixed, pure-wet velvet FIR for a canonical 7.1.4 destination.
pub fn ambient_expander_fir(sample_rate: u32, destination: &str) -> Option<VelvetFir> {
    AMBIENT_EXPANDER_714
        .iter()
        .find(|(name, _, _)| *name == destination)
        .map(|(_, seed, side)| {
            let pair = velvet_pair_seeded(sample_rate, *seed);
            if *side == 0 {
                pair.0
            } else {
                pair.1
            }
        })
}

/// Stateful fixed filtering from rear/height left/right inputs to 7.1.4.
pub struct FixedAmbientExpander714 {
    lines: Vec<VelvetLine>,
}

impl FixedAmbientExpander714 {
    pub fn new(sample_rate: u32) -> Self {
        Self {
            lines: AMBIENT_EXPANDER_714
                .iter()
                .map(|(name, _, _)| {
                    VelvetLine::new(
                        &ambient_expander_fir(sample_rate, name).expect("canonical destination"),
                    )
                })
                .collect(),
        }
    }

    /// Filter rear-left, rear-right, height-left, and height-right independently.
    pub fn process(&mut self, inputs: [&[f64]; 4]) -> [Vec<f64>; 8] {
        std::array::from_fn(|destination| {
            let mut output = inputs[SOURCES[destination]].to_vec();
            self.lines[destination].process(&mut output);
            output
        })
    }

    pub fn reset(&mut self) {
        for line in &mut self.lines {
            line.reset();
        }
    }
}
