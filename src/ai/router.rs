use std::{
    collections::HashMap,
    sync::Arc,
    time::{Duration, Instant},
};

use crate::{
    ai::provider::{
        AiError, AiErrorKind, AiProvider, ProviderKind, ProviderRequest, ProviderResponse,
        ReasoningEffort,
    },
    core::messages::Message,
};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AiRoute {
    LocalFast,
    ComputerAgent,
    Conversation,
    LiveCurrent,
    #[allow(dead_code)]
    Vision,
    Unknown,
}

impl AiRoute {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::LocalFast => "local_fast",
            Self::ComputerAgent => "computer_agent",
            Self::Conversation => "conversation",
            Self::LiveCurrent => "live_current",
            Self::Vision => "vision",
            Self::Unknown => "unknown",
        }
    }
}

#[derive(Debug, Clone)]
pub struct RouterConfig {
    pub enabled: bool,
    pub pc_primary: ProviderKind,
    pub chat_primary: ProviderKind,
    pub live_primary: ProviderKind,
    pub fallback_order: Vec<ProviderKind>,
    pub smart_model: String,
    pub live_model: String,
    pub cooldown_rate_limit: Duration,
    pub cooldown_timeout: Duration,
    pub circuit_failures: u32,
    pub max_retries: usize,
    pub voice_max_tokens: u32,
    pub detail_max_tokens: u32,
}

#[derive(Default, Debug, Clone)]
struct Health {
    last_success: Option<Instant>,
    cooldown_until: Option<Instant>,
    auth_disabled: bool,
    consecutive_failures: u32,
}

pub struct AiRouter {
    config: RouterConfig,
    providers: Vec<Arc<dyn AiProvider>>,
    health: HashMap<String, Health>,
}

impl AiRouter {
    pub fn new(config: RouterConfig, providers: Vec<Arc<dyn AiProvider>>) -> Self {
        Self {
            config,
            providers,
            health: HashMap::new(),
        }
    }

    pub fn classify(input: &str) -> AiRoute {
        let text = normalize(input);
        if text.is_empty() {
            return AiRoute::Unknown;
        }
        let historical = ["історі", "що таке", "чому", "як працює", "розкажи про"]
            .iter()
            .any(|marker| text.contains(marker));
        let live_subject = [
            "новин",
            "погод",
            "курс",
            "ціна",
            "коштує",
            "bitcoin",
            "біткоїн",
            "матч",
            "рахунок",
            "хто виграв",
            "статус сервіс",
        ]
        .iter()
        .any(|marker| text.contains(marker));
        let live_time = [
            "зараз",
            "сьогодні",
            "актуаль",
            "останні",
            "свіжі",
            "що по",
            "наразі",
        ]
        .iter()
        .any(|marker| text.contains(marker));
        if live_subject && !historical && (live_time || text.split_whitespace().count() <= 5) {
            return AiRoute::LiveCurrent;
        }
        let action = [
            "відкрий",
            "запусти",
            "увімкни",
            "включи",
            "закрий",
            "знайди файл",
            "скопіюй",
            "перемісти",
            "створи папку",
            "натисни",
            "покажи програми",
            "покажи запущ",
            "температура процесора",
        ]
        .iter()
        .any(|marker| text.contains(marker));
        if action {
            return AiRoute::ComputerAgent;
        }
        AiRoute::Conversation
    }

    pub fn is_configured_for(&self, route: AiRoute) -> bool {
        !self.chain(route).is_empty()
    }

    pub fn status(&self) -> String {
        let configured = self
            .providers
            .iter()
            .map(|provider| provider.kind().to_string())
            .collect::<Vec<_>>()
            .join(",");
        format!(
            "AI Router: pc={}, chat={}, live={}, fallback={}, configured={}",
            self.config.pc_primary,
            self.config.chat_primary,
            self.config.live_primary,
            self.config
                .fallback_order
                .iter()
                .map(ToString::to_string)
                .collect::<Vec<_>>()
                .join(","),
            configured
        )
    }

    pub async fn complete(
        &mut self,
        route: AiRoute,
        messages: &[Message],
        tools: Option<&[serde_json::Value]>,
        voice_mode: bool,
        detailed: bool,
    ) -> Result<ProviderResponse, AiError> {
        if route == AiRoute::LocalFast {
            return Err(AiError::new(
                AiErrorKind::InvalidRequest,
                "Local Fast Path не викликає AI.",
            ));
        }
        if tools.is_some_and(|value| !value.is_empty()) && route != AiRoute::ComputerAgent {
            return Err(AiError::new(
                AiErrorKind::InvalidRequest,
                "Локальні інструменти дозволені лише Computer Agent.",
            ));
        }
        if route == AiRoute::LiveCurrent && !self.is_configured_for(route) {
            return Err(AiError::new(
                AiErrorKind::Unavailable,
                live_fallback_message(messages),
            ));
        }
        let chain = self.chain(route);
        let max_tokens = if detailed {
            self.config.detail_max_tokens
        } else if voice_mode {
            self.config.voice_max_tokens
        } else {
            self.config.detail_max_tokens.min(512)
        };
        let reasoning = if route == AiRoute::ComputerAgent && is_complex(messages) {
            ReasoningEffort::Medium
        } else if route == AiRoute::ComputerAgent {
            ReasoningEffort::Low
        } else {
            ReasoningEffort::None
        };
        let mut last_error = None;
        for (index, provider) in chain.into_iter().enumerate() {
            let key = health_key(provider.as_ref());
            let health = self.health.entry(key.clone()).or_default();
            if health.auth_disabled
                || health
                    .cooldown_until
                    .is_some_and(|until| until > Instant::now())
            {
                continue;
            }
            let started = Instant::now();
            let request = ProviderRequest {
                messages,
                tools: (route == AiRoute::ComputerAgent).then_some(tools).flatten(),
                reasoning,
                temperature: if route == AiRoute::ComputerAgent {
                    0.0
                } else {
                    0.35
                },
                max_output_tokens: max_tokens,
                live_search: route == AiRoute::LiveCurrent
                    && provider.kind() == ProviderKind::Gemini,
            };
            let mut result = provider.complete(request.clone()).await;
            for _ in 0..self.config.max_retries {
                if !matches!(
                    result.as_ref().err().map(|error| error.kind),
                    Some(
                        AiErrorKind::Timeout
                            | AiErrorKind::Unavailable
                            | AiErrorKind::ProviderError
                    )
                ) {
                    break;
                }
                tokio::time::sleep(Duration::from_millis(150)).await;
                result = provider.complete(request.clone()).await;
            }
            match result {
                Ok(mut response) => {
                    let health = self.health.entry(key).or_default();
                    health.last_success = Some(Instant::now());
                    health.consecutive_failures = 0;
                    health.cooldown_until = None;
                    println!(
                        "[AI Router] route={} provider={} model={} fallback={} latency={}ms",
                        route.as_str(),
                        provider.kind(),
                        provider.model(),
                        index > 0,
                        started.elapsed().as_millis()
                    );
                    if response.input_tokens.is_some() || response.output_tokens.is_some() {
                        println!(
                            "[AI Router] usage_input={} usage_output={}",
                            response
                                .input_tokens
                                .map_or_else(|| "n/a".to_owned(), |value| value.to_string()),
                            response
                                .output_tokens
                                .map_or_else(|| "n/a".to_owned(), |value| value.to_string())
                        );
                    }
                    if !response.sources.is_empty() {
                        println!("[AI Router] grounding_sources={}", response.sources.len());
                    }
                    response.provider = Some(provider.kind());
                    response.model = Some(provider.model().to_owned());
                    response.fallback_count = index;
                    return Ok(response);
                }
                Err(error) => {
                    let health = self.health.entry(key).or_default();
                    health.consecutive_failures += 1;
                    match error.kind {
                        AiErrorKind::Auth => health.auth_disabled = true,
                        AiErrorKind::RateLimit => {
                            health.cooldown_until = Some(
                                Instant::now()
                                    + error.retry_after.unwrap_or(self.config.cooldown_rate_limit),
                            )
                        }
                        AiErrorKind::Timeout => {
                            health.cooldown_until =
                                Some(Instant::now() + self.config.cooldown_timeout)
                        }
                        AiErrorKind::Unavailable | AiErrorKind::ProviderError
                            if health.consecutive_failures >= self.config.circuit_failures =>
                        {
                            health.cooldown_until =
                                Some(Instant::now() + self.config.cooldown_timeout)
                        }
                        _ => {}
                    }
                    println!(
                        "[AI Router] provider={} model={} status={:?} fallback={}",
                        provider.kind(),
                        provider.model(),
                        error.kind,
                        index + 1 < self.providers.len()
                    );
                    last_error = Some(error);
                }
            }
        }
        if route == AiRoute::LiveCurrent {
            Err(AiError::new(
                AiErrorKind::Unavailable,
                live_fallback_message(messages),
            ))
        } else {
            Err(last_error.unwrap_or_else(|| {
                AiError::new(
                    AiErrorKind::Unavailable,
                    "Жоден AI-провайдер зараз не доступний, сер.",
                )
            }))
        }
    }

    fn chain(&self, route: AiRoute) -> Vec<Arc<dyn AiProvider>> {
        if !self.config.enabled {
            return self
                .providers
                .iter()
                .find(|provider| provider.kind() == ProviderKind::Groq)
                .cloned()
                .into_iter()
                .collect();
        }
        let (primary, allow_fallbacks) = match route {
            AiRoute::ComputerAgent => (self.config.pc_primary, true),
            AiRoute::Conversation | AiRoute::Unknown => (self.config.chat_primary, true),
            AiRoute::LiveCurrent => (self.config.live_primary, false),
            _ => return vec![],
        };
        let mut kinds = vec![primary];
        if matches!(route, AiRoute::Conversation | AiRoute::Unknown)
            && primary != ProviderKind::Cerebras
        {
            kinds.push(ProviderKind::Cerebras);
        }
        if allow_fallbacks {
            kinds.extend(self.config.fallback_order.iter().copied());
        }
        let mut result = Vec::new();
        for kind in kinds {
            let preferred_model = if kind == ProviderKind::Gemini {
                Some(if route == AiRoute::LiveCurrent {
                    self.config.live_model.as_str()
                } else {
                    self.config.smart_model.as_str()
                })
            } else {
                None
            };
            if let Some(provider) = self.providers.iter().find(|provider| {
                provider.kind() == kind
                    && preferred_model.is_none_or(|model| provider.model() == model)
            }) {
                if !result.iter().any(|item: &Arc<dyn AiProvider>| {
                    health_key(item.as_ref()) == health_key(provider.as_ref())
                }) {
                    result.push(provider.clone());
                }
            }
        }
        result
    }
}

fn normalize(input: &str) -> String {
    input
        .to_lowercase()
        .replace(['’', '`'], "'")
        .chars()
        .map(|character| {
            if character.is_alphanumeric() || character == '\'' {
                character
            } else {
                ' '
            }
        })
        .collect::<String>()
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
}
fn health_key(provider: &dyn AiProvider) -> String {
    format!("{}:{}", provider.kind(), provider.model())
}
fn is_complex(messages: &[Message]) -> bool {
    messages
        .last()
        .and_then(|message| message.content.as_deref())
        .is_some_and(|text| {
            [" і ", "потім", "після цього", "кілька", "спочатку"]
                .iter()
                .any(|marker| text.to_lowercase().contains(marker))
        })
}
fn live_fallback_message(messages: &[Message]) -> String {
    let text = messages
        .last()
        .and_then(|message| message.content.as_deref())
        .unwrap_or_default()
        .to_lowercase();
    let source = if text.contains("погод") {
        "погоди"
    } else if text.contains("новин") {
        "новин"
    } else if text.contains("матч") || text.contains("рахунок") || text.contains("виграв")
    {
        "спортивних даних"
    } else if text.contains("статус") {
        "статусу сервісу"
    } else {
        "актуальних даних"
    };
    format!("Актуального джерела {source} зараз немає, сер. Не стану вигадувати.")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ai::provider::{GroundingSource, ProviderResponse};
    use async_trait::async_trait;
    use std::sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    };
    struct Mock {
        kind: ProviderKind,
        model: String,
        calls: Arc<AtomicUsize>,
        failure: Option<AiErrorKind>,
    }
    #[async_trait]
    impl AiProvider for Mock {
        fn kind(&self) -> ProviderKind {
            self.kind
        }
        fn model(&self) -> &str {
            &self.model
        }
        async fn complete(
            &self,
            _request: ProviderRequest<'_>,
        ) -> Result<ProviderResponse, AiError> {
            self.calls.fetch_add(1, Ordering::SeqCst);
            if let Some(kind) = self.failure {
                Err(AiError::new(kind, "safe"))
            } else {
                Ok(ProviderResponse {
                    message: Message::assistant("Готово."),
                    input_tokens: None,
                    output_tokens: None,
                    sources: Vec::<GroundingSource>::new(),
                    provider: None,
                    model: None,
                    fallback_count: 0,
                })
            }
        }
    }
    fn config() -> RouterConfig {
        RouterConfig {
            enabled: true,
            pc_primary: ProviderKind::Cerebras,
            chat_primary: ProviderKind::Gemini,
            live_primary: ProviderKind::Gemini,
            fallback_order: vec![ProviderKind::Groq],
            smart_model: "smart".into(),
            live_model: "live".into(),
            cooldown_rate_limit: Duration::from_secs(30),
            cooldown_timeout: Duration::from_secs(10),
            circuit_failures: 2,
            max_retries: 0,
            voice_max_tokens: 180,
            detail_max_tokens: 800,
        }
    }
    #[test]
    fn deterministic_routes_are_correct() {
        assert_eq!(AiRouter::classify("Що по новинах?"), AiRoute::LiveCurrent);
        assert_eq!(
            AiRouter::classify("Розкажи історію Bitcoin"),
            AiRoute::Conversation
        );
        assert_eq!(
            AiRouter::classify("Відкрий браузер і знайди файл"),
            AiRoute::ComputerAgent
        );
        assert_eq!(
            AiRouter::classify("Ти знаєш хто такий Джарвіс?"),
            AiRoute::Conversation
        );
    }
    #[tokio::test]
    async fn rate_limit_falls_back_and_cools_down() {
        let first_calls = Arc::new(AtomicUsize::new(0));
        let second_calls = Arc::new(AtomicUsize::new(0));
        let providers: Vec<Arc<dyn AiProvider>> = vec![
            Arc::new(Mock {
                kind: ProviderKind::Cerebras,
                model: "pc".into(),
                calls: first_calls.clone(),
                failure: Some(AiErrorKind::RateLimit),
            }),
            Arc::new(Mock {
                kind: ProviderKind::Groq,
                model: "qwen".into(),
                calls: second_calls.clone(),
                failure: None,
            }),
        ];
        let mut router = AiRouter::new(config(), providers);
        let messages = vec![Message::user("відкрий програму і покажи файли")];
        router
            .complete(AiRoute::ComputerAgent, &messages, None, true, false)
            .await
            .unwrap();
        assert_eq!(first_calls.load(Ordering::SeqCst), 1);
        assert_eq!(second_calls.load(Ordering::SeqCst), 1);
        router
            .complete(AiRoute::ComputerAgent, &messages, None, true, false)
            .await
            .unwrap();
        assert_eq!(first_calls.load(Ordering::SeqCst), 1);
    }
    #[tokio::test]
    async fn auth_error_disables_provider() {
        let calls = Arc::new(AtomicUsize::new(0));
        let providers: Vec<Arc<dyn AiProvider>> = vec![Arc::new(Mock {
            kind: ProviderKind::Gemini,
            model: "smart".into(),
            calls: calls.clone(),
            failure: Some(AiErrorKind::Auth),
        })];
        let mut router = AiRouter::new(config(), providers);
        let messages = vec![Message::user("Привіт")];
        assert!(
            router
                .complete(AiRoute::Conversation, &messages, None, false, false)
                .await
                .is_err()
        );
        assert!(
            router
                .complete(AiRoute::Conversation, &messages, None, false, false)
                .await
                .is_err()
        );
        assert_eq!(calls.load(Ordering::SeqCst), 1);
    }

    #[tokio::test]
    async fn fast_path_and_non_computer_tools_never_call_providers() {
        let calls = Arc::new(AtomicUsize::new(0));
        let providers: Vec<Arc<dyn AiProvider>> = vec![Arc::new(Mock {
            kind: ProviderKind::Gemini,
            model: "smart".into(),
            calls: calls.clone(),
            failure: None,
        })];
        let mut router = AiRouter::new(config(), providers);
        let messages = vec![Message::user("Привіт")];
        assert!(
            router
                .complete(AiRoute::LocalFast, &messages, None, true, false)
                .await
                .is_err()
        );
        let fake_tools = vec![serde_json::json!({"type":"function"})];
        assert!(
            router
                .complete(
                    AiRoute::Conversation,
                    &messages,
                    Some(&fake_tools),
                    true,
                    false
                )
                .await
                .is_err()
        );
        assert_eq!(calls.load(Ordering::SeqCst), 0);
    }

    #[tokio::test]
    async fn live_without_search_provider_returns_honest_guard() {
        let mut router = AiRouter::new(config(), vec![]);
        let messages = vec![Message::user("Яка зараз погода?")];
        let error = router
            .complete(AiRoute::LiveCurrent, &messages, None, true, false)
            .await
            .unwrap_err();
        assert!(error.safe_message.contains("Не стану вигадувати"));
        assert!(!error.safe_message.starts_with('{'));
    }

    #[tokio::test]
    async fn gemini_rate_limit_falls_back_for_conversation() {
        let gemini_calls = Arc::new(AtomicUsize::new(0));
        let groq_calls = Arc::new(AtomicUsize::new(0));
        let providers: Vec<Arc<dyn AiProvider>> = vec![
            Arc::new(Mock {
                kind: ProviderKind::Gemini,
                model: "smart".into(),
                calls: gemini_calls.clone(),
                failure: Some(AiErrorKind::RateLimit),
            }),
            Arc::new(Mock {
                kind: ProviderKind::Groq,
                model: "qwen".into(),
                calls: groq_calls.clone(),
                failure: None,
            }),
        ];
        let mut router = AiRouter::new(config(), providers);
        let messages = vec![Message::user("Розкажи щось цікаве")];
        router
            .complete(AiRoute::Conversation, &messages, None, true, false)
            .await
            .unwrap();
        assert_eq!(gemini_calls.load(Ordering::SeqCst), 1);
        assert_eq!(groq_calls.load(Ordering::SeqCst), 1);
    }
}
