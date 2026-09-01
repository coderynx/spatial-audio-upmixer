use std::ffi::{c_char, c_void, CStr, CString};
use std::ptr;

#[link(name = "upmixer_phase_bridge", kind = "static")]
extern "C" {
    #[cfg(test)]
    fn upmixer_phase_uses_media_pipeline(layout: *const c_char, spatial: bool) -> bool;
    fn upmixer_phase_create(
        layout: *const c_char,
        spatial: bool,
        error: *mut *mut c_char,
    ) -> *mut c_void;
    fn upmixer_phase_start(host: *mut c_void, error: *mut *mut c_char) -> bool;
    fn upmixer_phase_pause(host: *mut c_void);
    fn upmixer_phase_resume(host: *mut c_void);
    fn upmixer_phase_schedule(
        host: *mut c_void,
        channels: *const *const f32,
        channel_count: u32,
        frames: u32,
        error: *mut *mut c_char,
    ) -> bool;
    fn upmixer_phase_destroy(host: *mut c_void);
    fn upmixer_phase_free_error(error: *mut c_char);
    fn upmixer_phase_max_output_channels() -> u32;
}

pub fn max_output_channels() -> usize {
    unsafe { upmixer_phase_max_output_channels() as usize }
}

pub struct PhaseHost(*mut c_void);

impl PhaseHost {
    pub fn new(layout: &str, spatial: bool) -> Result<Self, String> {
        let layout = CString::new(layout).map_err(|_| "Invalid channel layout".to_string())?;
        let mut error = ptr::null_mut();
        let host = unsafe { upmixer_phase_create(layout.as_ptr(), spatial, &mut error) };
        if host.is_null() {
            return Err(take_error(error, "Could not create PHASE"));
        }
        let mut error = ptr::null_mut();
        if !unsafe { upmixer_phase_start(host, &mut error) } {
            unsafe { upmixer_phase_destroy(host) };
            return Err(take_error(error, "Could not start PHASE"));
        }
        Ok(Self(host))
    }

    pub fn pause(&self) {
        unsafe { upmixer_phase_pause(self.0) };
    }

    pub fn resume(&self) {
        unsafe { upmixer_phase_resume(self.0) };
    }

    pub fn schedule(&self, channels: &[Vec<f32>], frames: usize) -> Result<(), String> {
        let pointers = channels
            .iter()
            .map(|channel| channel.as_ptr())
            .collect::<Vec<_>>();
        let mut error = ptr::null_mut();
        if unsafe {
            upmixer_phase_schedule(
                self.0,
                pointers.as_ptr(),
                pointers.len() as u32,
                frames as u32,
                &mut error,
            )
        } {
            Ok(())
        } else {
            Err(take_error(error, "Could not schedule PHASE audio"))
        }
    }
}

impl Drop for PhaseHost {
    fn drop(&mut self) {
        unsafe { upmixer_phase_destroy(self.0) };
    }
}

fn take_error(error: *mut c_char, fallback: &str) -> String {
    if error.is_null() {
        return fallback.to_string();
    }
    let message = unsafe { CStr::from_ptr(error) }
        .to_string_lossy()
        .into_owned();
    unsafe { upmixer_phase_free_error(error) };
    message
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn apple_media_pipeline_handles_every_layout_except_stereo() {
        for layout in ["stereo", "5.1", "7.1", "5.1.2", "5.1.4", "7.1.2", "7.1.4"] {
            let name = CString::new(layout).unwrap();
            let media = unsafe { upmixer_phase_uses_media_pipeline(name.as_ptr(), true) };
            assert_eq!(media, layout != "stereo");
            assert!(!unsafe { upmixer_phase_uses_media_pipeline(name.as_ptr(), false) });
        }
    }
}
