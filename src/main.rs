mod ai;
mod config;
mod core;
mod tools;
mod voice;
mod voice_input;

use std::error::Error;

use crate::{
    ai::build_router, config::Config, core::Agent, voice::VoiceClient,
    voice_input::VoiceInputClient,
};

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    if std::env::args().any(|argument| argument == "--router-benchmark") {
        return core::router_benchmark::run(None).await;
    }
    let config = Config::from_env()?;
    let mut router = build_router(&config);
    if std::env::args().any(|argument| argument == "--router-benchmark-live") {
        return core::router_benchmark::run(Some(&mut router)).await;
    }
    let voice = config.tts_enabled.then(|| VoiceClient::new(config.tts_url));
    let voice_input = config
        .voice_input_enabled
        .then(|| VoiceInputClient::new(config.voice_input_url));
    let mut agent = Agent::new(
        router,
        voice,
        voice_input,
        config.max_tool_rounds,
        config.conversation_timeout_secs,
        config.max_context_turns,
    );

    agent.run().await
}
