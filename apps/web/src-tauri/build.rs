fn main() {
    tauri_build::build();
    #[cfg(target_os = "macos")]
    {
        println!("cargo:rerun-if-changed=native/phase_bridge.m");
        println!("cargo:rerun-if-changed=native/phase_bridge.h");
        cc::Build::new()
            .file("native/phase_bridge.m")
            .flag("-fobjc-arc")
            .flag("-mmacosx-version-min=15.0")
            .compile("upmixer_phase_bridge");
        println!("cargo:rustc-link-lib=framework=AVFAudio");
        println!("cargo:rustc-link-lib=framework=AVFoundation");
        println!("cargo:rustc-link-lib=framework=CoreMedia");
        println!("cargo:rustc-link-lib=framework=Foundation");
        println!("cargo:rustc-link-lib=framework=PHASE");
    }
}
