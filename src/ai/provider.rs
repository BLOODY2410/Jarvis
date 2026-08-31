use std::{fmt, time::Duration};

use async_trait::async_trait;
use serde_json::Value;

use crate::core::messages::Message;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ProviderKind {
    Cerebras,
    Gemini,
    Groq,
    OpenRouter,
    Mistral,
}

impl fmt::Display for ProviderKind {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            formatter,
            "{}",
            match self {
                Self::Cerebras => "cerebras",
                Self::Gemini => "gemini",
                Self::Groq => "groq",
                Self::OpenRouter => "openrouter",
                Self::Mistral => "mistral",
            }
        )
    }
}

impl ProviderKind {
    pub fn parse(value: &str) -> Option<Self> {
        match value.trim().to_ascii_lowercase().as_str() {
            "cerebras" => Some(Self::Cerebras),
            "gemini" => Some(Self::Gemini),
            "groq" => Some(Self::Groq),
            "openrouter" => Some(Self::OpenRouter),
            "mistral" => Some(Self::Mistral),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AiErrorKind {
    RateLimit,
    Timeout,
    Auth,
    Unavailable,
    InvalidRequest,
    ProviderError,
}

#[derive(Debug, Clone)]
pub struct AiError {
    pub kind: AiErrorKind,
    pub safe_message: String,
    pub retry_after: Option<Duration>,
}

impl AiError {
    pub fn new(kind: AiErrorKind, message: impl Into<String>) -> Self {
        Self {
            kind,
            safe_message: message.into(),
            retry_after: None,
        }
    }

    pub fn retry_after(mut self, duration: Duration) -> Self {
        self.retry_after = Some(duration);
        self
    }
}

impl fmt::Display for AiError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.safe_message)
    }
}

impl std::error::Error for AiError {}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ReasoningEffort {
    None,
    Low,
    Medium,
}

impl ReasoningEffort {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::None => "none",
            Self::Low => "low",
            Self::Medium => "medium",
        }
    }
}

#[derive(Clone)]
pub struct ProviderRequest<'a> {
    pub messages: &'a [Message],
    pub tools: Option<&'a [Value]>,
    pub reasoning: ReasoningEffort,
    pub temperature: f32,
    pub max_output_tokens: u32,
    pub live_search: bool,
}

#[derive(Debug, Clone)]
pub struct ProviderResponse {
    pub message: Message,
    pub input_tokens: Option<u64>,
    pub output_tokens: Option<u64>,
    pub sources: Vec<GroundingSource>,
    pub provider: Option<ProviderKind>,
    pub model: Option<String>,
    pub fallback_count: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GroundingSource {
    pub title: String,
    pub url: String,
}

#[async_trait]
pub trait AiProvider: Send + Sync {
    fn kind(&self) -> ProviderKind;
    fn model(&self) -> &str;
    async fn complete(&self, request: ProviderRequest<'_>) -> Result<ProviderResponse, AiError>;
}

pub(crate) fn classify_http_error(
    status: reqwest::StatusCode,
    retry_after: Option<Duration>,
) -> AiError {
    let kind = match status.as_u16() {
        401 | 403 => AiErrorKind::Auth,
        429 => AiErrorKind::RateLimit,
        400 | 404 | 422 => AiErrorKind::InvalidRequest,
        500..=599 => AiErrorKind::Unavailable,
        _ => AiErrorKind::ProviderError,
    };
    let message = match kind {
        AiErrorKind::Auth => "Провайдер AI відхилив автентифікацію.",
        AiErrorKind::RateLimit => "Провайдер AI тимчасово вичерпав квоту.",
        AiErrorKind::InvalidRequest => "Провайдер AI відхилив запит.",
        AiErrorKind::Unavailable => "Провайдер AI тимчасово недоступний.",
        _ => "Помилка провайдера AI.",
    };
    let error = AiError::new(kind, message);
    retry_after.map_or(error.clone(), |duration| error.retry_after(duration))
}
