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
        let mut config = config.clone();
        let mut modified = false;

        if let Some(api_url) = &self.api_url {
            config.api_url = api_url.clone();
            modified = true;
        }

        if let Some(username) = &self.username {
            config.username = username.clone();
            modified = true;
        }

        if !modified {
            // Interactive configuration
            config.api_url = Input::new()
                .with_prompt("API URL")
                .default(config.api_url)
                .interact()?;

            config.username = Input::new()
                .with_prompt("Default username")
                .default(config.username)
                .interact()?;
        }

        config.save()?;
        println!("✅ Configuration saved successfully");
        Ok(())
    }
}
