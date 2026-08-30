use std::{
    env, fs,
    path::{Path, PathBuf},
};

use serde_json::json;

pub fn list_files(path: &str) -> Result<String, String> {
    let path = expand_path(path);
    if !path.is_dir() {
        return Err(format!("Папку '{}' не знайдено.", path.display()));
    }

    let mut entries = fs::read_dir(&path)
        .map_err(|error| format!("Не вдалося прочитати папку: {error}"))?
        .filter_map(Result::ok)
        .map(|entry| {
            let file_type = entry.file_type().ok();
            json!({
                "name": entry.file_name().to_string_lossy(),
                "path": entry.path().to_string_lossy(),
                "type": if file_type.as_ref().is_some_and(|kind| kind.is_dir()) { "directory" } else { "file" }
            })
        })
        .take(200)
        .collect::<Vec<_>>();

    entries.sort_by(|left, right| left["name"].as_str().cmp(&right["name"].as_str()));
    Ok(json!({"directory": path.to_string_lossy(), "entries": entries}).to_string())
}

pub fn find_files(directory: &str, query: &str) -> Result<String, String> {
    let root = expand_path(directory);
    if !root.is_dir() {
        return Err(format!("Папку '{}' не знайдено.", root.display()));
    }
    let query = query.to_lowercase();
    if query.trim().is_empty() {
        return Err("Пошуковий запит порожній.".to_owned());
    }

    let mut pending = vec![root.clone()];
    let mut matches = Vec::new();
    let mut scanned_directories = 0_u32;
    while let Some(current) = pending.pop() {
        scanned_directories += 1;
        if scanned_directories > 5_000 {
            return Ok(json!({
                "matches": matches,
                "truncated": true,
                "reason": "Досягнуто ліміт у 5000 перевірених папок"
            })
            .to_string());
        }
        let Ok(entries) = fs::read_dir(current) else {
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            let Ok(file_type) = entry.file_type() else {
                continue;
            };
            if entry
                .file_name()
                .to_string_lossy()
                .to_lowercase()
                .contains(&query)
            {
                matches.push(json!({
                    "path": path.to_string_lossy(),
                    "type": if file_type.is_dir() { "directory" } else { "file" }
                }));
                if matches.len() >= 100 {
                    return Ok(json!({"matches": matches, "truncated": true}).to_string());
                }
            }
            if file_type.is_dir() && !file_type.is_symlink() {
                pending.push(path);
            }
        }
    }

    Ok(json!({"matches": matches, "truncated": false}).to_string())
}

pub fn open_file(path: &str) -> Result<String, String> {
    let path = expand_path(path);
    if !path.exists() {
        return Err(format!("Шлях '{}' не знайдено.", path.display()));
    }

    std::process::Command::new("explorer.exe")
        .arg(&path)
        .spawn()
        .map(|_| format!("Відкрито '{}'.", path.display()))
        .map_err(|error| format!("Не вдалося відкрити '{}': {error}", path.display()))
}

pub fn create_directory(path: &str) -> Result<String, String> {
    let path = expand_path(path);
    if path.exists() {
        return Err(format!("Шлях '{}' уже існує.", path.display()));
    }
    fs::create_dir_all(&path)
        .map(|_| format!("Папку '{}' створено.", path.display()))
        .map_err(|error| format!("Не вдалося створити папку: {error}"))
}

pub fn copy_file(source: &str, destination: &str, overwrite: bool) -> Result<String, String> {
    let source = validate_source(source)?;
    let destination = destination_path(&source, destination);
    prepare_destination(&destination, overwrite)?;

    fs::copy(&source, &destination)
        .map(|_| {
            format!(
                "Файл '{}' скопійовано до '{}'.",
                source.display(),
                destination.display()
            )
        })
        .map_err(|error| format!("Не вдалося скопіювати файл: {error}"))
}

pub fn move_file(source: &str, destination: &str, overwrite: bool) -> Result<String, String> {
    let source = validate_source(source)?;
    let destination = destination_path(&source, destination);
    prepare_destination(&destination, overwrite)?;

    if !destination.exists() && fs::rename(&source, &destination).is_ok() {
        return Ok(format!(
            "Файл '{}' переміщено до '{}'.",
            source.display(),
            destination.display()
        ));
    }

    fs::copy(&source, &destination)
        .map_err(|error| format!("Не вдалося скопіювати файл під час переміщення: {error}"))?;
    fs::remove_file(&source).map_err(|error| {
        format!(
            "Копію створено в '{}', але не вдалося видалити початковий файл: {error}",
            destination.display()
        )
    })?;

    Ok(format!(
        "Файл '{}' переміщено до '{}'.",
        source.display(),
        destination.display()
    ))
}

fn validate_source(path: &str) -> Result<PathBuf, String> {
    let path = expand_path(path);
    if !path.is_file() {
        Err(format!("Файл '{}' не знайдено.", path.display()))
    } else {
        Ok(path)
    }
}

fn destination_path(source: &Path, destination: &str) -> PathBuf {
    let destination = expand_path(destination);
    if destination.is_dir() {
        destination.join(source.file_name().unwrap_or_default())
    } else {
        destination
    }
}

fn prepare_destination(destination: &Path, overwrite: bool) -> Result<(), String> {
    if destination.exists() {
        if !overwrite {
            return Err(format!(
                "Файл '{}' уже існує. Для перезапису потрібен явний дозвіл.",
                destination.display()
            ));
        }
        if !destination.is_file() {
            return Err("Шлях призначення не є файлом.".to_owned());
        }
    }
    if let Some(parent) = destination
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        fs::create_dir_all(parent)
            .map_err(|error| format!("Не вдалося створити папку призначення: {error}"))?;
    }
    Ok(())
}

fn expand_path(path: &str) -> PathBuf {
    let trimmed = path.trim().trim_matches('"');
    if trimmed == "~" {
        return env::var_os("USERPROFILE")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from(trimmed));
    }
    if let Some(rest) = trimmed
        .strip_prefix("~\\")
        .or_else(|| trimmed.strip_prefix("~/"))
        && let Some(home) = env::var_os("USERPROFILE")
    {
        return PathBuf::from(home).join(rest);
    }
    PathBuf::from(trimmed)
}

#[cfg(test)]
mod tests {
    use std::{fs, path::PathBuf, time::SystemTime};

    use super::{copy_file, move_file};

    #[test]
    fn copies_and_moves_without_implicit_overwrite() {
        let suffix = SystemTime::now()
            .duration_since(SystemTime::UNIX_EPOCH)
            .expect("valid system time")
            .as_nanos();
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("target")
            .join(format!("jarvis-file-test-{}-{suffix}", std::process::id()));
        fs::create_dir_all(&root).expect("create test directory");

        let source = root.join("source.txt");
        let copy = root.join("copy.txt");
        let moved = root.join("moved.txt");
        fs::write(&source, "Jarvis").expect("write source file");

        copy_file(
            source.to_string_lossy().as_ref(),
            copy.to_string_lossy().as_ref(),
            false,
        )
        .expect("copy file");
        assert_eq!(fs::read_to_string(&copy).unwrap(), "Jarvis");
        assert!(
            copy_file(
                source.to_string_lossy().as_ref(),
                copy.to_string_lossy().as_ref(),
                false,
            )
            .is_err()
        );

        move_file(
            copy.to_string_lossy().as_ref(),
            moved.to_string_lossy().as_ref(),
            false,
        )
        .expect("move file");
        assert!(!copy.exists());
        assert_eq!(fs::read_to_string(&moved).unwrap(), "Jarvis");

        fs::remove_dir_all(&root).expect("remove isolated test directory");
    }
}
