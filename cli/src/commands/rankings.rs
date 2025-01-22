use clap::{Args, ValueEnum};  // Add ValueEnum
use comfy_table::Cell;
use chrono::Local;
use crate::{api::Api, config::Config, ui, models::Period};

#[derive(Args)]
pub struct RankingsCommand {
    #[clap(short, long, default_value = "day", value_enum, help = "Period to show rankings for (day, week, month)")]
    period: Period,
    
    #[clap(short, long)]
    date: Option<String>,
}

impl RankingsCommand {
    pub async fn run(&self, config: &Config) -> anyhow::Result<()> {
        let pb = ui::create_spinner("Fetching rankings...");

        let api = Api::new(config.api_url.clone());
        
        // Get token from config, return error if not found
        let token = config.api_token.as_ref()
            .ok_or_else(|| anyhow::anyhow!("Not logged in. Please run `lic login` first."))?;

        let rankings = api.get_rankings(token, &self.period.to_string(), self.date.clone()).await?;

        let mut table = ui::create_table();
        table.set_header(vec![
            "Rank",
            "Name",
            "Score",
            "Streak",
            "Avg. Time",
            "Stats"
        ]);

        for (i, rank) in rankings.iter().enumerate() {
            let streak_display = match rank.streak {
                Some(s) if s > 0 => format!("🔥 {}", s),
                _ => String::new()
            };

            table.add_row(vec![
                Cell::new((i + 1).to_string()),
                Cell::new(&rank.name),
                Cell::new(format!("{:.2}", rank.score)),
                Cell::new(streak_display),
                Cell::new(&rank.average_arrival_time),
                Cell::new(format!(
                    "🏢 {} | 🏠 {} | 🤒 {} | ✈️ {}",
                    rank.stats.in_office,
                    rank.stats.remote,
                    rank.stats.sick,
                    rank.stats.leave
                ))
            ]);
        }

        pb.finish_and_clear();
        println!("{}", ui::format_header(&format!(
            "{} Rankings ({})",
            self.period.to_string().to_ascii_uppercase(),
            self.date.clone().unwrap_or_else(|| Local::now().format("%Y-%m-%d").to_string())
        )));
        println!("{}", table);

        Ok(())
    }
}
