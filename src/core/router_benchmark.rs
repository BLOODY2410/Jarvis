use std::{
    fs::{self, File},
    io::Write,
    time::Instant,
};

use crate::{
    ai::router::{AiRoute, AiRouter},
    core::{fast_command::match_fast_command, messages::Message},
    tools::ToolRegistry,
};
use serde_json::json;

const CASES: &[(&str, &str, Option<&str>)] = &[
    ("Як справи?", "local_fast", None),
    ("Відкрий YouTube.", "local_fast", Some("open_url")),
    ("Відкрай Steam.", "local_fast", Some("open_app")),
    (
        "Яка температура процесора?",
        "computer_agent",
        Some("get_system_info"),
    ),
    ("Що по новинах?", "live_current", None),
    ("Розкажи мені щось цікаве.", "conversation", None),
    (
        "Включи музику, яка тобі подобається.",
        "local_fast",
        Some("play_youtube_music"),
    ),
    (
        "Зроби знімок екрану.",
        "local_fast",
        Some("take_screenshot"),
    ),
    ("А чого ти мене ігноруєш?", "conversation", None),
    ("Ти знаєш хто такий Джарвіс?", "conversation", None),
    ("Яка зараз погода?", "live_current", None),
    ("Скільки зараз коштує Bitcoin?", "live_current", None),
    ("Розкажи історію Bitcoin.", "conversation", None),
    ("Хто виграв матч сьогодні?", "live_current", None),
    (
        "Відкрий браузер і знайди документацію Rust.",
        "computer_agent",
        Some("open_url"),
    ),
    (
        "Покажи запущені програми і відкрий Блокнот.",
        "computer_agent",
        Some("get_running_apps"),
    ),
    (
        "Створи папку для фотографій.",
        "computer_agent",
        Some("create_directory"),
    ),
    ("Поясни, як працює VAD.", "conversation", None),
    ("Який зараз курс долара?", "live_current", None),
    ("Чому небо синє?", "conversation", None),
    ("Закрий блокнот.", "local_fast", Some("close_app")),
    ("Гучність 40 відсотків.", "local_fast", Some("set_volume")),
    ("Постав музику на паузу.", "local_fast", Some("pause_media")),
    ("Який статус сервісу Discord зараз?", "live_current", None),
    (
        "Знайди файл звіту і відкрий його.",
        "computer_agent",
        Some("find_files"),
    ),
];

pub async fn run(router: Option<&mut AiRouter>) -> Result<(), Box<dyn std::error::Error>> {
    fs::create_dir_all("logs")?;
    let mut output = File::create("logs/router-benchmark.jsonl")?;
    let mut correct = 0usize;
    let tools = ToolRegistry::new();
    let mut router = router;
    for (phrase, expected_route, expected_tool) in CASES {
        let started = Instant::now();
        let fast = match_fast_command(phrase);
        let route = if fast.is_some() || is_local_small_talk(phrase) {
            AiRoute::LocalFast
        } else {
            AiRouter::classify(phrase)
        };
        let route_correct = route.as_str() == *expected_route;
        if route_correct {
            correct += 1;
        }
        let mut selected_tool = fast.as_ref().map(|command| command.intent.to_owned());
        let mut provider = None;
        let mut model = None;
        let mut fallback_count = 0;
        let mut input_tokens = None;
        let mut output_tokens = None;
        if let Some(active_router) = router
            .as_deref_mut()
            .filter(|_| route != AiRoute::LocalFast)
        {
            let messages = vec![
                Message::system(
                    "Тест маршрутизації. Не виконуй дії; лише обери потрібний tool, якщо він потрібен.",
                ),
                Message::user(*phrase),
            ];
            match active_router
                .complete(
                    route,
                    &messages,
                    (route == AiRoute::ComputerAgent).then(|| tools.schemas()),
                    false,
                    false,
                )
                .await
            {
                Ok(response) => {
                    selected_tool = response
                        .message
                        .tool_calls
                        .as_ref()
                        .and_then(|calls| calls.first())
                        .map(|call| call.function.name.clone());
                    provider = response.provider.map(|value| value.to_string());
                    model = response.model;
                    fallback_count = response.fallback_count;
                    input_tokens = response.input_tokens;
                    output_tokens = response.output_tokens;
                }
                Err(error) => {
                    provider = Some(format!("unavailable:{:?}", error.kind));
                }
            }
        }
        let row = json!({"phrase":phrase,"expected_route":expected_route,"actual_route":route.as_str(),"route_correct":route_correct,"expected_tool":expected_tool,"selected_tool":selected_tool,"latency_ms":started.elapsed().as_millis(),"provider":provider,"model":model,"input_tokens":input_tokens,"output_tokens":output_tokens,"fallback_count":fallback_count});
        writeln!(output, "{row}")?;
    }
    println!(
        "Router benchmark: {correct}/{} expected routes. Results: logs\\router-benchmark.jsonl",
        CASES.len()
    );
    Ok(())
}

fn is_local_small_talk(input: &str) -> bool {
    ["як справи", "дякую", "привіт", "ти тут"]
        .iter()
        .any(|marker| input.to_lowercase().contains(marker))
}
