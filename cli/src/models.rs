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

#[derive(Debug, Deserialize)]
pub struct StatsResponse {
    pub average_arrival_time: String,
    pub score: f64,
    pub stats: StatsDetail,
}

#[derive(Debug, Deserialize)]
pub struct StatsDetail {
    pub days: u32,
    pub in_office: u32,
    pub remote: u32,
    pub sick: u32,
    pub leave: u32,
}

// Add other models...
#[derive(Debug, Clone, Deserialize)]  // Added Clone
pub enum Period {
    Day,
    Week,
    Month,
}

impl std::fmt::Display for Period {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Period::Day => write!(f, "day"),
            Period::Week => write!(f, "week"),
            Period::Month => write!(f, "month"),
        }
    }
}

// Add ValueEnum implementation for clap
impl clap::ValueEnum for Period {
    fn value_variants<'a>() -> &'a [Self] {
        &[Self::Day, Self::Week, Self::Month]
    }

    fn to_possible_value(&self) -> Option<clap::builder::PossibleValue> {
        Some(match self {
            Self::Day => clap::builder::PossibleValue::new("day"),
            Self::Week => clap::builder::PossibleValue::new("week"),
            Self::Month => clap::builder::PossibleValue::new("month"),
        })
    }
}

// Remove FromStr implementation since we're using ValueEnum
