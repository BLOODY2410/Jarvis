use serde_json::json;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FastCommand {
    pub intent: &'static str,
    pub arguments: String,
    pub acknowledgement: &'static str,
}

pub fn match_fast_command(input: &str) -> Option<FastCommand> {
    let text = normalize(input);
    if text.is_empty() || contains_complexity_marker(&text) {
        return None;
    }

    if matches_any(
        &text,
        &[
            "зроби голосніше",
            "збільш гучність",
            "додай гучність",
            "голосніше",
        ],
    ) {
        return command("volume_up", json!({}), "Виконую, пане.");
    }
    if matches_any(
        &text,
        &[
            "зроби тихіше",
            "зменш гучність",
            "прибери гучність",
            "тихіше",
        ],
    ) {
        return command("volume_down", json!({}), "Виконую, пане.");
    }
    if matches_any(
        &text,
        &["вимкни звук", "прибери звук", "без звуку", "заглуши звук"],
    ) {
        return command("mute", json!({}), "Виконую, пане.");
    }
    if matches_any(
        &text,
        &[
            "увімкни звук",
            "включи звук",
            "поверни звук",
            "розблокуй звук",
        ],
    ) {
        return command("unmute", json!({}), "Виконую, пане.");
    }
    if matches_any(
        &text,
        &[
            "що запущено",
            "які програми запущені",
            "покажи запущені програми",
            "список запущених програм",
        ],
    ) {
        return command(
            "get_running_apps",
            json!({}),
            "Ось запущені програми, пане.",
        );
    }
    if matches_any(
        &text,
        &[
            "зроби скріншот",
            "зроби знімок екрана",
            "знімок екрана",
            "скріншот",
        ],
    ) {
        return command("take_screenshot", json!({}), "Виконую, пане.");
    }

    if let Some(level) = parse_volume_level(&text) {
        return command("set_volume", json!({"level": level}), "Виконую, пане.");
    }

    if let Some(target) = strip_verb(
        &text,
        &["відкрий", "відкрити", "запусти", "запустити", "включи"],
    ) {
        if let Some(url) = website_url(target) {
            return command("open_url", json!({"url": url}), "Виконую, пане.");
        }
        if let Some(domain) = target
            .strip_prefix("сайт ")
            .or_else(|| target.strip_prefix("сайтик "))
        {
            let url = normalize_url(domain)?;
            return command("open_url", json!({"url": url}), "Виконую, пане.");
        }
        if target.contains('.') && !target.contains(' ') {
            let url = normalize_url(target)?;
            return command("open_url", json!({"url": url}), "Виконую, пане.");
        }
        let app = app_alias(target)?;
        return command("open_app", json!({"app": app}), "Виконую, пане.");
    }

    if let Some(target) = strip_verb(
        &text,
        &["закрий", "закрити", "заверши", "зупини", "вимкни програму"],
    ) {
        let target = target
            .strip_prefix("програму ")
            .or_else(|| target.strip_prefix("програму"))
            .unwrap_or(target)
            .trim();
        let app = app_alias(target)?;
        return command("close_app", json!({"app": app}), "Виконую, пане.");
    }

    None
}

fn command(
    intent: &'static str,
    arguments: serde_json::Value,
    acknowledgement: &'static str,
) -> Option<FastCommand> {
    Some(FastCommand {
        intent,
        arguments: arguments.to_string(),
        acknowledgement,
    })
}

fn normalize(input: &str) -> String {
    let lower = input.to_lowercase().replace(['’', '`'], "'");
    let cleaned: String = lower
        .chars()
        .map(|character| {
            if character.is_alphanumeric() || matches!(character, '.' | ':' | '/' | '%' | '-' | '_')
            {
                character
            } else {
                ' '
            }
        })
        .collect();
    cleaned.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn contains_complexity_marker(text: &str) -> bool {
    [
        " і ",
        " а потім ",
        " після цього ",
        " знайди ",
        " пошукай ",
        " якщо ",
        " коли ",
    ]
    .iter()
    .any(|marker| text.contains(marker))
}

fn matches_any(text: &str, variants: &[&str]) -> bool {
    variants.contains(&text)
}

fn strip_verb<'a>(text: &'a str, verbs: &[&str]) -> Option<&'a str> {
    verbs.iter().find_map(|verb| {
        text.strip_prefix(verb)
            .and_then(|rest| rest.strip_prefix(' '))
            .map(str::trim)
            .filter(|rest| !rest.is_empty())
    })
}

fn parse_volume_level(text: &str) -> Option<u8> {
    let rest = strip_verb(
        text,
        &[
            "гучність",
            "встанови гучність",
            "постав гучність",
            "зроби гучність",
        ],
    )?;
    let rest = rest.strip_prefix("на ").unwrap_or(rest);
    let rest = rest
        .strip_suffix(" відсотків")
        .or_else(|| rest.strip_suffix(" відсотка"))
        .or_else(|| rest.strip_suffix(" відсоток"))
        .unwrap_or(rest);
    let number = rest.trim_end_matches('%').trim().parse::<u8>().ok()?;
    (number <= 100).then_some(number)
}

fn website_url(target: &str) -> Option<&'static str> {
    match target.trim_start_matches("сайт ") {
        "ютуб" | "youtube" | "ю туб" => Some("https://www.youtube.com"),
        "гугл" | "google" => Some("https://www.google.com"),
        "гітхаб" | "github" => Some("https://github.com"),
        "фейсбук" | "facebook" => Some("https://www.facebook.com"),
        "інстаграм" | "instagram" => Some("https://www.instagram.com"),
        "телеграм" | "telegram" => Some("https://web.telegram.org"),
        _ => None,
    }
}

fn normalize_url(target: &str) -> Option<String> {
    let target = target.trim().trim_end_matches('/');
    if target.is_empty() || target.contains(char::is_whitespace) || !target.contains('.') {
        return None;
    }
    Some(
        if target.starts_with("https://") || target.starts_with("http://") {
            target.to_owned()
        } else {
            format!("https://{target}")
        },
    )
}

fn app_alias(target: &str) -> Option<&str> {
    let target = target.trim();
    if target.is_empty() || target.split_whitespace().count() > 4 {
        return None;
    }
    Some(match target {
        "браузер" | "хром" | "google chrome" | "гугл хром" => "chrome",
        "калькулятор" | "calculator" => "калькулятор",
        "блокнот" | "notepad" => "блокнот",
        "пейнт" | "паінт" | "paint" => "paint",
        "провідник" | "проводник" | "explorer" => "провідник",
        "диспетчер завдань" | "диспетчер задач" => {
            "диспетчер завдань"
        }
        "командний рядок" | "cmd" => "cmd",
        "павершел" | "powershell" => "powershell",
        "стім" | "steam" => "steam",
        "діскорд" | "discord" => "discord",
        "телеграм" | "telegram" => "telegram",
        other => other,
    })
}

#[cfg(test)]
mod tests {
    use super::match_fast_command;

    fn intent(text: &str) -> Option<&'static str> {
        match_fast_command(text).map(|command| command.intent)
    }

    #[test]
    fn benchmark_commands_take_the_fast_path() {
        assert_eq!(intent("Відкрий калькулятор"), Some("open_app"));
        assert_eq!(intent("Відкрий YouTube"), Some("open_url"));
        assert_eq!(intent("Зроби тихіше"), Some("volume_down"));
        assert_eq!(intent("Вимкни звук"), Some("mute"));
        assert_eq!(intent("Закрий блокнот"), Some("close_app"));
    }

    #[test]
    fn supports_required_local_intents() {
        assert_eq!(intent("відкрий сайт example.com"), Some("open_url"));
        assert_eq!(intent("гучність 42%"), Some("set_volume"));
        assert_eq!(
            intent("встанови гучність на 50 відсотків"),
            Some("set_volume")
        );
        assert_eq!(intent("зроби голосніше"), Some("volume_up"));
        assert_eq!(intent("увімкни звук"), Some("unmute"));
        assert_eq!(intent("що запущено"), Some("get_running_apps"));
        assert_eq!(intent("зроби скріншот"), Some("take_screenshot"));
    }

    #[test]
    fn complex_or_uncertain_requests_fall_back_to_llm() {
        assert_eq!(intent("відкрий браузер і знайди прогноз погоди"), None);
        assert_eq!(intent("що ти думаєш про калькулятор"), None);
        assert_eq!(intent("гучність дуже висока"), None);
    }
}
