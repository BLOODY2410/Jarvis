use std::{error::Error, io};

use reqwest::Client;
use serde::Deserialize;
use serde_json::json;

use crate::{core::messages::Message, tools::ToolRegistry};

#[derive(Deserialize)]
struct ChatResponse {
    choices: Vec<Choice>,
}

#[derive(Deserialize)]
struct Choice {
    message: Message,
}

pub struct GroqClient {
    client: Client,
    api_key: String,
    model: String,
}

impl GroqClient {
    pub fn new(api_key: String, model: String) -> Self {
        Self {
            client: Client::new(),
            api_key,
            model,
        }
    }

    pub async fn chat(
        &self,
        messages: &[Message],
        tools: &ToolRegistry,
    ) -> Result<Message, Box<dyn Error>> {
        let body = json!({
            "model": self.model,
            "messages": messages,
            "tools": tools.schemas(),
            "tool_choice": "auto"
        });

        let response = self
            .client
            .post("https://api.groq.com/openai/v1/chat/completions")
            .bearer_auth(&self.api_key)
            .json(&body)
            .send()
            .await?;

        if !response.status().is_success() {
            let status = response.status();
            let text = response.text().await?;
            return Err(format!("Помилка Groq API {status}: {text}").into());
        }

        let data: ChatResponse = response.json().await?;
        data.choices
            .into_iter()
            .next()
            .map(|choice| choice.message)
            .ok_or_else(|| io::Error::other("Groq API повернув порожню відповідь").into())
    }
}
