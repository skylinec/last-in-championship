use clap::Args;
use comfy_table::Cell;
use crate::{api::Api, config::Config, ui, models::StatsResponse};  // Change to use StatsResponse

#[derive(Args)]
pub struct StatsCommand {
    #[clap(short, long)]
    user: Option<String>,
}

impl StatsCommand {
    pub async fn run(&self, config: &Config) -> anyhow::Result<()> {
        let pb = ui::create_spinner("Fetching statistics...");
        let config_api_token = config.api_token.clone();
        
        let api = Api::new(config.api_url.clone(), config_api_token);
        let username = self.user.clone().unwrap_or(config.username.clone());
        
        let token = config.api_token.as_ref()
            .ok_or_else(|| anyhow::anyhow!("Not logged in. Please run `lic login` first."))?;
            
        let stats: StatsResponse = api.get_user_stats(token, &username).await?;  // Change type annotation

        let mut table = ui::create_table();
        table.set_header(vec!["Metric", "Value"]);
        
        table.add_row(vec![
            Cell::new("Total Days"),
            Cell::new(stats.stats.days.to_string())
        ]);
        table.add_row(vec![
            Cell::new("In Office"),
            Cell::new(format!("{} ({}%)", 
                stats.stats.in_office,
                (stats.stats.in_office as f64 / stats.stats.days as f64 * 100.0).round()
            ))
        ]);
        table.add_row(vec![
            Cell::new("Remote"),
            Cell::new(format!("{} ({}%)", 
                stats.stats.remote,
                (stats.stats.remote as f64 / stats.stats.days as f64 * 100.0).round()
            ))
        ]);
        table.add_row(vec![
            Cell::new("Average Arrival"),
            Cell::new(&stats.average_arrival_time)
        ]);
        table.add_row(vec![
            Cell::new("Current Score"),
            Cell::new(format!("{:.2}", stats.score))
        ]);

        pb.finish_and_clear();
        println!("{}", ui::format_header(&format!("Statistics for {}", username)));
        println!("{}", table);

        Ok(())
    }
}
