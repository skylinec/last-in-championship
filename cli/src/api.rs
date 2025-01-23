use reqwest::{Client, StatusCode, header};
use serde::Deserialize;
use anyhow::{Result, Context};
use tracing::debug;
use chrono::NaiveDate;

use crate::models::*;
use crate::models::AttendanceEntry;

pub struct Api {
    client: Client,
    base_url: String,
    token: String,
}

impl Api {
    pub fn new(base_url: String, token: Option<String>) -> Self {
        Self {
            client: Client::new(),
            base_url,
            token: token.unwrap_or_default()
        }
    }

    pub async fn login(&self, username: &str, password: &str) -> Result<String> {
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

        let data: serde_json::Value = resp.json().await?;
        let token = data["token"].as_str()
            .ok_or_else(|| anyhow::anyhow!("No token in response"))?
            .to_string();

        Ok(token)
    }

    fn auth_headers(&self, token: &str) -> Result<header::HeaderMap> {
        let mut headers = header::HeaderMap::new();
        
        headers.insert(
            header::AUTHORIZATION,
            header::HeaderValue::from_str(&format!("Bearer {}", token))?
        );
        
        headers.insert(
            header::ACCEPT,
            header::HeaderValue::from_static("application/json")
        );
        
        Ok(headers)
    }

    pub async fn log_attendance(&self, entry: AttendanceEntry) -> Result<()> {
        let url = format!("{}/api/log", self.base_url);
        
        let response = self.client
            .post(&url)
            .header("Authorization", format!("Bearer {}", self.token))
            .json(&entry)
            .send()
            .await?;

        if response.status().is_success() {
            Ok(())
        } else {
            let error_text = response.text().await?;
            Err(anyhow::anyhow!("{}", error_text))
        }
    }

    pub async fn get_rankings(&self, token: &str, period: &str, date: Option<String>) -> Result<Vec<Ranking>> {
        let mut url = format!("{}/api/rankings/{}", self.base_url, period);
        if let Some(date) = date {
            url.push_str(&format!("/{}", date));
        }

        debug!("Requesting rankings from: {}", url);
        let response = self.client
            .get(&url)
            .headers(self.auth_headers(token)?)
            .send()
            .await?;

        self.handle_response(response).await
    }

    pub async fn get_streaks(&self, token: &str) -> Result<Vec<Streak>> {
        let url = format!("{}/api/streaks", self.base_url);
        debug!("Requesting streaks from: {}", url);
        let response = self.client
            .get(&url)
            .headers(self.auth_headers(token)?)
            .send()
            .await?;

        self.handle_response(response).await
    }

    pub async fn get_user_stats(&self, token: &str, username: &str) -> Result<StatsResponse> {
        let url = format!("{}/api/users/{}/stats", self.base_url, username);
        debug!("Requesting user stats from: {}", url);
        let response = self.client
            .get(&url)
            .headers(self.auth_headers(token)?)
            .send()
            .await?;

        self.handle_response(response).await
    }

    pub async fn query_data(
        &self,
        token: &str,
        period: &str,
        from: Option<NaiveDate>,
        to: Option<NaiveDate>,
        user: Option<&str>,
        mode: &str,
        status: Option<&str>,
        limit: Option<usize>,
    ) -> Result<Vec<QueryResult>> {
        let mut url = format!("{}/api/query/{}", self.base_url, period);
        
        let mut query_params = Vec::new();
        if let Some(from) = from {
            query_params.push(("from", from.format("%Y-%m-%d").to_string()));
        }
        if let Some(to) = to {
            query_params.push(("to", to.format("%Y-%m-%d").to_string()));
        }
        if let Some(user) = user {
            query_params.push(("user", user.to_string()));
        }
        query_params.push(("mode", mode.to_string()));
        if let Some(status) = status {
            query_params.push(("status", status.to_string()));
        }
        if let Some(limit) = limit {
            query_params.push(("limit", limit.to_string()));
        }
        
        if !query_params.is_empty() {
            url.push('?');
            url.push_str(&query_params.into_iter()
                .map(|(k, v)| format!("{}={}", k, v))
                .collect::<Vec<_>>()
                .join("&"));
        }

        debug!("Querying data from: {}", url);
        let response = self.client
            .get(&url)
            .headers(self.auth_headers(token)?)
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
