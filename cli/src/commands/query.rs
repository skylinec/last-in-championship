use clap::Args;
use comfy_table::Cell;
use chrono::{NaiveDate, Local};
use crate::{api::Api, config::Config, ui};

#[derive(Args)]
pub struct QueryCommand {
    #[clap(short, long, default_value = "day")]
    period: String,

    #[clap(short, long)]
    from: Option<String>,

    #[clap(short, long)]
    to: Option<String>,

    #[clap(short, long)]
    user: Option<String>,

    #[clap(short = 'm', long, default_value = "last-in")]
    mode: String,

    #[clap(short, long)]
    status: Option<String>,

    #[clap(long)]
    limit: Option<usize>,
}

impl QueryCommand {
    pub async fn run(&self, config: &Config) -> anyhow::Result<()> {
        let pb = ui::create_spinner("Querying data...");
        
        let api = Api::new(config.api_url.clone());
        let token = config.api_token.as_ref()
            .ok_or_else(|| anyhow::anyhow!("Not logged in. Please run `lic login` first."))?;

        // Parse dates if provided
        let from_date = self.from.as_ref()
            .map(|d| NaiveDate::parse_from_str(d, "%Y-%m-%d"))
            .transpose()?;
        
        let to_date = self.to.as_ref()
            .map(|d| NaiveDate::parse_from_str(d, "%Y-%m-%d"))
            .transpose()?;

        let results = api.query_data(
            token,
            &self.period,
            from_date,
            to_date,
            self.user.as_deref(),
            &self.mode,
            self.status.as_deref(),
            self.limit,
        ).await?;

        let mut table = ui::create_table();
        table.set_header(vec![
            "Date",
            "Name",
            "Status",
            "Time",
            "Score",
            "Streak",
        ]);

        for result in results {
            table.add_row(vec![
                Cell::new(result.date),
                Cell::new(&result.name),
                Cell::new(&result.status),
                Cell::new(&result.time),
                Cell::new(format!("{:.2}", result.score)),
                Cell::new(result.streak.map_or("—".to_string(), |s| format!("🔥 {}", s))),
            ]);
        }

        pb.finish_and_clear();
        println!("{}", ui::format_header("Query Results"));
        println!("{}", table);

        Ok(())
    }
}
