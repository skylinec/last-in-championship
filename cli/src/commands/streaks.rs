use clap::Args;
use comfy_table::Cell;
use crate::{api::Api, config::Config, ui};

#[derive(Args)]
pub struct StreaksCommand {}

impl StreaksCommand {
    pub async fn run(&self, config: &Config) -> anyhow::Result<()> {
        let pb = ui::create_spinner("Fetching streaks...");
        
        let api = Api::new(config.api_url.clone());
        
        // Get token from config, return error if not found
        let token = config.api_token.as_ref()
            .ok_or_else(|| anyhow::anyhow!("Not logged in. Please run `lic login` first."))?;
        
        let streaks = api.get_streaks(token).await?;

        let mut table = ui::create_table();
        table.set_header(vec![
            "Name",
            "Current Streak",
            "Best Streak",
            "Since"
        ]);

        for streak in streaks {
            let streak_indicator = if streak.current_streak > 0 {
                format!("🔥 {}", streak.current_streak)
            } else {
                "—".to_string()
            };

            table.add_row(vec![
                Cell::new(&streak.username),
                Cell::new(streak_indicator),
                Cell::new(streak.max_streak.to_string()),
                Cell::new(streak.streak_start.map_or("—".to_string(), |d| d.format("%Y-%m-%d").to_string()))
            ]);
        }

        pb.finish_and_clear();
        println!("{}", ui::format_header("Attendance Streaks"));
        println!("{}", table);

        Ok(())
    }
}
