mod cerebras;
pub mod context;
mod gemini;
mod groq;
mod mistral;
mod openai_compatible;
mod openrouter;
pub mod provider;
pub mod router;

use crate::config::Config;
use provider::AiProvider;
use std::{sync::Arc, time::Duration};

pub fn build_router(config: &Config) -> router::AiRouter {
    let mut providers: Vec<Arc<dyn AiProvider>> = Vec::new();
    let connect = Duration::from_millis(config.ai_connect_timeout_ms);
    let read = Duration::from_millis(config.ai_read_timeout_ms);
    if let Some(key) = &config.cerebras_api_key {
        providers.push(Arc::new(cerebras::client(
            key.clone(),
            config.cerebras_model.clone(),
            connect,
            read,
        )));
    }
    if let Some(key) = &config.gemini_api_key {
        providers.push(Arc::new(gemini::GeminiClient::new(
            key.clone(),
            config.gemini_smart_model.clone(),
            connect,
            read,
        )));
        if config.gemini_live_model != config.gemini_smart_model {
            providers.push(Arc::new(gemini::GeminiClient::new(
                key.clone(),
                config.gemini_live_model.clone(),
                connect,
                read,
            )));
        }
    }
    if let Some(key) = &config.groq_api_key {
        providers.push(Arc::new(groq::client(
            key.clone(),
            config.groq_model.clone(),
            connect,
            read,
        )));
    }
    if let (Some(key), Some(model)) = (&config.openrouter_api_key, &config.openrouter_model) {
        providers.push(Arc::new(openrouter::client(
            key.clone(),
            model.clone(),
            connect,
            read,
        )));
    }
    if let (Some(key), Some(model)) = (&config.mistral_api_key, &config.mistral_model) {
        providers.push(Arc::new(mistral::client(
            key.clone(),
            model.clone(),
            connect,
            read,
        )));
    }
    router::AiRouter::new(
        router::RouterConfig {
            enabled: config.ai_router_enabled,
            pc_primary: config.pc_brain_provider,
            chat_primary: config.chat_brain_provider,
            live_primary: config.live_brain_provider,
            fallback_order: config.ai_fallback_order.clone(),
            smart_model: config.gemini_smart_model.clone(),
            live_model: config.gemini_live_model.clone(),
            cooldown_rate_limit: Duration::from_secs(config.ai_rate_limit_cooldown_secs),
            cooldown_timeout: Duration::from_secs(config.ai_timeout_cooldown_secs),
            circuit_failures: config.ai_circuit_failures,
            max_retries: config.ai_max_retries,
            voice_max_tokens: config.ai_voice_max_output_tokens,
            detail_max_tokens: config.ai_detail_max_output_tokens,
        },
        providers,
    )
}
