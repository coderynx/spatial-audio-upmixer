//! Loader for the SciPy-generated fixtures under `tests/golden/`.
//!
//! Regenerate with `uv run python packages/dsp/tools/dump_golden_vectors.py`.

use std::path::PathBuf;

pub struct Case {
    pub name: String,
    pub meta: serde_json::Value,
    pub tolerance: f64,
}

fn golden_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/golden")
}

impl Case {
    pub fn load(name: &str) -> Self {
        let path = golden_dir().join(format!("{name}.json"));
        let text = std::fs::read_to_string(&path)
            .unwrap_or_else(|e| panic!("missing fixture {}: {e}", path.display()));
        let meta: serde_json::Value =
            serde_json::from_str(&text).expect("fixture metadata is not valid JSON");
        let tolerance = meta["tolerance"].as_f64().expect("fixture has no tolerance");
        Self { name: name.to_string(), meta, tolerance }
    }

    pub fn param_f64(&self, key: &str) -> f64 {
        self.meta["params"][key]
            .as_f64()
            .unwrap_or_else(|| panic!("{}: no float param {key}", self.name))
    }

    pub fn param_usize(&self, key: &str) -> usize {
        self.meta["params"][key]
            .as_u64()
            .unwrap_or_else(|| panic!("{}: no integer param {key}", self.name)) as usize
    }

    pub fn param_str(&self, key: &str) -> String {
        self.meta["params"][key]
            .as_str()
            .unwrap_or_else(|| panic!("{}: no string param {key}", self.name))
            .to_string()
    }

    pub fn param_f64_list(&self, key: &str) -> Vec<f64> {
        self.meta["params"][key]
            .as_array()
            .unwrap_or_else(|| panic!("{}: no list param {key}", self.name))
            .iter()
            .map(|v| v.as_f64().expect("list element is not a number"))
            .collect()
    }

    pub fn array(&self, key: &str) -> Vec<f64> {
        let file = self.meta["arrays"][key]["file"]
            .as_str()
            .unwrap_or_else(|| panic!("{}: no array {key}", self.name));
        let bytes = std::fs::read(golden_dir().join(file)).expect("missing fixture blob");
        bytes
            .chunks_exact(8)
            .map(|c| f64::from_le_bytes(c.try_into().expect("8-byte chunk")))
            .collect()
    }

    /// Arrays are dumped flat; SOS fixtures are `(n_sections, 6)`.
    pub fn sos(&self, key: &str) -> Vec<[f64; 6]> {
        self.array(key)
            .chunks_exact(6)
            .map(|c| [c[0], c[1], c[2], c[3], c[4], c[5]])
            .collect()
    }

    pub fn assert_close(&self, got: &[f64], want: &[f64], what: &str) {
        assert_eq!(got.len(), want.len(), "{}: {what} length mismatch", self.name);
        let mut worst = 0.0_f64;
        let mut worst_at = 0usize;
        for (i, (g, w)) in got.iter().zip(want.iter()).enumerate() {
            let d = (g - w).abs();
            if d > worst {
                worst = d;
                worst_at = i;
            }
        }
        assert!(
            worst <= self.tolerance,
            "{}: {what} max |Δ| = {worst:.3e} at index {worst_at} (tolerance {:.1e}); \
             got {:?} want {:?}",
            self.name,
            self.tolerance,
            got[worst_at],
            want[worst_at],
        );
    }
}
