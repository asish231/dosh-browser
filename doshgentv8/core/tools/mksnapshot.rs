//! Standalone snapshot builder tool for doshgentv8.
//! Bakes EXTENSIONS_SCRIPT (and optional minidom.js) into a standalone binary snapshot blob.

use std::fs;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = std::env::args().collect();
    let out_path = args.get(1).map(|s| s.as_str()).unwrap_or("dosh_snapshot.bin");
    let script_path = args.get(2).map(|s| s.as_str());

    println!("Building dosh snapshot to {}...", out_path);
    let custom_script = if let Some(p) = script_path {
        println!("Reading extra script from {}...", p);
        Some(fs::read_to_string(p)?)
    } else {
        None
    };

    let blob = doshgentv8_core::create_dosh_snapshot(custom_script.as_deref())?;
    fs::write(out_path, &blob)?;
    println!("Successfully wrote {} bytes snapshot blob to {}", blob.len(), out_path);
    Ok(())
}
