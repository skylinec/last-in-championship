use clap::{Parser, Subcommand};
use std::path::PathBuf;
use tracing::debug;

mod api;
mod commands;
mod config;
mod models;
mod ui;

use crate::commands::*;
use crate::config::Config;

#[derive(Parser)]
#[clap(
    name = "lic",
    about = "Last-In Championship CLI",
    version = env!("CARGO_PKG_VERSION")
)]
struct Cli {
    #[clap(subcommand)]
    command: Commands,

    #[clap(global = true, short = 'c', long = "config")]
    config_path: Option<PathBuf>,
}

#[derive(Subcommand)]
enum Commands {
    /// Login to the system
    Login(LoginCommand),
    
    /// Log attendance
    Log(LogCommand),
    
    /// View rankings
    Rankings(RankingsCommand),
    
    /// View streaks
    Streaks(StreaksCommand),
    
    /// View statistics and visualizations
    Stats(StatsCommand),
    
    /// Configure the CLI
    Config(ConfigCommand),
    
    /// Query attendance data with filters
    Query(QueryCommand),
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Initialize logging
    tracing_subscriber::fmt::init();

    let cli = Cli::parse();
    let config = Config::load()?;
    
    debug!("Using API URL: {}", config.api_url);

    match cli.command {
        Commands::Login(cmd) => cmd.run(&config).await?,
        Commands::Log(cmd) => cmd.run(&config).await?,
        Commands::Rankings(cmd) => cmd.run(&config).await?,
        Commands::Streaks(cmd) => cmd.run(&config).await?,
        Commands::Stats(cmd) => cmd.run(&config).await?,
        Commands::Config(cmd) => cmd.run(&config).await?,
        Commands::Query(cmd) => cmd.run(&config).await?,
    }

    Ok(())
}
