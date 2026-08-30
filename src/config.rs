use std::{env, error::Error, io};

use dotenvy::dotenv;

const DEFAULT_MODEL: &str = "openai/gpt-oss-20b";
const DEFAULT_MAX_TOOL_ROUNDS: usize = 8;
const DEFAULT_TTS_URL: &str = "http://127.0.0.1:8765";
const DEFAULT_VOICE_INPUT_URL: &str = "http://127.0.0.1:8766";
const DEFAULT_CONVERSATION_TIMEOUT_SECS: u64 = 25;

pub struct Config {
    pub groq_api_key: String,
    pub groq_model: String,
    pub max_tool_rounds: usize,
    pub tts_enabled: bool,
    pub tts_url: String,
    pub voice_input_enabled: bool,
    pub voice_input_url: String,
    pub conversation_timeout_secs: u64,
}

impl Config {
    pub fn from_env() -> Result<Self, Box<dyn Error>> {
        dotenv().ok();

        let groq_api_key = env::var("GROQ_API_KEY").map_err(|_| {
            io::Error::new(
                io::ErrorKind::NotFound,
                "GROQ_API_KEY не знайдено. Додайте його у файл .env",
            )
        })?;

        let groq_model = env::var("GROQ_MODEL").unwrap_or_else(|_| DEFAULT_MODEL.to_owned());
        let max_tool_rounds = env::var("JARVIS_MAX_TOOL_ROUNDS")
            .ok()
            .and_then(|value| value.parse().ok())
            .filter(|value| *value > 0)
            .unwrap_or(DEFAULT_MAX_TOOL_ROUNDS);
        let tts_enabled = env::var("JARVIS_TTS_ENABLED")
            .map(|value| !matches!(value.to_ascii_lowercase().as_str(), "0" | "false" | "no"))
            .unwrap_or(true);
        let tts_url = env::var("JARVIS_TTS_URL").unwrap_or_else(|_| DEFAULT_TTS_URL.to_owned());
        let voice_input_enabled = env_bool("JARVIS_VOICE_INPUT_ENABLED", true);
        let voice_input_url = env::var("JARVIS_VOICE_INPUT_URL")
            .unwrap_or_else(|_| DEFAULT_VOICE_INPUT_URL.to_owned());
        let conversation_timeout_secs = env::var("JARVIS_CONVERSATION_TIMEOUT_SECS")
            .ok()
            .and_then(|value| value.parse().ok())
            .filter(|value| *value > 0)
            .unwrap_or(DEFAULT_CONVERSATION_TIMEOUT_SECS);

        Ok(Self {
            groq_api_key,
            groq_model,
            max_tool_rounds,
            tts_enabled,
            tts_url,
            voice_input_enabled,
            voice_input_url,
            conversation_timeout_secs,
        })
    }
}

fn env_bool(name: &str, default: bool) -> bool {
    env::var(name)
        .map(|value| {
            !matches!(
                value.trim().to_ascii_lowercase().as_str(),
                "0" | "false" | "no" | "off"
            )
        })
        .unwrap_or(default)
}
