use clap::Args;
use chrono::{Local, NaiveTime};
use dialoguer::{Select, Input};
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

        let status = match &self.status {
            Some(s) => s.clone(),
            None => {
                let options = vec!["in-office", "remote", "sick", "leave"];
                let selection = Select::new()
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
                    Input::<String>::new()
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

        let api = Api::new(config.api_url.clone());
        match api.log_attendance(entry).await {
            Ok(_) => {
                pb.finish_with_message("✅ Attendance logged successfully");
                Ok(())
            },
            Err(e) => {
                pb.finish_with_message("❌ Failed to log attendance");
                Err(e)
            }
        }
    }
}
