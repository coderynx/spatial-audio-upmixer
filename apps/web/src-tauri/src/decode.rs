use std::io::Cursor;

use rubato::{FftFixedInOut, Resampler};
use symphonia::core::codecs::audio::AudioDecoderOptions;
use symphonia::core::errors::Error as SymphoniaError;
use symphonia::core::formats::probe::Hint;
use symphonia::core::formats::{FormatOptions, TrackType};
use symphonia::core::io::MediaSourceStream;
use symphonia::core::meta::MetadataOptions;

pub struct DecodedAudio {
    pub channels: Vec<Vec<f32>>,
    pub sample_rate: u32,
}

pub fn decode(bytes: Vec<u8>, extension: Option<&str>) -> Result<DecodedAudio, String> {
    let source = MediaSourceStream::new(Box::new(Cursor::new(bytes)), Default::default());
    let mut hint = Hint::new();
    if let Some(extension) = extension {
        hint.with_extension(extension);
    }
    let mut format = symphonia::default::get_probe()
        .probe(
            &hint,
            source,
            FormatOptions::default(),
            MetadataOptions::default(),
        )
        .map_err(|error| format!("Unsupported audio: {error}"))?;
    let track = format
        .default_track(TrackType::Audio)
        .ok_or_else(|| "Audio has no decodable track".to_string())?;
    let codec = track
        .codec_params
        .as_ref()
        .and_then(|params| params.audio())
        .ok_or_else(|| "Audio codec parameters are missing".to_string())?;
    let sample_rate = codec
        .sample_rate
        .ok_or_else(|| "Audio sample rate is missing".to_string())?;
    let channel_count = codec
        .channels
        .as_ref()
        .map(|channels| channels.count())
        .ok_or_else(|| "Audio channel layout is missing".to_string())?;
    let mut decoder = symphonia::default::get_codecs()
        .make_audio_decoder(codec, &AudioDecoderOptions::default())
        .map_err(|error| format!("Unsupported audio codec: {error}"))?;
    let track_id = track.id;
    let mut channels = vec![Vec::new(); channel_count];

    loop {
        let packet = match format.next_packet() {
            Ok(Some(packet)) => packet,
            Ok(None) => break,
            Err(SymphoniaError::ResetRequired) => {
                return Err("Chained audio streams are not supported".into())
            }
            Err(error) => return Err(format!("Could not read audio: {error}")),
        };
        if packet.track_id != track_id {
            continue;
        }
        let decoded = match decoder.decode(&packet) {
            Ok(decoded) => decoded,
            Err(SymphoniaError::DecodeError(_)) | Err(SymphoniaError::IoError(_)) => continue,
            Err(error) => return Err(format!("Could not decode audio: {error}")),
        };
        let mut interleaved = vec![0.0f32; decoded.samples_interleaved()];
        decoded.copy_to_slice_interleaved(&mut interleaved);
        for frame in interleaved.chunks_exact(channel_count) {
            for (channel, sample) in channels.iter_mut().zip(frame) {
                channel.push(*sample);
            }
        }
    }
    if channels.first().is_none_or(Vec::is_empty) {
        return Err("Decoded audio is empty".into());
    }
    Ok(DecodedAudio {
        channels,
        sample_rate,
    })
}

pub fn stereo_48k(decoded: DecodedAudio) -> Result<[Vec<f32>; 2], String> {
    let left = decoded.channels[0].clone();
    let right = decoded
        .channels
        .get(1)
        .cloned()
        .unwrap_or_else(|| left.clone());
    if decoded.sample_rate == 48_000 {
        return Ok([left, right]);
    }
    let input_frames = left.len().min(right.len());
    let mut resampler = FftFixedInOut::<f32>::new(decoded.sample_rate as usize, 48_000, 1024, 2)
        .map_err(|error| format!("Could not create resampler: {error}"))?;
    let delay = resampler.output_delay();
    let mut input = [&left[..input_frames], &right[..input_frames]];
    let mut output = [Vec::new(), Vec::new()];
    let mut needed = resampler.input_frames_next();
    while input[0].len() >= needed {
        let block = resampler
            .process(&[&input[0][..needed], &input[1][..needed]], None)
            .map_err(|error| format!("Could not resample audio: {error}"))?;
        for channel in 0..2 {
            output[channel].extend_from_slice(&block[channel]);
            input[channel] = &input[channel][needed..];
        }
        needed = resampler.input_frames_next();
    }
    if !input[0].is_empty() {
        let block = resampler
            .process_partial(Some(&input), None)
            .map_err(|error| format!("Could not finish resampling audio: {error}"))?;
        for channel in 0..2 {
            output[channel].extend_from_slice(&block[channel]);
        }
    }
    let expected = (input_frames as u64 * 48_000 / decoded.sample_rate as u64) as usize;
    for channel in &mut output {
        if channel.len() > delay {
            channel.drain(..delay);
        }
        channel.truncate(expected);
    }
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn duplicates_mono_and_resamples_to_48k() {
        let mono = (0..4410)
            .map(|i| (i as f32 / 20.0).sin())
            .collect::<Vec<_>>();
        let stereo = stereo_48k(DecodedAudio {
            channels: vec![mono],
            sample_rate: 44_100,
        })
        .unwrap();
        assert_eq!(stereo[0].len(), 4800);
        assert_eq!(stereo[0], stereo[1]);
    }
}
