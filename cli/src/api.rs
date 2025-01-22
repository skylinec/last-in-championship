use reqwest::{Client, StatusCode, header};
use serde::{Deserialize, Serialize};
use anyhow::{Result, Context};
use tracing::debug;

use crate::models::*;

pub struct Api {
    client: Client,
    base_url: String,
}

impl Api {
    pub fn new(base_url: String) -> Self {
        let client = Client::builder()
            .cookie_store(true)
            .build()
            .expect("Failed to create HTTP client");
        Self { client, base_url }
    }

    pub async fn login(&self, username: &str, password: &str) -> Result<()> {
        let resp = self.client
            .post(&format!("{}/api/login", self.base_url))
            .header(header::CONTENT_TYPE, "application/json")
            .json(&serde_json::json!({
                "username": username,
                "password": password
            }))
            .send()
            .await?;

        if resp.status() != StatusCode::OK {
            let error = resp.text().await?;
            anyhow::bail!("Login failed: {}", error);
        }

        Ok(())
    }

    pub async fn log_attendance(&self, entry: AttendanceEntry) -> Result<()> {
        let resp = self.client
            .post(&format!("{}/api/log", self.base_url))
            .header(header::CONTENT_TYPE, "application/json")
            .json(&entry)
            .send()
            .await?;

        if !resp.status().is_success() {
            anyhow::bail!("Failed to log attendance: {}", resp.text().await?);
        }

        Ok(())
    }

    pub async fn get_rankings(&self, period: &str, date: Option<String>) -> Result<Vec<Ranking>> {
        let mut url = format!("{}/api/rankings/{}", self.base_url, period);
        if let Some(date) = date {
            url.push_str(&format!("/{}", date));
        }

        debug!("Requesting rankings from: {}", url);
        let response = self.client
            .get(&url)
            .header(header::ACCEPT, "application/json")
            .send()
            .await?;

        self.handle_response(response).await
    }

    pub async fn get_streaks(&self) -> Result<Vec<Streak>> {
        let url = format!("{}/api/streaks", self.base_url);
        debug!("Requesting streaks from: {}", url);
        let response = self.client
            .get(&url)
            .header(header::ACCEPT, "application/json")
            .send()
            .await?;

        self.handle_response(response).await
    }

    pub async fn get_user_stats(&self, username: &str) -> Result<Ranking> {
        let url = format!("{}/api/users/{}/stats", self.base_url, username);
        debug!("Requesting user stats from: {}", url);
        let response = self.client
            .get(&url)
            .header(header::ACCEPT, "application/json")
            .send()
            .await?;

        self.handle_response(response).await
    }

    async fn handle_response<T: for<'de> Deserialize<'de>>(&self, response: reqwest::Response) -> Result<T> {
        let status = response.status();
        let text = response.text().await?;
        
        debug!("Response status: {}", status);
        debug!("Response body: {}", text);

        if !status.is_success() {
            anyhow::bail!("API request failed: {} - {}", status, text);
        }

        serde_json::from_str(&text)
            .with_context(|| format!("Failed to parse response: {}", text))
    }
}
