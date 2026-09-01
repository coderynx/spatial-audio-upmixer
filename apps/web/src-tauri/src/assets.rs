use std::fs;
use std::path::Path;

use reqwest::blocking::Client;
use reqwest::Url;
use serde::Deserialize;
use upmixer_dsp_core::stream::engine::PreviewEngine;

use crate::decode::decode;

const SAMPLE_RATE: u32 = 48_000;

#[derive(Clone, Default, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct NativeAssets {
    pub decode_asset: Option<String>,
    pub xtc_asset: Option<String>,
    pub master_eq_asset: Option<String>,
    pub reference_fir_url: Option<String>,
}

pub fn checked_url(base: &Url, value: &str) -> Result<Url, String> {
    let url = Url::parse(value)
        .or_else(|_| base.join(value))
        .map_err(|_| "Invalid preview URL")?;
    if url.scheme() != base.scheme()
        || url.host_str() != base.host_str()
        || url.port_or_known_default() != base.port_or_known_default()
    {
        return Err("Native preview URLs must use the configured processing node".into());
    }
    Ok(url)
}

pub fn load_assets(
    client: &Client,
    server_base: &Url,
    resource_dir: &Path,
    assets: &NativeAssets,
    engine: &mut PreviewEngine,
) -> Result<(), String> {
    if let Some(asset) = &assets.decode_asset {
        safe_asset(asset)?;
        let mut taps = Vec::new();
        for part in ["01-08ch", "09-16ch", "17-24ch", "25-32ch"] {
            let path = resource_dir
                .join("hrir")
                .join(format!("{asset}_{part}.wav"));
            let decoded = decode(
                fs::read(&path)
                    .map_err(|error| format!("Could not read {}: {error}", path.display()))?,
                Some("wav"),
            )?;
            if decoded.sample_rate != SAMPLE_RATE {
                return Err("Decode filters must be 48 kHz".into());
            }
            taps.extend(decoded.channels.into_iter().flatten().map(f64::from));
        }
        engine.set_decode_taps(taps);
    }
    if let Some(asset) = &assets.xtc_asset {
        safe_asset(asset)?;
        let path = resource_dir.join("xtc").join(format!("{asset}.wav"));
        let decoded = decode(
            fs::read(&path)
                .map_err(|error| format!("Could not read {}: {error}", path.display()))?,
            Some("wav"),
        )?;
        engine.set_xtc_taps(
            decoded
                .channels
                .into_iter()
                .flatten()
                .map(f64::from)
                .collect(),
        );
    }
    if let Some(asset) = &assets.master_eq_asset {
        safe_asset(asset)?;
        let path = resource_dir.join("eq_fir").join(format!("{asset}.wav"));
        let decoded = decode(
            fs::read(&path)
                .map_err(|error| format!("Could not read {}: {error}", path.display()))?,
            Some("wav"),
        )?;
        engine.set_master_eq_taps(
            decoded
                .channels
                .into_iter()
                .next()
                .unwrap_or_default()
                .into_iter()
                .map(f64::from)
                .collect(),
        );
    } else {
        engine.set_master_eq_taps(Vec::new());
    }
    if let Some(value) = &assets.reference_fir_url {
        let url = checked_url(server_base, value)?;
        let bytes = client
            .get(url)
            .send()
            .and_then(reqwest::blocking::Response::error_for_status)
            .and_then(reqwest::blocking::Response::bytes)
            .map_err(|error| format!("Could not download reference filter: {error}"))?;
        let decoded = decode(bytes.to_vec(), Some("wav"))?;
        engine.set_reference_taps(
            decoded
                .channels
                .into_iter()
                .next()
                .unwrap_or_default()
                .into_iter()
                .map(f64::from)
                .collect(),
        );
    } else {
        engine.set_reference_taps(Vec::new());
    }
    Ok(())
}

fn safe_asset(value: &str) -> Result<(), String> {
    if value.is_empty()
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
    {
        return Err("Invalid bundled filter identifier".into());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_cross_origin_preview_urls() {
        let base = Url::parse("http://127.0.0.1:8000/root").unwrap();
        assert!(checked_url(&base, "http://localhost:8000/audio.ogg").is_err());
        assert!(checked_url(&base, "/audio.ogg").is_ok());
    }

    #[test]
    fn rejects_asset_traversal() {
        assert!(safe_asset("studio_o3_decode").is_ok());
        assert!(safe_asset("../secret").is_err());
    }
}
