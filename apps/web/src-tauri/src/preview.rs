use std::collections::VecDeque;
use std::path::{Path, PathBuf};
use std::sync::mpsc::{Receiver, TryRecvError};
use std::thread;
use std::time::Duration;

use reqwest::blocking::Client;
use reqwest::Url;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri::ipc::Channel;
use upmixer_dsp_core::stream::engine::{PreviewEngine, StemSource};
use upmixer_dsp_core::stream::measure::MeasurementPass;
use upmixer_dsp_core::stream::params::{EngineParams, OutputMode};
use upmixer_dsp_core::stream::scale::RouteScalePass;

pub use crate::assets::NativeAssets;
use crate::assets::{checked_url, load_assets};
use crate::audio::AudioHost;
use crate::decode::{decode, stereo_48k};

const SAMPLE_RATE: usize = 48_000;
const QUANTUM: usize = 512;
const REPORT_BLOCKS: usize = 3;
const STEM_LOAD_CONCURRENCY: usize = 3;

#[derive(Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct NativeSource {
    pub key: String,
    pub url: String,
    pub channels: usize,
}

#[derive(Clone, Copy, Deserialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum NativeRenderer {
    Direct,
    AppleSpatial,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OpenRequest {
    pub server_base: String,
    pub sources: Vec<NativeSource>,
    pub params: Value,
    pub assets: NativeAssets,
    pub renderer: NativeRenderer,
}

pub enum Command {
    Update {
        params: Value,
        assets: NativeAssets,
        renderer: NativeRenderer,
    },
    Transport {
        playing: Option<bool>,
        looping: Option<bool>,
        frame: Option<usize>,
    },
    Seek(usize),
    Measure {
        weights: Vec<f64>,
        request_id: u64,
    },
    Monitor {
        volume: f32,
        muted: bool,
    },
    Close,
}

#[derive(Clone, Serialize)]
#[serde(
    tag = "type",
    rename_all = "camelCase",
    rename_all_fields = "camelCase"
)]
pub enum NativeEvent {
    LoadProgress {
        progress: f64,
    },
    Ready {
        core_version: String,
        total_frames: usize,
        max_channels: usize,
    },
    Frame {
        position: usize,
        meters: Vec<f32>,
        spectrum: Vec<f32>,
        underruns: u64,
    },
    Measuring {
        progress: f64,
    },
    Measured {
        stage: String,
        request_id: u64,
        lkfs: f64,
        dbtp: f64,
        monitor_lkfs: f64,
        monitor_dbtp: f64,
    },
    Ended,
    Error {
        message: String,
    },
}

pub fn run(
    request: OpenRequest,
    resource_dir: PathBuf,
    commands: Receiver<Command>,
    events: Channel<NativeEvent>,
) {
    if let Err(message) =
        Session::load(request, resource_dir, commands, events.clone()).and_then(Session::run)
    {
        let _ = events.send(NativeEvent::Error { message });
    }
}

struct Session {
    engine: PreviewEngine,
    audio: AudioHost,
    resource_dir: PathBuf,
    server_base: Url,
    client: Client,
    assets: NativeAssets,
    renderer: NativeRenderer,
    commands: Receiver<Command>,
    events: Channel<NativeEvent>,
    playing: bool,
    looping: bool,
    volume: f32,
    muted: bool,
    report_blocks: usize,
    scheduled_frame: usize,
    pending_frames: VecDeque<PendingFrame>,
    measurement: Option<MeasurementState>,
    route_scale: Option<ScaleState>,
    background_turn: bool,
    current_gain: f32,
    flat: Vec<f32>,
    channels: Vec<Vec<f32>>,
}

struct MeasurementState {
    pass: MeasurementPass,
    weights: Vec<f64>,
    request_id: u64,
    exact: bool,
}

struct ScaleState {
    pass: RouteScalePass,
    exact: bool,
}

struct PendingFrame {
    presented_at: usize,
    meters: Vec<f32>,
    spectrum: Vec<f32>,
}

fn take_presented_frame(
    frames: &mut VecDeque<PendingFrame>,
    presented_at: usize,
) -> Option<PendingFrame> {
    let mut latest = None;
    while frames
        .front()
        .is_some_and(|frame| frame.presented_at <= presented_at)
    {
        latest = frames.pop_front();
    }
    latest
}

impl Session {
    fn load(
        request: OpenRequest,
        resource_dir: PathBuf,
        commands: Receiver<Command>,
        events: Channel<NativeEvent>,
    ) -> Result<Self, String> {
        let server_base =
            Url::parse(&request.server_base).map_err(|_| "Invalid processing-node URL")?;
        if !matches!(server_base.scheme(), "http" | "https") {
            return Err("Processing-node URL must use HTTP or HTTPS".into());
        }
        let client = Client::builder()
            .connect_timeout(Duration::from_secs(10))
            .timeout(Duration::from_secs(120))
            .build()
            .map_err(|error| format!("Could not create HTTP client: {error}"))?;
        let params = parse_params(request.params)?;
        let mut engine = PreviewEngine::new(SAMPLE_RATE as u32, params, Vec::new());
        let total = request.sources.len().max(1);
        let mut loaded = 0;
        for sources in request.sources.chunks(STEM_LOAD_CONCURRENCY) {
            let stems = thread::scope(|scope| {
                sources
                    .iter()
                    .map(|source| {
                        let client = client.clone();
                        let server_base = &server_base;
                        scope.spawn(move || load_stem(&client, server_base, source))
                    })
                    .collect::<Vec<_>>()
                    .into_iter()
                    .map(|handle| {
                        handle
                            .join()
                            .map_err(|_| "Native stem loader stopped unexpectedly".to_string())?
                    })
                    .collect::<Result<Vec<_>, String>>()
            })?;
            for stem in stems {
                engine.push_stem(stem);
                loaded += 1;
                let _ = events.send(NativeEvent::LoadProgress {
                    progress: loaded as f64 / total as f64,
                });
            }
        }
        load_assets(
            &client,
            &server_base,
            &resource_dir,
            &request.assets,
            &mut engine,
        )?;
        let layout = output_layout(engine.params(), request.renderer)?;
        let audio = AudioHost::new(
            layout,
            request.renderer == NativeRenderer::AppleSpatial,
            engine.position(),
        )?;
        audio.pause();
        let _ = events.send(NativeEvent::Ready {
            core_version: upmixer_dsp_core::version().to_string(),
            total_frames: engine.total_frames(),
            max_channels: 12,
        });
        let route_scale = (engine.stem_count() > 0).then(|| ScaleState {
            pass: RouteScalePass::new_excerpts(&engine, 5, SAMPLE_RATE * 3, SAMPLE_RATE / 2),
            exact: false,
        });
        let scheduled_frame = engine.position();
        Ok(Self {
            engine,
            audio,
            resource_dir,
            server_base,
            client,
            assets: request.assets,
            renderer: request.renderer,
            commands,
            events,
            playing: false,
            looping: false,
            volume: 1.0,
            muted: false,
            report_blocks: 0,
            scheduled_frame,
            pending_frames: VecDeque::new(),
            measurement: None,
            route_scale,
            background_turn: true,
            current_gain: 1.0,
            flat: Vec::new(),
            channels: Vec::new(),
        })
    }

    fn run(mut self) -> Result<(), String> {
        loop {
            loop {
                match self.commands.try_recv() {
                    Ok(Command::Close) | Err(TryRecvError::Disconnected) => return Ok(()),
                    Ok(command) => self.command(command)?,
                    Err(TryRecvError::Empty) => break,
                }
            }
            if self.playing {
                self.render_block()?;
            } else {
                self.advance_background();
                thread::sleep(Duration::from_millis(1));
            }
        }
    }

    fn command(&mut self, command: Command) -> Result<(), String> {
        match command {
            Command::Update {
                params,
                assets,
                renderer,
            } => {
                let params = parse_params(params)?;
                let old_layout = output_layout(self.engine.params(), self.renderer)?.to_string();
                self.engine.update_params(params);
                if assets != self.assets {
                    load_assets(
                        &self.client,
                        &self.server_base,
                        &self.resource_dir,
                        &assets,
                        &mut self.engine,
                    )?;
                    self.assets = assets;
                }
                let new_layout = output_layout(self.engine.params(), renderer)?;
                if renderer != self.renderer || old_layout != new_layout {
                    self.audio = AudioHost::new(
                        new_layout,
                        renderer == NativeRenderer::AppleSpatial,
                        self.engine.position(),
                    )?;
                    self.scheduled_frame = self.engine.position();
                    self.pending_frames.clear();
                    if self.playing {
                        self.audio.resume();
                    } else {
                        self.audio.pause();
                    }
                }
                self.renderer = renderer;
                if self.engine.stem_count() > 0 && !self.engine.has_route_scales() {
                    self.route_scale = Some(ScaleState {
                        pass: RouteScalePass::new_excerpts(
                            &self.engine,
                            5,
                            SAMPLE_RATE * 3,
                            SAMPLE_RATE / 2,
                        ),
                        exact: false,
                    });
                }
            }
            Command::Transport {
                playing,
                looping,
                frame,
            } => {
                if let Some(looping) = looping {
                    self.looping = looping;
                }
                if let Some(frame) = frame {
                    self.seek(frame)?;
                }
                if let Some(playing) = playing {
                    self.playing = playing;
                    if playing {
                        self.audio.resume();
                    } else {
                        self.audio.pause();
                    }
                }
            }
            Command::Seek(frame) => self.seek(frame)?,
            Command::Measure {
                weights,
                request_id,
            } => {
                self.measurement = Some(MeasurementState {
                    pass: MeasurementPass::new_excerpts(
                        &self.engine,
                        &weights,
                        3,
                        SAMPLE_RATE,
                        SAMPLE_RATE / 4,
                    ),
                    weights,
                    request_id,
                    exact: false,
                });
            }
            Command::Monitor { volume, muted } => {
                self.volume = volume.clamp(0.0, 1.0);
                self.muted = muted;
            }
            Command::Close => return Ok(()),
        }
        Ok(())
    }

    fn seek(&mut self, frame: usize) -> Result<(), String> {
        self.engine.seek(frame);
        let layout = output_layout(self.engine.params(), self.renderer)?;
        self.audio = AudioHost::new(
            layout,
            self.renderer == NativeRenderer::AppleSpatial,
            self.engine.position(),
        )?;
        self.scheduled_frame = self.engine.position();
        self.pending_frames.clear();
        if self.playing {
            self.audio.resume();
        } else {
            self.audio.pause();
        }
        Ok(())
    }

    fn render_block(&mut self) -> Result<(), String> {
        let channel_count = self.engine.output_channels();
        self.flat.resize(channel_count * QUANTUM, 0.0);
        self.flat.fill(0.0);
        let written = self.engine.render_f32(&mut self.flat, QUANTUM);
        if written == 0 {
            if self.looping {
                self.engine.rewind();
                return Ok(());
            }
            self.playing = false;
            self.audio.pause();
            let _ = self.events.send(NativeEvent::Ended);
            return Ok(());
        }
        let target_gain = if self.muted { 0.0 } else { self.volume };
        let gain_step = (target_gain - self.current_gain) / written as f32;
        self.channels.resize_with(channel_count, Vec::new);
        for channel in 0..channel_count {
            self.channels[channel].resize(written, 0.0);
            for frame in 0..written {
                let gain = self.current_gain + gain_step * (frame + 1) as f32;
                self.channels[channel][frame] = self.flat[channel * QUANTUM + frame] * gain;
            }
        }
        self.current_gain = target_gain;
        self.audio.schedule(&self.channels, written)?;
        self.scheduled_frame += written;
        self.report_blocks += 1;
        if self.report_blocks >= REPORT_BLOCKS {
            self.report_blocks = 0;
            let meters = {
                let meters = self.engine.meters();
                let mut values = vec![0.0; meters.len()];
                meters.write(&mut values);
                values
            };
            let spectrum = self
                .engine
                .stem_spectrum()
                .into_iter()
                .flat_map(|(level, centroid)| [level as f32, centroid as f32])
                .collect();
            if self.audio.playback_frame().is_some() {
                self.pending_frames.push_back(PendingFrame {
                    presented_at: self.scheduled_frame,
                    meters,
                    spectrum,
                });
            } else {
                let _ = self.events.send(NativeEvent::Frame {
                    position: self.engine.position(),
                    meters,
                    spectrum,
                    underruns: 0,
                });
            }
        }
        if let Some(presented_at) = self.audio.playback_frame() {
            if let Some(frame) = take_presented_frame(&mut self.pending_frames, presented_at) {
                let position = if self.looping && self.engine.total_frames() > 0 {
                    presented_at % self.engine.total_frames()
                } else {
                    presented_at.min(self.engine.total_frames())
                };
                let _ = self.events.send(NativeEvent::Frame {
                    position,
                    meters: frame.meters,
                    spectrum: frame.spectrum,
                    underruns: 0,
                });
            }
        }
        Ok(())
    }

    fn advance_background(&mut self) {
        let advance_scale =
            self.route_scale.is_some() && (self.measurement.is_none() || self.background_turn);
        self.background_turn = !self.background_turn;
        if advance_scale {
            let state = self
                .route_scale
                .as_mut()
                .expect("route scale checked above");
            if let Some(scales) = state.pass.advance(4096) {
                self.engine.set_route_scales(scales);
                if state.exact {
                    self.route_scale = None;
                } else {
                    self.route_scale = Some(ScaleState {
                        pass: RouteScalePass::new(&self.engine),
                        exact: true,
                    });
                }
            }
            return;
        }
        let Some(state) = &mut self.measurement else {
            return;
        };
        let step = if state.exact { 384 } else { 2048 };
        let progress = state.pass.progress();
        let _ = self.events.send(NativeEvent::Measuring { progress });
        if let Some([lkfs, dbtp, monitor_lkfs, monitor_dbtp]) = state.pass.advance(step) {
            let stage = if state.exact { "exact" } else { "fast" }.to_string();
            let _ = self.events.send(NativeEvent::Measured {
                stage,
                request_id: state.request_id,
                lkfs,
                dbtp,
                monitor_lkfs,
                monitor_dbtp,
            });
            if state.exact {
                self.measurement = None;
            } else {
                self.measurement = Some(MeasurementState {
                    pass: MeasurementPass::new(&self.engine, &state.weights),
                    weights: state.weights.clone(),
                    request_id: state.request_id,
                    exact: true,
                });
            }
        }
    }
}

fn load_stem(
    client: &Client,
    server_base: &Url,
    source: &NativeSource,
) -> Result<StemSource, String> {
    if !matches!(source.channels, 1 | 2) {
        return Err(format!(
            "Stem '{}' has an unsupported channel count",
            source.key
        ));
    }
    let url = checked_url(server_base, &source.url)?;
    let response = client
        .get(url.clone())
        .send()
        .and_then(reqwest::blocking::Response::error_for_status)
        .map_err(|error| format!("Could not download '{}': {error}", source.key))?;
    let bytes = response
        .bytes()
        .map_err(|error| format!("Could not read '{}': {error}", source.key))?;
    let extension = Path::new(url.path())
        .extension()
        .and_then(|value| value.to_str());
    let [left, right] = stereo_48k(decode(bytes.to_vec(), extension)?)?;
    Ok(StemSource { left, right })
}

fn parse_params(value: Value) -> Result<EngineParams, String> {
    serde_json::from_value(value).map_err(|error| format!("Engine parameters rejected: {error}"))
}

fn output_layout(params: &EngineParams, renderer: NativeRenderer) -> Result<&'static str, String> {
    if renderer == NativeRenderer::Direct && params.output_mode != OutputMode::Native {
        return Ok("stereo");
    }
    let names = params
        .speakers
        .iter()
        .map(|speaker| speaker.name.as_str())
        .collect::<Vec<_>>();
    output_layout_for_names(&names)
}

fn output_layout_for_names(names: &[&str]) -> Result<&'static str, String> {
    match names {
        ["FL", "FR"] => Ok("stereo"),
        ["FL", "FR", "C", "LFE", "SL", "SR"] => Ok("5.1"),
        ["FL", "FR", "C", "LFE", "SL", "SR", "BL", "BR"] => Ok("7.1"),
        ["FL", "FR", "C", "LFE", "SL", "SR", "TFL", "TFR"] => Ok("5.1.2"),
        ["FL", "FR", "C", "LFE", "SL", "SR", "TFL", "TFR", "TBL", "TBR"] => Ok("5.1.4"),
        ["FL", "FR", "C", "LFE", "SL", "SR", "BL", "BR", "TFL", "TFR"] => Ok("7.1.2"),
        ["FL", "FR", "C", "LFE", "BL", "BR", "SL", "SR", "TFL", "TFR", "TBL", "TBR"] => Ok("7.1.4"),
        _ => Err(format!(
            "Native audio output does not support this {}-channel layout",
            names.len()
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn visuals_wait_for_the_latest_presented_audio_frame() {
        let mut frames = VecDeque::from([
            PendingFrame {
                presented_at: 100,
                meters: vec![1.0],
                spectrum: vec![],
            },
            PendingFrame {
                presented_at: 200,
                meters: vec![2.0],
                spectrum: vec![],
            },
            PendingFrame {
                presented_at: 300,
                meters: vec![3.0],
                spectrum: vec![],
            },
        ]);

        let frame = take_presented_frame(&mut frames, 250).unwrap();

        assert_eq!(frame.meters, vec![2.0]);
        assert_eq!(frames.front().unwrap().presented_at, 300);
    }

    #[test]
    fn recognizes_every_supported_layout() {
        for (names, layout) in [
            (&["FL", "FR"][..], "stereo"),
            (&["FL", "FR", "C", "LFE", "SL", "SR"], "5.1"),
            (&["FL", "FR", "C", "LFE", "SL", "SR", "BL", "BR"], "7.1"),
            (&["FL", "FR", "C", "LFE", "SL", "SR", "TFL", "TFR"], "5.1.2"),
            (
                &[
                    "FL", "FR", "C", "LFE", "SL", "SR", "TFL", "TFR", "TBL", "TBR",
                ],
                "5.1.4",
            ),
            (
                &["FL", "FR", "C", "LFE", "SL", "SR", "BL", "BR", "TFL", "TFR"],
                "7.1.2",
            ),
            (
                &[
                    "FL", "FR", "C", "LFE", "BL", "BR", "SL", "SR", "TFL", "TFR", "TBL", "TBR",
                ],
                "7.1.4",
            ),
        ] {
            assert_eq!(output_layout_for_names(names).unwrap(), layout);
        }
    }
}
