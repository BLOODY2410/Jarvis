use std::{
    env, fs,
    net::TcpStream,
    path::{Path, PathBuf},
    process::Command,
    thread,
    time::Duration,
};

use serde::Deserialize;
use serde_json::{Value, json};
use tungstenite::{Message, WebSocket, connect, stream::MaybeTlsStream};
use url::Url;

const DEFAULT_CDP_PORT: u16 = 9223;
const MUSIC_URL: &str = "https://music.youtube.com/";

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct CdpTarget {
    url: String,
    web_socket_debugger_url: Option<String>,
}

pub fn open_youtube_music() -> Result<String, String> {
    let target = ensure_music_target(MUSIC_URL)?;
    if target.web_socket_debugger_url.is_none() {
        return Err("Керована вкладка YouTube Music не надала CDP-сеанс.".to_owned());
    }
    Ok("YouTube Music відкрито в керованому профілі.".to_owned())
}

pub fn play_youtube_music(query: Option<&str>) -> Result<String, String> {
    let target_url = query
        .filter(|value| !value.trim().is_empty())
        .map(|value| {
            let encoded: String =
                url::form_urlencoded::byte_serialize(value.trim().as_bytes()).collect();
            format!("{MUSIC_URL}search?q={encoded}")
        })
        .unwrap_or_else(|| MUSIC_URL.to_owned());
    let target = ensure_music_target(&target_url)?;
    let socket = target
        .web_socket_debugger_url
        .ok_or_else(|| "Керована вкладка YouTube Music недоступна.".to_owned())?;
    thread::sleep(Duration::from_millis(if query.is_some() {
        1800
    } else {
        900
    }));
    let login = evaluate(
        &socket,
        "location.hostname.includes('accounts.google') || document.body.innerText.includes('Sign in') || document.body.innerText.includes('Увійти')",
    )?;
    if login == Value::Bool(true) {
        return Err(
            "Потрібно один раз увійти в YouTube Music у керованому профілі JARVIS.".to_owned(),
        );
    }
    let script = if query.is_some() {
        r#"(() => {
          const item = document.querySelector('ytmusic-shelf-renderer ytmusic-responsive-list-item-renderer, ytmusic-search-page ytmusic-responsive-list-item-renderer');
          const play = item && (item.querySelector('ytmusic-play-button-renderer') || item.querySelector('a'));
          if (!play) return false; play.click(); return true;
        })()"#
    } else {
        r#"(() => {
          const video = document.querySelector('video');
          if (video && video.src) { video.play(); return true; }
          const play = document.querySelector('ytmusic-play-button-renderer, ytmusic-responsive-list-item-renderer a');
          if (!play) return false; play.click(); return true;
        })()"#
    };
    let action_triggered = evaluate(&socket, script)? == Value::Bool(true);
    thread::sleep(Duration::from_millis(600));
    let playback_confirmed = evaluate(
        &socket,
        "(() => { const v=document.querySelector('video'); return !!v && !v.paused && !!v.currentSrc; })()",
    )? == Value::Bool(true);
    if action_triggered && playback_confirmed {
        Ok(match query {
            Some(value) => format!("Відтворення «{}» запущено в YouTube Music.", value.trim()),
            None => "Музику запущено в YouTube Music.".to_owned(),
        })
    } else {
        Err("Не знайшов доступний трек для відтворення. Відкрийте керований профіль і перевірте вхід або згоду на cookies.".to_owned())
    }
}

pub fn media_control(action: &str) -> Result<String, String> {
    let target = find_music_target()?.ok_or_else(|| {
        "Керована сесія YouTube Music не запущена. Спочатку відкрийте її через JARVIS.".to_owned()
    })?;
    let socket = target
        .web_socket_debugger_url
        .ok_or_else(|| "Керована вкладка YouTube Music недоступна.".to_owned())?;
    let (script, success) = media_script(action)?;
    if evaluate(&socket, script)? == Value::Bool(true) {
        Ok(success.to_owned())
    } else {
        Err("Не вдалося підтвердити медіадію в керованій вкладці.".to_owned())
    }
}

fn media_script(action: &str) -> Result<(&'static str, &'static str), String> {
    match action {
        "pause" => Ok((
            r#"(() => { const v=document.querySelector('video'); if(!v||v.paused)return false; v.pause(); return v.paused; })()"#,
            "Відтворення призупинено.",
        )),
        "resume" => Ok((
            r#"(async () => { const v=document.querySelector('video'); if(!v||!v.paused)return false; try { await v.play(); return !v.paused; } catch (_) { return false; } })()"#,
            "Відтворення продовжено.",
        )),
        "next" => Ok((
            r#"(() => { const b=document.querySelector('.next-button, [aria-label*="Next"], [aria-label*="Наступ"]'); if(!b)return false; b.click(); return true; })()"#,
            "Увімкнено наступний трек.",
        )),
        "previous" => Ok((
            r#"(() => { const b=document.querySelector('.previous-button, [aria-label*="Previous"], [aria-label*="Поперед"]'); if(!b)return false; b.click(); return true; })()"#,
            "Увімкнено попередній трек.",
        )),
        _ => Err("Невідома медіакоманда.".to_owned()),
    }
}

fn ensure_music_target(url: &str) -> Result<CdpTarget, String> {
    ensure_browser()?;
    if let Some(target) = find_music_target()? {
        if target.url != url
            && let Some(socket) = &target.web_socket_debugger_url
        {
            let expression = format!("location.href={}", serde_json::to_string(url).unwrap());
            let _ = evaluate(socket, &expression)?;
        }
        return Ok(target);
    }
    let endpoint = format!("{}/json/new?{}", cdp_base(), url);
    http_client()
        .put(endpoint)
        .send()
        .and_then(|response| response.error_for_status())
        .map_err(|_| "Не вдалося створити керовану вкладку Chrome.".to_owned())?
        .json()
        .map_err(|_| "Chrome повернув некоректні дані CDP.".to_owned())
}

fn find_music_target() -> Result<Option<CdpTarget>, String> {
    let response = match http_client()
        .get(format!("{}/json/list", cdp_base()))
        .send()
    {
        Ok(response) => response,
        Err(_) => return Ok(None),
    };
    let targets: Vec<CdpTarget> = response
        .json()
        .map_err(|_| "Chrome повернув некоректний список вкладок.".to_owned())?;
    Ok(targets
        .into_iter()
        .find(|target| target.url.contains("music.youtube.com")))
}

fn ensure_browser() -> Result<(), String> {
    if http_client()
        .get(format!("{}/json/version", cdp_base()))
        .send()
        .is_ok()
    {
        return Ok(());
    }
    let chrome = find_chrome().ok_or_else(|| "Chrome або Chromium не знайдено.".to_owned())?;
    let profile = managed_profile();
    fs::create_dir_all(&profile)
        .map_err(|_| "Не вдалося створити профіль браузера JARVIS.".to_owned())?;
    Command::new(chrome)
        .args([
            format!("--remote-debugging-port={}", cdp_port()),
            format!("--user-data-dir={}", profile.display()),
            "--no-first-run".to_owned(),
            "--no-default-browser-check".to_owned(),
            "--remote-allow-origins=*".to_owned(),
            MUSIC_URL.to_owned(),
        ])
        .spawn()
        .map_err(|_| "Не вдалося запустити керований Chrome.".to_owned())?;
    for _ in 0..30 {
        thread::sleep(Duration::from_millis(200));
        if http_client()
            .get(format!("{}/json/version", cdp_base()))
            .send()
            .is_ok()
        {
            return Ok(());
        }
    }
    Err("Керований Chrome не відкрив CDP-порт.".to_owned())
}

fn evaluate(socket_url: &str, expression: &str) -> Result<Value, String> {
    let url = Url::parse(socket_url).map_err(|_| "Некоректна CDP-адреса.".to_owned())?;
    let (mut socket, _) = connect(url.as_str())
        .map_err(|_| "Не вдалося підключитися до керованої вкладки.".to_owned())?;
    socket
        .send(Message::Text(json!({"id": 1, "method": "Runtime.evaluate", "params": {"expression": expression, "returnByValue": true, "awaitPromise": true}}).to_string().into()))
        .map_err(|_| "Не вдалося надіслати CDP-команду.".to_owned())?;
    read_evaluation(&mut socket)
}

fn read_evaluation(socket: &mut WebSocket<MaybeTlsStream<TcpStream>>) -> Result<Value, String> {
    for _ in 0..20 {
        let message = socket
            .read()
            .map_err(|_| "CDP-сеанс перервано.".to_owned())?;
        if let Message::Text(text) = message {
            let payload: Value =
                serde_json::from_str(&text).map_err(|_| "Некоректна CDP-відповідь.".to_owned())?;
            if payload.get("id") == Some(&Value::from(1)) {
                return Ok(payload
                    .pointer("/result/result/value")
                    .cloned()
                    .unwrap_or(Value::Null));
            }
        }
    }
    Err("CDP не підтвердив дію.".to_owned())
}

fn http_client() -> reqwest::blocking::Client {
    reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(3))
        .build()
        .expect("client")
}

fn cdp_port() -> u16 {
    env::var("JARVIS_BROWSER_CDP_PORT")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(DEFAULT_CDP_PORT)
}

fn cdp_base() -> String {
    format!("http://127.0.0.1:{}", cdp_port())
}

fn managed_profile() -> PathBuf {
    env::var_os("JARVIS_BROWSER_PROFILE")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(".jarvis-browser-profile"))
}

fn find_chrome() -> Option<PathBuf> {
    if let Some(path) = env::var_os("JARVIS_BROWSER_EXECUTABLE")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        && path.is_file()
    {
        return Some(path);
    }
    [
        env::var_os("PROGRAMFILES"),
        env::var_os("PROGRAMFILES(X86)"),
        env::var_os("LOCALAPPDATA"),
    ]
    .into_iter()
    .flatten()
    .map(PathBuf::from)
    .flat_map(|root| {
        [
            root.join("Google/Chrome/Application/chrome.exe"),
            root.join("Microsoft/Edge/Application/msedge.exe"),
        ]
    })
    .find(|path| Path::new(path).is_file())
}

#[cfg(test)]
mod tests {
    use super::media_script;

    #[test]
    fn youtube_music_controls_require_confirmed_dom_actions() {
        for action in ["pause", "resume", "next", "previous"] {
            let (script, success) = media_script(action).unwrap();
            assert!(
                script.contains("return true")
                    || script.contains("return v.paused")
                    || script.contains("return !v.paused")
            );
            assert!(!success.is_empty());
        }
        assert!(media_script("delete").is_err());
    }
}
