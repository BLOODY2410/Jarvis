use std::time::Duration;

use async_trait::async_trait;
use reqwest::Client;
use serde::Deserialize;
use serde_json::{Value, json};

use crate::{
    ai::provider::{
        AiError, AiErrorKind, AiProvider, GroundingSource, ProviderKind, ProviderRequest,
        ProviderResponse, classify_http_error,
    },
    core::messages::Message,
};

#[derive(Clone)]
pub struct GeminiClient {
    client: Client,
    api_key: String,
    model: String,
    endpoint_base: String,
}

#[derive(Deserialize)]
struct GeminiResponse {
    candidates: Option<Vec<Candidate>>,
    #[serde(rename = "usageMetadata")]
    usage: Option<Usage>,
}
#[derive(Deserialize)]
struct Candidate {
    content: Option<Content>,
    #[serde(rename = "groundingMetadata")]
    grounding: Option<GroundingMetadata>,
}
#[derive(Deserialize)]
struct Content {
    parts: Vec<Part>,
}
#[derive(Deserialize)]
struct Part {
    text: Option<String>,
}
#[derive(Deserialize)]
struct Usage {
    #[serde(rename = "promptTokenCount")]
    prompt: Option<u64>,
    #[serde(rename = "candidatesTokenCount")]
    candidates: Option<u64>,
}
#[derive(Deserialize)]
struct GroundingMetadata {
    #[serde(rename = "groundingChunks", default)]
    chunks: Vec<GroundingChunk>,
}
#[derive(Deserialize)]
struct GroundingChunk {
    web: Option<WebSource>,
}
#[derive(Deserialize)]
struct WebSource {
    uri: Option<String>,
    title: Option<String>,
}

impl GeminiClient {
    pub fn new(api_key: String, model: String, connect: Duration, read: Duration) -> Self {
        Self {
            client: Client::builder()
                .connect_timeout(connect)
                .timeout(read)
                .build()
                .expect("valid Gemini HTTP client"),
            api_key,
            model,
            endpoint_base: "https://generativelanguage.googleapis.com/v1beta/models".to_owned(),
        }
    }
}

#[async_trait]
impl AiProvider for GeminiClient {
    fn kind(&self) -> ProviderKind {
        ProviderKind::Gemini
    }
    fn model(&self) -> &str {
        &self.model
    }

    async fn complete(&self, request: ProviderRequest<'_>) -> Result<ProviderResponse, AiError> {
        if request.tools.is_some_and(|tools| !tools.is_empty()) {
            return Err(AiError::new(
                AiErrorKind::InvalidRequest,
                "Локальні інструменти для Gemini-route вимкнені.",
            ));
        }
        let system = request
            .messages
            .iter()
            .find(|message| message.role == "system")
            .and_then(|message| message.content.as_deref())
            .unwrap_or_default();
        let contents: Vec<Value> = request.messages.iter().filter(|message| message.role == "user" || message.role == "assistant").filter_map(|message| {
            let text = message.content.as_deref()?;
            Some(json!({"role": if message.role == "assistant" { "model" } else { "user" }, "parts": [{"text": text}]}))
        }).collect();
        let mut body = json!({
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": {"temperature": request.temperature, "maxOutputTokens": request.max_output_tokens}
        });
        if request.live_search {
            body["tools"] = json!([{"google_search": {}}]);
        }
        let url = format!("{}/{}:generateContent", self.endpoint_base, self.model);
        let response = self
            .client
            .post(url)
            .header("x-goog-api-key", &self.api_key)
            .json(&body)
            .send()
            .await
            .map_err(|error| {
                if error.is_timeout() {
                    AiError::new(AiErrorKind::Timeout, "Gemini не відповів вчасно.")
                } else {
                    AiError::new(AiErrorKind::Unavailable, "Немає зв'язку з Gemini.")
                }
            })?;
        let status = response.status();
        if !status.is_success() {
            return Err(classify_http_error(status, None));
        }
        let data: GeminiResponse = response.json().await.map_err(|_| {
            AiError::new(
                AiErrorKind::ProviderError,
                "Gemini повернув некоректну відповідь.",
            )
        })?;
        let candidate = data
            .candidates
            .and_then(|mut values| (!values.is_empty()).then(|| values.remove(0)))
            .ok_or_else(|| {
                AiError::new(AiErrorKind::ProviderError, "Gemini не повернув відповіді.")
            })?;
        let text = candidate
            .content
            .map(|content| {
                content
                    .parts
                    .into_iter()
                    .filter_map(|part| part.text)
                    .collect::<Vec<_>>()
                    .join("\n")
            })
            .filter(|text| !text.trim().is_empty())
            .ok_or_else(|| {
                AiError::new(
                    AiErrorKind::ProviderError,
                    "Gemini повернув порожню відповідь.",
                )
            })?;
        let sources = candidate
            .grounding
            .map(|metadata| {
                metadata
                    .chunks
                    .into_iter()
                    .filter_map(|chunk| {
                        let web = chunk.web?;
                        Some(GroundingSource {
                            title: web.title.unwrap_or_else(|| "Google Search".to_owned()),
                            url: web.uri?,
                        })
                    })
                    .collect()
            })
            .unwrap_or_default();
        Ok(ProviderResponse {
            message: Message::assistant(text),
            input_tokens: data.usage.as_ref().and_then(|usage| usage.prompt),
            output_tokens: data.usage.and_then(|usage| usage.candidates),
            sources,
            provider: None,
            model: None,
            fallback_count: 0,
        })
    }
}
