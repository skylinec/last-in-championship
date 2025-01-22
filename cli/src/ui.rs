use comfy_table::{Table, ContentArrangement};
use colored::*;
use indicatif::{ProgressBar, ProgressStyle};

pub fn create_table() -> Table {
    let mut table = Table::new();
    table.set_content_arrangement(ContentArrangement::Dynamic)
        .load_preset("||--+-++|    ")
        .apply_modifier(comfy_table::modifiers::UTF8_ROUND_CORNERS);
    table
}

pub fn format_header(text: &str) -> String {
    format!("\n{}\n", text.bold().blue())
}

pub fn create_spinner(msg: &str) -> ProgressBar {
    let pb = ProgressBar::new_spinner();
    pb.set_style(
        ProgressStyle::default_spinner()
            .template("{spinner:.blue} {msg}")
            .unwrap()
    );
    pb.set_message(msg.to_string());
    pb
}

// Add other UI helper functions...
