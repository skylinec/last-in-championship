use clap::Args;
use dialoguer::Input;
use crate::config::Config;

#[derive(Args)]
pub struct ConfigCommand {
    #[clap(short, long)]
    api_url: Option<String>,
    
    #[clap(short, long)]
    username: Option<String>,
}

impl ConfigCommand {
    pub async fn run(&self, config: &Config) -> anyhow::Result<()> {
        let new_config = if self.api_url.is_some() || self.username.is_some() {
            config.clone().with_updates(self.api_url.clone(), self.username.clone())
        } else {
            let api_url = Input::<String>::new()
                .with_prompt("API URL")
                .default(config.api_url.clone())
                .interact()?;

            let username = Input::<String>::new()
                .with_prompt("Default username")
                .default(config.username.clone())
                .interact()?;

            config.clone().with_updates(Some(api_url), Some(username))
        };

        new_config.save()?;
        println!("✅ Configuration saved successfully");
        Ok(())
    }
}
