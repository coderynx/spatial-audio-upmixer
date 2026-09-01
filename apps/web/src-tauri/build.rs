fn main() {
    tauri_build::build();
    #[cfg(target_os = "macos")]
    {
        println!("cargo:rerun-if-changed=native/audio_bridge.m");
        println!("cargo:rerun-if-changed=native/audio_bridge.h");
        cc::Build::new()
            .file("native/audio_bridge.m")
            .flag("-fobjc-arc")
            .flag("-mmacosx-version-min=15.0")
            .compile("upmixer_audio_bridge");
        println!("cargo:rustc-link-lib=framework=AVFAudio");
        println!("cargo:rustc-link-lib=framework=Foundation");
        println!("cargo:rustc-link-lib=framework=PHASE");
    }
}
