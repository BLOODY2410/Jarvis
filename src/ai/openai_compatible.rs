use std::time::Duration;

use async_trait::async_trait;
use reqwest::{Client, header::RETRY_AFTER};
use serde::Deserialize;
use serde_json::{Map, Value, json};

use crate::{
    ai::provider::{
        AiError, AiErrorKind, AiProvider, ProviderKind, ProviderRequest, ProviderResponse,
        classify_http_error,
    },
    core::messages::Message,
};

#[derive(Clone)]
pub struct OpenAiCompatibleClient {
    client: Client,
    kind: ProviderKind,
    api_key: String,
    model: String,
    endpoint: String,
}

#[derive(Deserialize)]
struct ChatResponse {
    choices: Vec<Choice>,
    usage: Option<Usage>,
}
#[derive(Deserialize)]
struct Choice {
    message: Message,
}
#[derive(Deserialize)]
struct Usage {
    prompt_tokens: Option<u64>,
    completion_tokens: Option<u64>,
}

impl OpenAiCompatibleClient {
    pub fn new(
        kind: ProviderKind,
        api_key: String,
        model: String,
        endpoint: String,
        connect_timeout: Duration,
        read_timeout: Duration,
    ) -> Self {
        Self {
            client: Client::builder()
                .connect_timeout(connect_timeout)
                .timeout(read_timeout)
                .build()
                .expect("valid AI HTTP client"),
            kind,
            api_key,
            model,
            endpoint,
        }
    }
}

#[async_trait]
impl AiProvider for OpenAiCompatibleClient {
    fn kind(&self) -> ProviderKind {
        self.kind
    }
    fn model(&self) -> &str {
        &self.model
    }

    async fn complete(&self, request: ProviderRequest<'_>) -> Result<ProviderResponse, AiError> {
        let mut body = Map::new();
        body.insert("model".into(), json!(self.model));
        body.insert("messages".into(), json!(request.messages));
        body.insert("temperature".into(), json!(request.temperature));
        let token_field = if matches!(self.kind, ProviderKind::OpenRouter | ProviderKind::Mistral) {
            "max_tokens"
        } else {
            "max_completion_tokens"
        };
        body.insert(token_field.into(), json!(request.max_output_tokens));
        if request.reasoning != crate::ai::provider::ReasoningEffort::None {
            body.insert("reasoning_effort".into(), json!(request.reasoning.as_str()));
            body.insert("reasoning_format".into(), json!("hidden"));
        } else if self.kind == ProviderKind::Groq && self.model.starts_with("qwen/") {
            body.insert("reasoning_effort".into(), json!("none"));
        }
        if let Some(tools) = request.tools.filter(|tools| !tools.is_empty()) {
            body.insert("tools".into(), json!(tools));
            body.insert("tool_choice".into(), json!("auto"));
        }

        let response = self
            .client
            .post(&self.endpoint)
            .bearer_auth(&self.api_key)
            .json(&Value::Object(body))
            .send()
            .await
            .map_err(|error| {
                if error.is_timeout() {
                    AiError::new(AiErrorKind::Timeout, "Провайдер AI не відповів вчасно.")
                } else {
                    AiError::new(AiErrorKind::Unavailable, "Немає зв'язку з провайдером AI.")
                }
            })?;
        let status = response.status();
        let retry_after = response
            .headers()
            .get(RETRY_AFTER)
            .and_then(|value| value.to_str().ok())
            .and_then(|value| value.parse::<f64>().ok())
            .map(Duration::from_secs_f64);
        if !status.is_success() {
            return Err(classify_http_error(status, retry_after));
        }
        let data: ChatResponse = response.json().await.map_err(|_| {
            AiError::new(
                AiErrorKind::ProviderError,
                "Провайдер AI повернув некоректну відповідь.",
            )
        })?;
        let message = data
            .choices
            .into_iter()
            .next()
            .map(|choice| choice.message)
            .ok_or_else(|| {
                AiError::new(
                    AiErrorKind::ProviderError,
                    "Провайдер AI повернув порожню відповідь.",
                )
            })?;
        Ok(ProviderResponse {
            message,
            input_tokens: data.usage.as_ref().and_then(|usage| usage.prompt_tokens),
            output_tokens: data.usage.and_then(|usage| usage.completion_tokens),
            sources: vec![],
            provider: None,
            model: None,
            fallback_count: 0,
        })
    }
}
