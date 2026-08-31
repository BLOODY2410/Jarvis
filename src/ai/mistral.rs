use crate::ai::{openai_compatible::OpenAiCompatibleClient, provider::ProviderKind};
use std::time::Duration;

pub fn client(
    api_key: String,
    model: String,
    connect: Duration,
    read: Duration,
) -> OpenAiCompatibleClient {
    OpenAiCompatibleClient::new(
        ProviderKind::Mistral,
        api_key,
        model,
        "https://api.mistral.ai/v1/chat/completions".to_owned(),
        connect,
        read,
    )
}
