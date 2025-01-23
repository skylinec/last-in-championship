use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use std::{fs, io};
use directories::ProjectDirs;
use anyhow::Result;

#[derive(Debug, Serialize, Deserialize)]
pub struct Config {
    pub api_url: String,
    pub username: String,
    pub api_token: String,  // Add this field
}

impl Config {
    pub fn load() -> Result<Self> {
        let config_path = get_config_path()?;
        
        if !config_path.exists() {
            return Err(anyhow::anyhow!("Config file not found. Please run 'lic-cli login' first."));
        }

        let config_str = fs::read_to_string(config_path)?;
        let config = serde_json::from_str(&config_str)?;
        Ok(config)
    }

    pub fn save(&self) -> Result<()> {
        let config_path = get_config_path()?;
        
        // Ensure directory exists
        if let Some(parent) = config_path.parent() {
            fs::create_dir_all(parent)?;
        }

        let config_str = serde_json::to_string_pretty(self)?;
        fs::write(config_path, config_str)?;
        Ok(())
    }
}

fn get_config_path() -> io::Result<PathBuf> {
    let proj_dirs = ProjectDirs::from("com", "mattdh", "lic-cli")
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "Could not determine config directory"))?;
    
    Ok(proj_dirs.config_dir().join("config.json"))
}
