use std::{error::Error, io, time::Duration};

use reqwest::{Client, StatusCode, header::RETRY_AFTER};
use serde::Deserialize;
use serde_json::json;

use crate::{core::messages::Message, tools::ToolRegistry};

const DEFAULT_ENDPOINT: &str = "https://api.groq.com/openai/v1/chat/completions";

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
    endpoint: String,
    max_retries: usize,
    max_completion_tokens: u32,
}

impl GroqClient {
    pub fn new(
        api_key: String,
        model: String,
        max_retries: usize,
        max_completion_tokens: u32,
    ) -> Self {
        Self {
            client: Client::builder()
                .connect_timeout(Duration::from_secs(5))
                .timeout(Duration::from_secs(30))
                .build()
                .expect("valid HTTP client"),
            api_key,
            model,
            endpoint: DEFAULT_ENDPOINT.to_owned(),
            max_retries: max_retries.min(1),
            max_completion_tokens,
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
            "tool_choice": "auto",
            "temperature": 0,
            "max_completion_tokens": self.max_completion_tokens,
        });

        for attempt in 0..=self.max_retries {
            let response = match self
                .client
                .post(&self.endpoint)
                .bearer_auth(&self.api_key)
                .json(&body)
                .send()
                .await
            {
                Ok(response) => response,
                Err(error) if error.is_timeout() => {
                    return Err(io::Error::new(
                        io::ErrorKind::TimedOut,
                        "Модель не відповіла вчасно, сер. Спробуйте ще раз.",
                    )
                    .into());
                }
                Err(_) => {
                    return Err(io::Error::new(
                        io::ErrorKind::ConnectionAborted,
                        "Немає зв'язку з моделлю, сер. Перевірте мережу.",
                    )
                    .into());
                }
            };

            let status = response.status();
            let retry_header = response
                .headers()
                .get(RETRY_AFTER)
                .and_then(|value| value.to_str().ok())
                .and_then(parse_retry_after);
            if status.is_success() {
                let data: ChatResponse = response
                    .json()
                    .await
                    .map_err(|_| io::Error::other("Модель повернула некоректну відповідь, сер."))?;
                return data
                    .choices
                    .into_iter()
                    .next()
                    .map(|choice| choice.message)
                    .ok_or_else(|| {
                        io::Error::other("Модель повернула порожню відповідь, сер.").into()
                    });
            }

            let text = response.text().await.unwrap_or_default();
            if status == StatusCode::TOO_MANY_REQUESTS {
                let delay = retry_header
                    .or_else(|| parse_retry_delay_from_body(&text))
                    .unwrap_or(Duration::from_millis(750))
                    .min(Duration::from_secs(3));
                eprintln!(
                    "[Groq] status=429 retry={} delay_ms={}",
                    attempt < self.max_retries,
                    delay.as_millis()
                );
                if attempt < self.max_retries {
                    tokio::time::sleep(delay).await;
                    continue;
                }
                return Err(io::Error::other(
                    "Модель тимчасово перевантажена, сер. Спробуйте за кілька секунд.",
                )
                .into());
            }
            if status.is_server_error() {
                eprintln!("[Groq] status={} server_error=true", status.as_u16());
                return Err(io::Error::other(
                    "Сервіс моделі тимчасово недоступний, сер. Спробуйте за кілька секунд.",
                )
                .into());
            }
            eprintln!("[Groq] status={} request_rejected=true", status.as_u16());
            return Err(
                io::Error::other("Модель відхилила запит, сер. Перевірте конфігурацію.").into(),
            );
        }
        unreachable!()
    }
}

fn parse_retry_after(value: &str) -> Option<Duration> {
    value
        .trim()
        .parse::<f64>()
        .ok()
        .and_then(duration_from_secs)
}

fn parse_retry_delay_from_body(body: &str) -> Option<Duration> {
    let lower = body.to_ascii_lowercase();
    let marker = "try again in ";
    let start = lower.find(marker)? + marker.len();
    let tail = &lower[start..];
    let number_len = tail
        .chars()
        .take_while(|character| character.is_ascii_digit() || *character == '.')
        .count();
    let value = tail[..number_len].parse::<f64>().ok()?;
    if tail[number_len..].starts_with("ms") {
        Some(Duration::from_secs_f64(value / 1000.0))
    } else if tail[number_len..].starts_with('s') {
        duration_from_secs(value)
    } else {
        None
    }
}

fn duration_from_secs(value: f64) -> Option<Duration> {
    value
        .is_finite()
        .then(|| Duration::from_secs_f64(value.max(0.0)))
}

#[cfg(test)]
mod tests {
    use super::{GroqClient, parse_retry_after, parse_retry_delay_from_body};
    use crate::{core::messages::Message, tools::ToolRegistry};
    use std::{
        io::{Read, Write},
        net::TcpListener,
        thread,
        time::Duration,
    };

    #[test]
    fn parses_retry_delays_without_exposing_error_payload() {
        assert_eq!(parse_retry_after("2"), Some(Duration::from_secs(2)));
        assert_eq!(
            parse_retry_delay_from_body(
                r#"{"error":"try again in 850ms","organization":"secret"}"#
            ),
            Some(Duration::from_millis(850))
        );
        assert_eq!(
            parse_retry_delay_from_body("Please try again in 1.5s"),
            Some(Duration::from_millis(1500))
        );
    }

    #[tokio::test]
    async fn retries_429_once_then_returns_clean_error() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let server = thread::spawn(move || {
            for _ in 0..2 {
                let (mut stream, _) = listener.accept().unwrap();
                let mut request = [0_u8; 8192];
                let _ = stream.read(&mut request);
                let body = r#"{"error":{"message":"try again in 0ms","organization":"org-secret","url":"https://billing.invalid"}}"#;
                let response = format!(
                    "HTTP/1.1 429 Too Many Requests\r\nRetry-After: 0\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                    body.len(),
                    body
                );
                stream.write_all(response.as_bytes()).unwrap();
            }
        });
        let mut client = GroqClient::new("test-key".to_owned(), "test-model".to_owned(), 1, 64);
        client.endpoint = format!("http://{address}");
        let error = client
            .chat(
                &[Message::system("system"), Message::user("test")],
                &ToolRegistry::new(),
            )
            .await
            .unwrap_err()
            .to_string();
        server.join().unwrap();
        assert_eq!(
            error,
            "Модель тимчасово перевантажена, сер. Спробуйте за кілька секунд."
        );
        assert!(!error.contains("org-secret"));
        assert!(!error.contains("billing"));
    }
}
