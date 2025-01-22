use serde::{Deserialize, Serialize};
use chrono::NaiveDate;

#[derive(Debug, Serialize, Deserialize)]
pub struct AttendanceEntry {
    pub date: String,
    pub time: String,
    pub name: String,
    pub status: String,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct Ranking {
    pub name: String,
    pub score: f64,
    pub streak: Option<i32>,
    pub average_arrival_time: String,
    pub stats: RankingStats,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct RankingStats {
    pub in_office: i32,
    pub remote: i32,
    pub sick: i32,
    pub leave: i32,
    pub days: i32,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct Streak {
    pub username: String,
    pub current_streak: i32,
    pub max_streak: i32,
    pub streak_start: Option<NaiveDate>,
}

#[derive(Debug, Deserialize)]
pub struct QueryResult {
    pub date: String,
    pub name: String,
    pub status: String,
    pub time: String,
    pub score: f64,
    pub streak: Option<i32>,
}

// Add other models...
