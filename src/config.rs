use crate::ai::provider::ProviderKind;
use dotenvy::dotenv;
use std::{env, error::Error, io};

const DEFAULT_GROQ_MODEL: &str = "qwen/qwen3.8-27b";
const DEFAULT_CEREBRAS_MODEL: &str = "gpt-oss-120b";
const DEFAULT_GEMINI_SMART_MODEL: &str = "gemini-3.6-flash";
const DEFAULT_GEMINI_LIVE_MODEL: &str = "gemini-2.5-flash";

pub struct Config {
    pub groq_api_key: Option<String>,
    pub groq_model: String,
    pub cerebras_api_key: Option<String>,
    pub cerebras_model: String,
    pub gemini_api_key: Option<String>,
    pub gemini_smart_model: String,
    pub gemini_live_model: String,
    pub openrouter_api_key: Option<String>,
    pub openrouter_model: Option<String>,
    pub mistral_api_key: Option<String>,
    pub mistral_model: Option<String>,
    pub ai_router_enabled: bool,
    pub pc_brain_provider: ProviderKind,
    pub chat_brain_provider: ProviderKind,
    pub live_brain_provider: ProviderKind,
    pub ai_fallback_order: Vec<ProviderKind>,
    pub ai_connect_timeout_ms: u64,
    pub ai_read_timeout_ms: u64,
    pub ai_rate_limit_cooldown_secs: u64,
    pub ai_timeout_cooldown_secs: u64,
    pub ai_circuit_failures: u32,
    pub ai_max_retries: usize,
    pub ai_voice_max_output_tokens: u32,
    pub ai_detail_max_output_tokens: u32,
    pub max_context_turns: usize,
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
        let groq_api_key = secret("GROQ_API_KEY");
        let cerebras_api_key = secret("CEREBRAS_API_KEY");
        let gemini_api_key = secret("GEMINI_API_KEY");
        let openrouter_api_key = secret("OPENROUTER_API_KEY");
        let mistral_api_key = secret("MISTRAL_API_KEY");
        if [
            groq_api_key.as_ref(),
            cerebras_api_key.as_ref(),
            gemini_api_key.as_ref(),
            openrouter_api_key.as_ref(),
            mistral_api_key.as_ref(),
        ]
        .iter()
        .all(|value| value.is_none())
        {
            return Err(io::Error::new(
                io::ErrorKind::NotFound,
                "Не знайдено жодного AI API key у .env",
            )
            .into());
        }
        Ok(Self {
            groq_api_key,
            groq_model: env::var("GROQ_MODEL").unwrap_or_else(|_| DEFAULT_GROQ_MODEL.into()),
            cerebras_api_key,
            cerebras_model: env::var("CEREBRAS_MODEL")
                .unwrap_or_else(|_| DEFAULT_CEREBRAS_MODEL.into()),
            gemini_api_key,
            gemini_smart_model: env::var("GEMINI_SMART_MODEL")
                .unwrap_or_else(|_| DEFAULT_GEMINI_SMART_MODEL.into()),
            gemini_live_model: env::var("GEMINI_LIVE_MODEL")
                .unwrap_or_else(|_| DEFAULT_GEMINI_LIVE_MODEL.into()),
            openrouter_api_key,
            openrouter_model: optional("OPENROUTER_MODEL"),
            mistral_api_key,
            mistral_model: optional("MISTRAL_MODEL"),
            ai_router_enabled: env_bool("JARVIS_AI_ROUTER_ENABLED", true),
            pc_brain_provider: provider("JARVIS_PC_BRAIN_PROVIDER", ProviderKind::Cerebras),
            chat_brain_provider: provider("JARVIS_CHAT_BRAIN_PROVIDER", ProviderKind::Gemini),
            live_brain_provider: provider("JARVIS_LIVE_BRAIN_PROVIDER", ProviderKind::Gemini),
            ai_fallback_order: env::var("JARVIS_AI_FALLBACK_ORDER")
                .unwrap_or_else(|_| "groq,openrouter,mistral".into())
                .split(',')
                .filter_map(ProviderKind::parse)
                .collect(),
            ai_connect_timeout_ms: positive("JARVIS_AI_CONNECT_TIMEOUT_MS", 2500),
            ai_read_timeout_ms: positive("JARVIS_AI_READ_TIMEOUT_MS", 12000),
            ai_rate_limit_cooldown_secs: positive("JARVIS_AI_RATE_LIMIT_COOLDOWN_SECS", 30),
            ai_timeout_cooldown_secs: positive("JARVIS_AI_TIMEOUT_COOLDOWN_SECS", 10),
            ai_circuit_failures: positive("JARVIS_AI_CIRCUIT_FAILURES", 2),
            ai_max_retries: env::var("JARVIS_AI_MAX_RETRIES")
                .ok()
                .and_then(|value| value.parse().ok())
                .unwrap_or(0)
                .min(2),
            ai_voice_max_output_tokens: positive("JARVIS_VOICE_MAX_OUTPUT_TOKENS", 180),
            ai_detail_max_output_tokens: positive("JARVIS_DETAIL_MAX_OUTPUT_TOKENS", 900),
            max_context_turns: positive("JARVIS_MAX_CONTEXT_TURNS", 6),
            max_tool_rounds: positive("JARVIS_MAX_TOOL_ROUNDS", 8),
            tts_enabled: env_bool("JARVIS_TTS_ENABLED", true),
            tts_url: env::var("JARVIS_TTS_URL").unwrap_or_else(|_| "http://127.0.0.1:8765".into()),
            voice_input_enabled: env_bool("JARVIS_VOICE_INPUT_ENABLED", true),
            voice_input_url: env::var("JARVIS_VOICE_INPUT_URL")
                .unwrap_or_else(|_| "http://127.0.0.1:8766".into()),
            conversation_timeout_secs: positive("JARVIS_CONVERSATION_TIMEOUT_SECS", 60),
        })
    }
}

fn secret(name: &str) -> Option<String> {
    optional(name).filter(|value| !matches!(value.as_str(), "replace_me" | "changeme"))
}
fn optional(name: &str) -> Option<String> {
    env::var(name)
        .ok()
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
}
fn provider(name: &str, default: ProviderKind) -> ProviderKind {
    env::var(name)
        .ok()
        .and_then(|value| ProviderKind::parse(&value))
        .unwrap_or(default)
}
fn positive<T>(name: &str, default: T) -> T
where
    T: std::str::FromStr + PartialOrd + Default + Copy,
{
    env::var(name)
        .ok()
        .and_then(|value| value.parse().ok())
        .filter(|value| *value > T::default())
        .unwrap_or(default)
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

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn confirmed_defaults_are_current() {
        assert_eq!(DEFAULT_CEREBRAS_MODEL, "gpt-oss-120b");
        assert_eq!(DEFAULT_GROQ_MODEL, "qwen/qwen3.8-27b");
        assert_eq!(DEFAULT_GEMINI_SMART_MODEL, "gemini-3.6-flash");
        assert_eq!(DEFAULT_GEMINI_LIVE_MODEL, "gemini-2.5-flash");
    }
}
