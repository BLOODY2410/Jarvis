use std::{error::Error, time::Duration};

use reqwest::Client;
use serde::{Deserialize, Serialize};

#[derive(Clone)]
pub struct VoiceInputClient {
    http: Client,
    base_url: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum VoiceEvent {
    Wake,
    SpeechStarted,
    Listening,
    Interrupt,
    Transcript {
        text: String,
        #[serde(default)]
        mic_end_unix_ms: Option<u64>,
        #[serde(default)]
        stt_first_byte_unix_ms: Option<u64>,
        #[serde(default)]
        stt_done_unix_ms: Option<u64>,
    },
    Error {
        message: String,
    },
    Timeout,
    Stopped,
}

#[derive(Serialize)]
struct StateRequest {
    conversation_active: bool,
    speaking: bool,
}

#[derive(Deserialize)]
struct HealthResponse {
    running: bool,
}

impl VoiceInputClient {
    pub fn new(base_url: String) -> Self {
        Self {
            http: Client::new(),
            base_url: base_url.trim_end_matches('/').to_owned(),
        }
    }

    pub async fn health(&self) -> Result<(), Box<dyn Error>> {
        let response = self
            .http
            .get(format!("{}/health", self.base_url))
            .timeout(Duration::from_secs(2))
            .send()
            .await?
            .error_for_status()?
            .json::<HealthResponse>()
            .await?;
        if !response.running {
            return Err("Voice Input Core не слухає мікрофон".into());
        }
        Ok(())
    }

    pub async fn set_state(
        &self,
        conversation_active: bool,
        speaking: bool,
    ) -> Result<(), Box<dyn Error>> {
        self.http
            .post(format!("{}/state", self.base_url))
            .timeout(Duration::from_secs(2))
            .json(&StateRequest {
                conversation_active,
                speaking,
            })
            .send()
            .await?
            .error_for_status()?;
        Ok(())
    }

    pub async fn next_event(&self, timeout: Duration) -> Result<VoiceEvent, Box<dyn Error>> {
        let timeout_secs = timeout.as_secs_f32().clamp(0.1, 30.0);
        let response = self
            .http
            .get(format!("{}/events/next", self.base_url))
            .query(&[("timeout", timeout_secs)])
            .timeout(timeout + Duration::from_secs(2))
            .send()
            .await?
            .error_for_status()?;
        Ok(response.json().await?)
    }
}
