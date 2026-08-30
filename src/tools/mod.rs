mod apps;
mod browser;
mod files;
mod screen;
mod system;

use std::{collections::HashMap, process::Command};

use serde_json::{Value, json};

#[derive(Clone)]
pub struct ToolRegistry {
    schemas: Vec<Value>,
}

impl ToolRegistry {
    pub fn new() -> Self {
        Self {
            schemas: vec![
                tool(
                    "open_app",
                    "Знайти й відкрити встановлену програму Windows.",
                    json!({
                        "app": {"type": "string", "description": "Назва програми, наприклад Steam або Блокнот."}
                    }),
                    &["app"],
                ),
                tool(
                    "close_app",
                    "Закрити запущену програму за назвою процесу або вікна.",
                    json!({
                        "app": {"type": "string", "description": "Назва програми або процесу без .exe."}
                    }),
                    &["app"],
                ),
                tool(
                    "get_running_apps",
                    "Отримати список запущених програм із видимими вікнами.",
                    json!({}),
                    &[],
                ),
                tool(
                    "open_url",
                    "Відкрити HTTP або HTTPS адресу у стандартному браузері.",
                    json!({
                        "url": {"type": "string", "description": "Повна вебадреса з https:// або http://."}
                    }),
                    &["url"],
                ),
                tool(
                    "set_volume",
                    "Встановити системну гучність Windows.",
                    json!({
                        "level": {"type": "integer", "minimum": 0, "maximum": 100, "description": "Рівень гучності у відсотках."}
                    }),
                    &["level"],
                ),
                tool("mute", "Вимкнути системний звук.", json!({}), &[]),
                tool("unmute", "Увімкнути системний звук.", json!({}), &[]),
                tool(
                    "get_system_info",
                    "Отримати основну інформацію про Windows, процесор, пам'ять і диски.",
                    json!({}),
                    &[],
                ),
                tool(
                    "take_screenshot",
                    "Зберегти знімок усіх екранів у PNG. Це лише знімок, без AI-аналізу.",
                    json!({
                        "output_path": {"type": "string", "description": "Необов'язковий повний шлях до PNG-файла."}
                    }),
                    &[],
                ),
                tool(
                    "list_files",
                    "Показати файли та папки безпосередньо в заданій папці.",
                    json!({
                        "path": {"type": "string", "description": "Повний або відносний шлях до папки."}
                    }),
                    &["path"],
                ),
                tool(
                    "find_files",
                    "Знайти файли за частиною назви всередині папки.",
                    json!({
                        "directory": {"type": "string", "description": "Папка, у якій шукати."},
                        "query": {"type": "string", "description": "Частина назви файла або папки."}
                    }),
                    &["directory", "query"],
                ),
                tool(
                    "open_file",
                    "Відкрити файл або папку у стандартній програмі Windows.",
                    json!({
                        "path": {"type": "string", "description": "Шлях до наявного файла або папки."}
                    }),
                    &["path"],
                ),
                tool(
                    "create_directory",
                    "Створити нову папку.",
                    json!({
                        "path": {"type": "string", "description": "Шлях нової папки."}
                    }),
                    &["path"],
                ),
                tool(
                    "copy_file",
                    "Скопіювати файл. Наявний файл призначення не перезаписується без явного дозволу.",
                    json!({
                        "source": {"type": "string"},
                        "destination": {"type": "string"},
                        "overwrite": {"type": "boolean", "default": false}
                    }),
                    &["source", "destination"],
                ),
                tool(
                    "move_file",
                    "Перемістити або перейменувати файл. Наявний файл призначення не перезаписується без явного дозволу.",
                    json!({
                        "source": {"type": "string"},
                        "destination": {"type": "string"},
                        "overwrite": {"type": "boolean", "default": false}
                    }),
                    &["source", "destination"],
                ),
            ],
        }
    }

    pub fn schemas(&self) -> &[Value] {
        &self.schemas
    }

    pub fn execute(&self, name: &str, raw_arguments: &str) -> String {
        let args: Value = match serde_json::from_str(raw_arguments) {
            Ok(value) => value,
            Err(error) => return failure(format!("Некоректні аргументи: {error}")),
        };

        let result = match name {
            "open_app" => required_str(&args, "app").and_then(apps::open_app),
            "close_app" => required_str(&args, "app").and_then(apps::close_app),
            "get_running_apps" => apps::get_running_apps(),
            "open_url" => required_str(&args, "url").and_then(browser::open_url),
            "set_volume" => required_u64(&args, "level")
                .and_then(|level| system::set_volume(level.min(100) as u8)),
            "mute" => system::set_muted(true),
            "unmute" => system::set_muted(false),
            "volume_up" => system::adjust_volume(10),
            "volume_down" => system::adjust_volume(-10),
            "get_system_info" => system::get_system_info(),
            "take_screenshot" => screen::take_screenshot(optional_str(&args, "output_path")),
            "list_files" => required_str(&args, "path").and_then(files::list_files),
            "find_files" => required_str(&args, "directory").and_then(|directory| {
                required_str(&args, "query").and_then(|query| files::find_files(directory, query))
            }),
            "open_file" => required_str(&args, "path").and_then(files::open_file),
            "create_directory" => required_str(&args, "path").and_then(files::create_directory),
            "copy_file" => {
                file_transfer_args(&args).and_then(|(source, destination, overwrite)| {
                    files::copy_file(source, destination, overwrite)
                })
            }
            "move_file" => {
                file_transfer_args(&args).and_then(|(source, destination, overwrite)| {
                    files::move_file(source, destination, overwrite)
                })
            }
            _ => Err(format!("Невідомий інструмент: {name}")),
        };

        match result {
            Ok(message) => success(message),
            Err(error) => failure(error),
        }
    }
}

fn tool(name: &str, description: &str, properties: Value, required: &[&str]) -> Value {
    json!({
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": false
            }
        }
    })
}

fn required_str<'a>(args: &'a Value, name: &str) -> Result<&'a str, String> {
    args.get(name)
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| format!("Відсутній текстовий аргумент '{name}'"))
}

fn optional_str<'a>(args: &'a Value, name: &str) -> Option<&'a str> {
    args.get(name)
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty())
}

fn required_u64(args: &Value, name: &str) -> Result<u64, String> {
    args.get(name)
        .and_then(Value::as_u64)
        .ok_or_else(|| format!("Відсутній числовий аргумент '{name}'"))
}

fn file_transfer_args(args: &Value) -> Result<(&str, &str, bool), String> {
    Ok((
        required_str(args, "source")?,
        required_str(args, "destination")?,
        args.get("overwrite")
            .and_then(Value::as_bool)
            .unwrap_or(false),
    ))
}

fn success(message: String) -> String {
    json!({"success": true, "result": message}).to_string()
}

fn failure(message: String) -> String {
    json!({"success": false, "error": message}).to_string()
}

pub(crate) fn powershell(script: &str, environment: &[(&str, &str)]) -> Result<String, String> {
    let mut command = Command::new("powershell.exe");
    command.args([
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
    ]);

    let environment: HashMap<_, _> = environment.iter().copied().collect();
    command.envs(environment);

    let output = command
        .output()
        .map_err(|error| format!("Не вдалося запустити PowerShell: {error}"))?;

    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_owned();
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_owned();

    if output.status.success() {
        Ok(stdout)
    } else if stderr.is_empty() {
        Err(if stdout.is_empty() {
            "Команда Windows завершилася з помилкою".to_owned()
        } else {
            stdout
        })
    } else {
        Err(stderr)
    }
}

#[cfg(test)]
mod tests {
    use super::ToolRegistry;

    #[test]
    fn registry_contains_unique_tool_names() {
        let registry = ToolRegistry::new();
        let mut names: Vec<_> = registry
            .schemas()
            .iter()
            .filter_map(|schema| {
                schema
                    .pointer("/function/name")
                    .and_then(|value| value.as_str())
            })
            .collect();
        let original_len = names.len();
        names.sort_unstable();
        names.dedup();
        assert_eq!(names.len(), original_len);
        assert_eq!(original_len, 15);
    }

    #[test]
    fn rejects_non_http_urls() {
        let registry = ToolRegistry::new();
        let result = registry.execute("open_url", r#"{"url":"file:///C:/Windows"}"#);
        assert!(result.contains(r#""success":false"#));
    }
}
