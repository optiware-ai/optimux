// The client's version is Optimux's version: read from the repository's VERSION file,
// so a release changes one file only. Built outside the repository, it is "dev".
fn main() {
    println!("cargo:rerun-if-changed=../VERSION");
    let version = std::fs::read_to_string("../VERSION")
        .map(|s| s.trim().to_string())
        .unwrap_or_else(|_| "dev".to_string());
    println!("cargo:rustc-env=OPTIMUX_VERSION={version}");
}
