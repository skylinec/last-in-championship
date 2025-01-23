use clap::Args;
use chrono::{Local, NaiveTime};
use dialoguer::{Select, theme::ColorfulTheme, Input};
use crate::{api::Api, config::Config, models::AttendanceEntry, ui};

#[derive(Args)]
pub struct LogCommand {
    #[clap(short, long)]
    status: Option<String>,
    
    #[clap(short, long)]
    time: Option<String>,
}

impl LogCommand {
    pub async fn run(&self, config: &Config) -> anyhow::Result<()> {
        let pb = ui::create_spinner("Logging attendance...");

        // Get API token from config
        let api = Api::new(config.api_url.clone(), config.api_token.clone());

        let status = match &self.status {
            Some(s) => s.clone(),
            None => {
                let options = vec!["in-office", "remote", "sick", "leave"];
                let theme = ColorfulTheme::default();
                let selection = Select::with_theme(&theme)  // Add theme for better terminal compatibility
                    .with_prompt("Select status")
                    .items(&options)
                    .default(0)
                    .interact()?;
                options[selection].to_string()
            }
        };

        let time = match &self.time {
            Some(t) => t.clone(),
            None => {
                if status == "sick" || status == "leave" {
                    "00:00".to_string()
                } else {
                    let now = Local::now().time();
                    let theme = ColorfulTheme::default();
                    Input::<String>::with_theme(&theme)  // Add theme for better terminal compatibility
                        .with_prompt("Time (HH:MM)")
                        .default(now.format("%H:%M").to_string())
                        .validate_with(|input: &String| -> Result<(), &str> {
                            NaiveTime::parse_from_str(input, "%H:%M")
                                .map(|_| ())
                                .map_err(|_| "Invalid time format")
                        })
                        .interact()?
                }
            }
        };

        let entry = AttendanceEntry {
            date: Local::now().date_naive().format("%Y-%m-%d").to_string(),
            time,
            name: config.username.clone(),
            status,
        };

        match api.log_attendance(entry).await {
            Ok(_) => {
                pb.finish_with_message("✅ Attendance logged successfully");
                Ok(())
            },
            Err(e) => {
                pb.finish_with_message(format!("❌ Failed to log attendance: {}", e));
                Err(e)
            }
        }
    }
}
