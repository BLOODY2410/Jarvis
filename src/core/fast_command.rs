use serde_json::json;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FastCommand {
    pub intent: &'static str,
    pub arguments: String,
    pub acknowledgement: &'static str,
    pub acknowledgement_cache: Option<&'static str>,
}

pub fn match_fast_command(input: &str) -> Option<FastCommand> {
    let text = expand_known_merged_command(&normalize(input));
    if text.is_empty() || contains_complexity_marker(&text) {
        return None;
    }

    if matches_any(
        &text,
        &[
            "відкрий youtube music",
            "відкрий ютуб music",
            "відкрий ютуб музику",
        ],
    ) {
        return command(
            "open_youtube_music",
            json!({}),
            "YouTube Music відкрито.",
            None,
        );
    }
    if ["включи музику", "увімкни музику"]
        .iter()
        .any(|prefix| text.starts_with(prefix))
        && [
            "яка тобі нравиться",
            "яка тобі подобається",
            "яку ти любиш",
            "на свій смак",
            "якусь музику",
        ]
        .iter()
        .any(|tail| text.contains(tail))
    {
        return command(
            "play_youtube_music",
            json!({}),
            "Музику запущено в YouTube Music.",
            None,
        );
    }
    if matches_any(
        &text,
        &[
            "включи музику в youtube music",
            "увімкни музику в youtube music",
            "включи якусь музику в youtube music",
            "увімкни якусь музику в youtube music",
            "включи музику в ютуб music",
        ],
    ) {
        return command(
            "play_youtube_music",
            json!({}),
            "Музику запущено в YouTube Music.",
            None,
        );
    }
    if matches_any(
        &text,
        &[
            "постав на паузу",
            "постав музику на паузу",
            "пауза",
            "призупини музику",
        ],
    ) {
        return command("pause_media", json!({}), "Відтворення призупинено.", None);
    }
    if matches_any(
        &text,
        &["продовж музику", "продовж відтворення", "зніми з паузи"],
    ) {
        return command("resume_media", json!({}), "Відтворення продовжено.", None);
    }
    if matches_any(&text, &["наступний трек", "увімкни наступний трек"])
    {
        return command("next_track", json!({}), "Увімкнено наступний трек.", None);
    }
    if matches_any(&text, &["попередній трек", "увімкни попередній трек"])
    {
        return command(
            "previous_track",
            json!({}),
            "Увімкнено попередній трек.",
            None,
        );
    }

    if matches_any(
        &text,
        &[
            "зроби голосніше",
            "зробити голосніше",
            "збільш гучність",
            "додай гучність",
            "голосніше",
        ],
    ) {
        return command(
            "volume_up",
            json!({}),
            "Гучність збільшено.",
            Some("louder"),
        );
    }
    if matches_any(
        &text,
        &[
            "зроби тихіше",
            "зробити тихіше",
            "зменш гучність",
            "прибери гучність",
            "тихіше",
        ],
    ) {
        return command(
            "volume_down",
            json!({}),
            "Гучність зменшено.",
            Some("quieter"),
        );
    }
    if matches_any(
        &text,
        &["вимкни звук", "прибери звук", "без звуку", "заглуши звук"],
    ) {
        return command("mute", json!({}), "Звук вимкнено.", Some("muted"));
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
        return command("unmute", json!({}), "Готово.", Some("done"));
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
        return command("get_running_apps", json!({}), "Готово.", Some("done"));
    }
    if matches_any(
        &text,
        &[
            "зроби скріншот",
            "зроби знімок екрана",
            "зроби знімок екрану",
            "знімок екрана",
            "знімок екрану",
            "скріншот",
        ],
    ) {
        return command("take_screenshot", json!({}), "Готово.", Some("done"));
    }

    if let Some(level) = parse_volume_level(&text) {
        return command(
            "set_volume",
            json!({"level": level}),
            "Готово.",
            Some("done"),
        );
    }

    if let Some(target) = strip_safe_fuzzy_verb(
        &text,
        &["відкрий", "відкрити", "запусти", "запустити", "включи"],
    ) {
        if let Some(app) = known_app_alias(target) {
            return command(
                "open_app",
                json!({"app": app}),
                app_opened_ack(app),
                Some("opened"),
            );
        }
        if let Some(url) = website_url(target) {
            return command(
                "open_url",
                json!({"url": url}),
                website_opened_ack(url),
                Some("opened"),
            );
        }
        if let Some(domain) = target
            .strip_prefix("сайт ")
            .or_else(|| target.strip_prefix("сайтик "))
        {
            let url = normalize_url(domain)?;
            return command("open_url", json!({"url": url}), "Відкрито.", Some("opened"));
        }
        if is_valid_domain(target) {
            let url = normalize_url(target)?;
            return command(
                "open_url",
                json!({"url": url}),
                "Сайт відкрито.",
                Some("opened"),
            );
        }
        let app = app_alias(target)?;
        return command(
            "open_app",
            json!({"app": app}),
            app_opened_ack(app),
            Some("opened"),
        );
    }

    // Colloquial "давай Steam/Chrome" is safe only when the target resolves to
    // a finite, known alias. Unknown nouns still fall through to the LLM.
    if let Some(target) = strip_verb(&text, &["давай"])
        && let Some(app) = known_app_alias(target)
    {
        return command("open_app", json!({"app": app}), "Відкрито.", Some("opened"));
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
        let app = exact_app_alias(target)?;
        return command(
            "close_app",
            json!({"app": app}),
            "Програму закрито.",
            Some("closed"),
        );
    }

    None
}

fn command(
    intent: &'static str,
    arguments: serde_json::Value,
    acknowledgement: &'static str,
    acknowledgement_cache: Option<&'static str>,
) -> Option<FastCommand> {
    Some(FastCommand {
        intent,
        arguments: arguments.to_string(),
        acknowledgement,
        acknowledgement_cache,
    })
}

fn normalize(input: &str) -> String {
    let lower = input
        .trim()
        .trim_end_matches(['.', ',', '!', '?', ':', ';'])
        .to_lowercase()
        .replace(['’', '`'], "'");
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
    let fillers = [
        "мені",
        "будь",
        "ласка",
        "можеш",
        "ну",
        "джарвіс",
        "джарвис",
        "джарвиз",
        "сер",
    ];
    cleaned
        .split_whitespace()
        .filter(|word| !fillers.contains(word))
        .collect::<Vec<_>>()
        .join(" ")
}

fn expand_known_merged_command(text: &str) -> String {
    const VERBS: &[(&str, &str)] = &[
        ("відкрий", "відкрий"),
        ("відкри", "відкрий"),
        ("відкрі", "відкрий"),
        ("запусти", "запусти"),
        ("включи", "включи"),
    ];
    const TARGETS: &[(&str, &str)] = &[
        ("стім", "стім"),
        ("steam", "steam"),
        ("ютуб", "ютуб"),
        ("youtube", "youtube"),
        ("хром", "хром"),
        ("chrome", "chrome"),
        ("дискорд", "дискорд"),
        ("discord", "discord"),
        ("телеграм", "телеграм"),
        ("telegram", "telegram"),
        ("vscode", "vscode"),
        ("віескод", "vscode"),
        ("visualstudiocode", "visual studio code"),
        ("візуалстудіокод", "visual studio code"),
        ("калькулятор", "калькулятор"),
        ("calculator", "calculator"),
        ("блокнот", "блокнот"),
        ("notepad", "notepad"),
    ];
    if text.contains(' ') {
        return text.to_owned();
    }
    for (prefix, verb) in VERBS {
        if let Some(target) = text.strip_prefix(prefix)
            && let Some((_, expanded)) = TARGETS.iter().find(|(alias, _)| *alias == target)
        {
            return format!("{verb} {expanded}");
        }
    }
    text.to_owned()
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

fn strip_safe_fuzzy_verb<'a>(text: &'a str, verbs: &[&str]) -> Option<&'a str> {
    let (candidate, rest) = text.split_once(' ')?;
    let allowed = if candidate.chars().count() >= 8 { 2 } else { 1 };
    verbs
        .iter()
        .any(|verb| bounded_edit_distance(candidate, verb, allowed) <= allowed)
        .then(|| rest.trim())
        .filter(|rest| !rest.is_empty())
}

fn bounded_edit_distance(left: &str, right: &str, limit: usize) -> usize {
    let left: Vec<char> = left.chars().collect();
    let right: Vec<char> = right.chars().collect();
    if left.len().abs_diff(right.len()) > limit {
        return limit + 1;
    }
    let mut previous: Vec<usize> = (0..=right.len()).collect();
    for (row, left_char) in left.iter().enumerate() {
        let mut current = vec![row + 1];
        let mut row_min = row + 1;
        for (column, right_char) in right.iter().enumerate() {
            let value = (current[column] + 1)
                .min(previous[column + 1] + 1)
                .min(previous[column] + usize::from(left_char != right_char));
            current.push(value);
            row_min = row_min.min(value);
        }
        if row_min > limit {
            return limit + 1;
        }
        previous = current;
    }
    previous[right.len()]
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
    safe_alias(
        target.trim_start_matches("сайт "),
        &[
            ("ютуб", "https://www.youtube.com"),
            ("youtube", "https://www.youtube.com"),
            ("ю туб", "https://www.youtube.com"),
            ("гугл", "https://www.google.com"),
            ("google", "https://www.google.com"),
            ("гітхаб", "https://github.com"),
            ("github", "https://github.com"),
            ("фейсбук", "https://www.facebook.com"),
            ("facebook", "https://www.facebook.com"),
            ("інстаграм", "https://www.instagram.com"),
            ("instagram", "https://www.instagram.com"),
            ("телеграм", "https://web.telegram.org"),
            ("telegram", "https://web.telegram.org"),
        ],
    )
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

fn is_valid_domain(target: &str) -> bool {
    let without_scheme = target
        .strip_prefix("https://")
        .or_else(|| target.strip_prefix("http://"))
        .unwrap_or(target);
    let host = without_scheme.split(['/', ':']).next().unwrap_or_default();
    if host.is_empty() || host.ends_with('.') || host.contains(char::is_whitespace) {
        return false;
    }
    let mut labels = host.split('.');
    let first = labels.next().unwrap_or_default();
    let rest: Vec<_> = labels.collect();
    let tld = rest.last().copied().unwrap_or_default();
    !first.is_empty()
        && !rest.is_empty()
        && tld.len() >= 2
        && host
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '.')
        && tld.chars().all(|c| c.is_ascii_alphabetic())
}

fn app_opened_ack(app: &str) -> &'static str {
    match app {
        "steam" => "Steam відкрито.",
        "discord" => "Discord відкрито.",
        "telegram" => "Telegram відкрито.",
        "chrome" => "Chrome відкрито.",
        "visual studio code" => "Visual Studio Code відкрито.",
        "калькулятор" => "Калькулятор відкрито.",
        "блокнот" => "Блокнот відкрито.",
        _ => "Програму відкрито.",
    }
}

fn website_opened_ack(url: &str) -> &'static str {
    if url.contains("youtube.com") {
        "YouTube відкрито."
    } else {
        "Сайт відкрито."
    }
}

fn app_alias(target: &str) -> Option<&str> {
    let target = target.trim();
    if target.is_empty() || target.split_whitespace().count() > 4 {
        return None;
    }
    safe_alias(
        target,
        &[
            ("браузер", "chrome"),
            ("хром", "chrome"),
            ("google chrome", "chrome"),
            ("гугл хром", "chrome"),
            ("калькулятор", "калькулятор"),
            ("calculator", "калькулятор"),
            ("блокнот", "блокнот"),
            ("notepad", "блокнот"),
            ("пейнт", "paint"),
            ("паінт", "paint"),
            ("paint", "paint"),
            ("провідник", "провідник"),
            ("проводник", "провідник"),
            ("explorer", "провідник"),
            ("диспетчер завдань", "диспетчер завдань"),
            ("диспетчер задач", "диспетчер завдань"),
            ("командний рядок", "cmd"),
            ("cmd", "cmd"),
            ("павершел", "powershell"),
            ("powershell", "powershell"),
            ("стім", "steam"),
            ("steam", "steam"),
            ("діскорд", "discord"),
            ("дискорд", "discord"),
            ("discord", "discord"),
            ("телеграм", "telegram"),
            ("telegram", "telegram"),
            ("visual studio code", "visual studio code"),
            ("vs code", "visual studio code"),
            ("vscode", "visual studio code"),
            ("візуал студіо код", "visual studio code"),
        ],
    )
    .or(Some(target))
}

fn exact_app_alias(target: &str) -> Option<&str> {
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
        "діскорд" | "дискорд" | "discord" => "discord",
        "телеграм" | "telegram" => "telegram",
        "visual studio code" | "vs code" | "vscode" | "візуал студіо код" => {
            "visual studio code"
        }
        other => other,
    })
}

fn safe_alias<'a>(target: &str, aliases: &[(&str, &'a str)]) -> Option<&'a str> {
    let target_length = target.chars().count();
    let mut best: Option<(usize, &'a str)> = None;
    let mut ambiguous = false;
    for (alias, value) in aliases {
        let alias_length = alias.chars().count();
        let allowed = if target_length.max(alias_length) >= 9 {
            2
        } else {
            1
        };
        let distance = bounded_edit_distance(target, alias, allowed);
        if distance > allowed {
            continue;
        }
        match best {
            None => {
                best = Some((distance, *value));
                ambiguous = false;
            }
            Some((best_distance, _)) if distance < best_distance => {
                best = Some((distance, *value));
                ambiguous = false;
            }
            Some((best_distance, best_value))
                if distance == best_distance && *value != best_value =>
            {
                ambiguous = true;
            }
            _ => {}
        }
    }
    best.and_then(|(_, value)| (!ambiguous).then_some(value))
}

fn known_app_alias(target: &str) -> Option<&str> {
    let alias = app_alias(target)?;
    [
        "chrome",
        "калькулятор",
        "блокнот",
        "paint",
        "провідник",
        "диспетчер завдань",
        "cmd",
        "powershell",
        "steam",
        "discord",
        "telegram",
        "visual studio code",
    ]
    .contains(&alias)
    .then_some(alias)
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
        assert_eq!(intent("відкрий мені ютуб"), Some("open_url"));
        assert_eq!(intent("давай стім"), Some("open_app"));
        assert_eq!(intent("можеш зробити тихіше"), Some("volume_down"));
        assert_eq!(intent("можеш відкрити chrome"), Some("open_app"));
        assert_eq!(intent("відкрай ютуб"), Some("open_url"));
        assert_eq!(intent("відкрей ютуб"), Some("open_url"));
        assert_eq!(intent("відкри ютуб"), Some("open_url"));
        assert_eq!(intent("відкрій ютуб"), Some("open_url"));
        assert_eq!(intent("відкрей visual studio code"), Some("open_app"));
        assert_eq!(
            intent("ну Джарвіс відкрий хром, будь ласка"),
            Some("open_app")
        );
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
        assert_eq!(intent("зроби знімок екрану"), Some("take_screenshot"));
        assert_eq!(
            intent("включи музику яка тобі нравиться"),
            Some("play_youtube_music")
        );
        let music = match_fast_command("Включи музику, яка тобі нравиться").unwrap();
        assert_ne!(music.intent, "open_app");
    }

    #[test]
    fn complex_or_uncertain_requests_fall_back_to_llm() {
        assert_eq!(intent("відкрий браузер і знайди прогноз погоди"), None);
        assert_eq!(intent("що ти думаєш про калькулятор"), None);
        assert_eq!(intent("гучність дуже висока"), None);
        assert_eq!(intent("давай невідому програму"), None);
    }

    #[test]
    fn fuzzy_matching_is_limited_to_safe_verbs_and_aliases() {
        assert_eq!(intent("відкрий калькулятер"), Some("open_app"));
        assert_eq!(intent("відкрий блакнот"), Some("open_app"));
        assert_eq!(intent("давай стим"), Some("open_app"));
        assert_eq!(intent("відкрий дискорд"), Some("open_app"));
        assert_eq!(intent("зокрий блокнот"), None);
        assert_eq!(intent("відкрий браузер і знайди погоду"), None);
    }

    #[test]
    fn trailing_punctuation_does_not_turn_apps_into_domains() {
        let steam = match_fast_command("Відкрий, Steam.").unwrap();
        assert_eq!(steam.intent, "open_app");
        assert!(steam.arguments.contains(r#""app":"steam""#));
        let domain = match_fast_command("Відкрий youtube.com.").unwrap();
        assert_eq!(domain.intent, "open_url");
        assert!(domain.arguments.contains("https://youtube.com"));
        for app in ["steam.", "discord.", "chrome."] {
            assert_eq!(intent(&format!("відкрий {app}")), Some("open_app"));
        }
    }

    #[test]
    fn splits_only_whitelisted_merged_commands() {
        assert_eq!(intent("відкрістім"), Some("open_app"));
        assert_eq!(intent("відкрийютуб"), Some("open_url"));
        assert_eq!(intent("запустидискорд"), Some("open_app"));
        assert_eq!(intent("відкрийвізуалстудіокод"), Some("open_app"));
        assert_eq!(intent("відкрийбанкінг"), None);
        assert_eq!(intent("видалифайл"), None);
    }

    #[test]
    fn fast_acknowledgements_are_short_outcomes() {
        for text in [
            "відкрай ютуб",
            "відкрей visual studio code",
            "зроби тихіше",
            "вимкни звук",
        ] {
            let command = match_fast_command(text).expect("expected fast command");
            assert!(command.acknowledgement.split_whitespace().count() <= 10);
            assert!(!command.acknowledgement.contains("Виконую"));
            assert!(command.acknowledgement_cache.is_some());
        }
    }
}
