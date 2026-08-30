use std::process::Command;

pub fn open_url(url: &str) -> Result<String, String> {
    let url = url.trim();
    if !(url.starts_with("https://") || url.starts_with("http://")) {
        return Err("Дозволені лише адреси, що починаються з https:// або http://".to_owned());
    }
    if url.chars().any(char::is_control) {
        return Err("Адреса містить недопустимі символи.".to_owned());
    }

    Command::new("explorer.exe")
        .arg(url)
        .spawn()
        .map(|_| format!("Відкрито {url}"))
        .map_err(|error| format!("Не вдалося відкрити адресу: {error}"))
}
