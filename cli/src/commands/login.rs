use clap::Args;
use dialoguer::{Input, Password};
use crate::{api::Api, config::Config, ui};

#[derive(Args)]
pub struct LoginCommand {
    #[clap(short, long)]
    username: Option<String>,
    
    #[clap(short, long)]
    password: Option<String>,
}

impl LoginCommand {
    pub async fn run(&self, config: &Config) -> anyhow::Result<()> {
        let pb = ui::create_spinner("Logging in...");
        
        let username = match &self.username {
            Some(u) => u.clone(),
            None => Input::<String>::new()
                .with_prompt("Username")
                .interact()?
        };

        let password = match &self.password {
            Some(p) => p.clone(),
            None => Password::new()
                .with_prompt("Password")
                .interact()?
        };

        let api = Api::new(config.api_url.clone());
        match api.login(&username, &password).await {
            Ok(token) => {
                let mut new_config = config.clone();
                new_config.username = username;
                new_config.api_token = Some(token.clone());  // Clone the token
                new_config.save()?;
                
                pb.finish_with_message("✅ Login successful");
                Ok(())
            },
            Err(e) => {
                pb.finish_with_message("❌ Login failed");
                Err(e)
            }
        }
    }
}
