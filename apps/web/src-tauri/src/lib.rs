mod assets;
mod audio;
mod decode;
mod preview;

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{mpsc, Mutex};

use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri::ipc::Channel;
use tauri::{Manager, State};

use preview::{Command, NativeAssets, NativeEvent, NativeRenderer, OpenRequest};

struct ActiveSession {
    id: u64,
    commands: mpsc::Sender<Command>,
}

#[derive(Default)]
struct NativeState {
    next_id: AtomicU64,
    active: Mutex<Option<ActiveSession>>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct NativeCapabilities {
    native_dsp: bool,
    apple_spatial: bool,
    max_channels: usize,
    error: Option<String>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct UpdateRequest {
    session_id: u64,
    params: Value,
    assets: NativeAssets,
    renderer: NativeRenderer,
    apple_head_tracking: bool,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct TransportRequest {
    session_id: u64,
    playing: Option<bool>,
    looping: Option<bool>,
    frame: Option<usize>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct SeekRequest {
    session_id: u64,
    frame: usize,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct MeasureRequest {
    session_id: u64,
    weights: Vec<f64>,
    request_id: u64,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct MonitorRequest {
    session_id: u64,
    volume: f32,
    muted: bool,
}

#[tauri::command]
fn native_capabilities() -> NativeCapabilities {
    NativeCapabilities {
        native_dsp: true,
        apple_spatial: true,
        max_channels: audio::max_output_channels().max(2),
        error: None,
    }
}

#[tauri::command]
fn native_preview_open(
    app: tauri::AppHandle,
    state: State<'_, NativeState>,
    request: OpenRequest,
    on_event: Channel<NativeEvent>,
) -> Result<u64, String> {
    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|error| error.to_string())?;
    let id = state.next_id.fetch_add(1, Ordering::Relaxed) + 1;
    let (commands, receiver) = mpsc::channel();
    let old = state
        .active
        .lock()
        .map_err(|_| "Native preview state is unavailable")?
        .replace(ActiveSession { id, commands });
    if let Some(old) = old {
        let _ = old.commands.send(Command::Close);
    }
    std::thread::Builder::new()
        .name(format!("upmixer-preview-{id}"))
        .spawn(move || preview::run(request, resource_dir, receiver, on_event))
        .map_err(|error| format!("Could not start native preview: {error}"))?;
    Ok(id)
}

#[tauri::command]
fn native_preview_update(
    state: State<'_, NativeState>,
    request: UpdateRequest,
) -> Result<(), String> {
    send(
        &state,
        request.session_id,
        Command::Update {
            params: request.params,
            assets: request.assets,
            renderer: request.renderer,
            apple_head_tracking: request.apple_head_tracking,
        },
    )
}

#[tauri::command]
fn native_preview_transport(
    state: State<'_, NativeState>,
    request: TransportRequest,
) -> Result<(), String> {
    send(
        &state,
        request.session_id,
        Command::Transport {
            playing: request.playing,
            looping: request.looping,
            frame: request.frame,
        },
    )
}

#[tauri::command]
fn native_preview_seek(state: State<'_, NativeState>, request: SeekRequest) -> Result<(), String> {
    send(&state, request.session_id, Command::Seek(request.frame))
}

#[tauri::command]
fn native_preview_measure(
    state: State<'_, NativeState>,
    request: MeasureRequest,
) -> Result<(), String> {
    send(
        &state,
        request.session_id,
        Command::Measure {
            weights: request.weights,
            request_id: request.request_id,
        },
    )
}

#[tauri::command]
fn native_preview_monitor(
    state: State<'_, NativeState>,
    request: MonitorRequest,
) -> Result<(), String> {
    send(
        &state,
        request.session_id,
        Command::Monitor {
            volume: request.volume,
            muted: request.muted,
        },
    )
}

#[tauri::command]
fn native_preview_close(state: State<'_, NativeState>, session_id: u64) -> Result<(), String> {
    let mut active = state
        .active
        .lock()
        .map_err(|_| "Native preview state is unavailable")?;
    if active
        .as_ref()
        .is_some_and(|session| session.id == session_id)
    {
        if let Some(session) = active.take() {
            let _ = session.commands.send(Command::Close);
        }
    }
    Ok(())
}

fn send(state: &State<'_, NativeState>, id: u64, command: Command) -> Result<(), String> {
    let active = state
        .active
        .lock()
        .map_err(|_| "Native preview state is unavailable")?;
    let session = active
        .as_ref()
        .filter(|session| session.id == id)
        .ok_or_else(|| "Native preview session is no longer active".to_string())?;
    session
        .commands
        .send(command)
        .map_err(|_| "Native preview stopped".to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(NativeState::default())
        .invoke_handler(tauri::generate_handler![
            native_capabilities,
            native_preview_open,
            native_preview_update,
            native_preview_transport,
            native_preview_seek,
            native_preview_measure,
            native_preview_monitor,
            native_preview_close,
        ])
        .run(tauri::generate_context!())
        .expect("error while running Upmixer");
}
