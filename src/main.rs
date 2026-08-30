mod ai;
mod config;
mod core;
mod tools;
mod voice;
mod voice_input;

use std::error::Error;

use crate::{
    ai::GroqClient, config::Config, core::Agent, voice::VoiceClient, voice_input::VoiceInputClient,
};

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    let config = Config::from_env()?;
    let groq = GroqClient::new(config.groq_api_key, config.groq_model);
    let voice = config.tts_enabled.then(|| VoiceClient::new(config.tts_url));
    let voice_input = config
        .voice_input_enabled
        .then(|| VoiceInputClient::new(config.voice_input_url));
    let mut agent = Agent::new(
        groq,
        voice,
        voice_input,
        config.max_tool_rounds,
        config.conversation_timeout_secs,
    );

    agent.run().await
}
