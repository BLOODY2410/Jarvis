use std::{
    collections::HashSet,
    env,
    ffi::OsStr,
    fs,
    os::windows::ffi::OsStrExt,
    path::{Path, PathBuf},
    process::Command,
    sync::{Arc, OnceLock, RwLock},
    thread,
    time::Duration,
};

use serde_json::json;
use windows::{
    Win32::{
        Foundation::{HWND, LPARAM, WPARAM},
        System::Threading::{
            OpenProcess, PROCESS_NAME_WIN32, PROCESS_QUERY_LIMITED_INFORMATION,
            QueryFullProcessImageNameW,
        },
        UI::{
            Shell::ShellExecuteW,
            WindowsAndMessaging::{
                EnumWindows, GetWindowTextLengthW, GetWindowTextW, GetWindowThreadProcessId,
                IsWindowVisible, PostMessageW, SW_SHOWNORMAL, WM_CLOSE,
            },
        },
    },
    core::{BOOL, PCWSTR, PWSTR},
};

#[derive(Clone, Debug)]
struct AppEntry {
    name: String,
    path: PathBuf,
}

#[derive(Clone)]
struct AppIndex(Arc<RwLock<Vec<AppEntry>>>);

impl AppIndex {
    fn global() -> &'static Self {
        static INDEX: OnceLock<AppIndex> = OnceLock::new();
        INDEX.get_or_init(|| {
            let index = Self(Arc::new(RwLock::new(scan_apps())));
            let entries = Arc::clone(&index.0);
            thread::spawn(move || {
                loop {
                    thread::sleep(Duration::from_secs(600));
                    if let Ok(mut current) = entries.write() {
                        *current = scan_apps();
                    }
                }
            });
            index
        })
    }

    fn find(&self, query: &str) -> Option<AppEntry> {
        let query = normalize(query);
        self.0
            .read()
            .ok()?
            .iter()
            .filter_map(|entry| {
                let name = normalize(&entry.name);
                let score = if name == query {
                    0
                } else if name.starts_with(&query) {
                    1
                } else if name.contains(&query) {
                    2
                } else {
                    return None;
                };
                Some((score, name.len(), entry.clone()))
            })
            .min_by_key(|(score, length, _)| (*score, *length))
            .map(|(_, _, entry)| entry)
    }
}

pub fn warm_index() {
    let _ = AppIndex::global();
}

pub fn open_app(app: &str) -> Result<String, String> {
    if let Some((executable, display_name)) = builtin_app(app) {
        return Command::new(executable)
            .spawn()
            .map(|_| format!("{display_name} відкрито."))
            .map_err(|error| format!("Не вдалося відкрити {display_name}: {error}"));
    }
    if let Some(entry) = AppIndex::global().find(app) {
        shell_open(&entry.path)?;
        return Ok(format!("{} відкрито.", entry.name));
    }
    let executable = if app.to_ascii_lowercase().ends_with(".exe") {
        app.to_owned()
    } else {
        format!("{app}.exe")
    };
    Command::new(&executable)
        .spawn()
        .map(|_| format!("{} відкрито.", app.trim()))
        .map_err(|_| format!("Програму '{}' не знайдено в AppIndex або PATH.", app.trim()))
}

pub fn close_app(app: &str) -> Result<String, String> {
    let normalized = normalize(app.trim().trim_end_matches(".exe"));
    if is_protected(&normalized) {
        return Err(format!("Процес '{normalized}' захищено від закриття."));
    }
    let matches: Vec<_> = visible_windows()
        .into_iter()
        .filter(|window| {
            normalize(&window.process) == normalized
                || normalize(&window.title).contains(&normalized)
        })
        .collect();
    if matches.is_empty() {
        return Err(format!("Запущену програму '{}' не знайдено.", app.trim()));
    }
    let mut names = HashSet::new();
    for window in &matches {
        unsafe {
            PostMessageW(Some(window.hwnd), WM_CLOSE, WPARAM(0), LPARAM(0))
                .map_err(|error| format!("Не вдалося закрити вікно: {error}"))?;
        }
        names.insert(window.process.clone());
    }
    let mut names: Vec<_> = names.into_iter().collect();
    names.sort();
    Ok(format!("Надіслано команду закриття: {}.", names.join(", ")))
}

pub fn get_running_apps() -> Result<String, String> {
    let apps: Vec<_> = visible_windows()
        .into_iter()
        .map(|window| json!({"name": window.process, "title": window.title, "pid": window.pid}))
        .collect();
    serde_json::to_string(&apps)
        .map_err(|error| format!("Не вдалося серіалізувати список програм: {error}"))
}

#[derive(Debug)]
struct VisibleWindow {
    hwnd: HWND,
    pid: u32,
    process: String,
    title: String,
}

fn visible_windows() -> Vec<VisibleWindow> {
    unsafe extern "system" fn callback(hwnd: HWND, lparam: LPARAM) -> BOOL {
        unsafe {
            if !IsWindowVisible(hwnd).as_bool() {
                return true.into();
            }
            let length = GetWindowTextLengthW(hwnd);
            if length <= 0 {
                return true.into();
            }
            let mut title = vec![0u16; length as usize + 1];
            let copied = GetWindowTextW(hwnd, &mut title);
            if copied <= 0 {
                return true.into();
            }
            title.truncate(copied as usize);
            let mut pid = 0;
            GetWindowThreadProcessId(hwnd, Some(&mut pid));
            let process = process_name(pid).unwrap_or_else(|| format!("pid-{pid}"));
            let windows = &mut *(lparam.0 as *mut Vec<VisibleWindow>);
            windows.push(VisibleWindow {
                hwnd,
                pid,
                process,
                title: String::from_utf16_lossy(&title),
            });
            true.into()
        }
    }
    let mut windows = Vec::new();
    unsafe {
        let _ = EnumWindows(
            Some(callback),
            LPARAM((&mut windows as *mut Vec<VisibleWindow>) as isize),
        );
    }
    windows
}

fn process_name(pid: u32) -> Option<String> {
    unsafe {
        let process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid).ok()?;
        let mut buffer = vec![0u16; 32_768];
        let mut size = buffer.len() as u32;
        let result = QueryFullProcessImageNameW(
            process,
            PROCESS_NAME_WIN32,
            PWSTR(buffer.as_mut_ptr()),
            &mut size,
        );
        let _ = windows::Win32::Foundation::CloseHandle(process);
        result.ok()?;
        PathBuf::from(String::from_utf16_lossy(&buffer[..size as usize]))
            .file_stem()
            .map(|name| name.to_string_lossy().into_owned())
    }
}

fn shell_open(path: &Path) -> Result<(), String> {
    let operation = wide("open");
    let file = wide(path.as_os_str());
    let result = unsafe {
        ShellExecuteW(
            None,
            PCWSTR(operation.as_ptr()),
            PCWSTR(file.as_ptr()),
            PCWSTR::null(),
            PCWSTR::null(),
            SW_SHOWNORMAL,
        )
    };
    if result.0 as isize <= 32 {
        Err(format!(
            "Windows Shell не зміг відкрити '{}'.",
            path.display()
        ))
    } else {
        Ok(())
    }
}

fn scan_apps() -> Vec<AppEntry> {
    let mut roots = Vec::new();
    if let Some(value) = env::var_os("APPDATA") {
        roots.push(PathBuf::from(value).join("Microsoft/Windows/Start Menu/Programs"));
    }
    if let Some(value) = env::var_os("ProgramData") {
        roots.push(PathBuf::from(value).join("Microsoft/Windows/Start Menu/Programs"));
    }
    if let Some(value) = env::var_os("USERPROFILE") {
        roots.push(PathBuf::from(value).join("Desktop"));
    }
    if let Some(value) = env::var_os("PUBLIC") {
        roots.push(PathBuf::from(value).join("Desktop"));
    }
    let mut entries = Vec::new();
    for root in roots {
        collect_shortcuts(&root, &mut entries, 0);
    }
    entries
}

fn collect_shortcuts(directory: &Path, output: &mut Vec<AppEntry>, depth: usize) {
    if depth > 8 {
        return;
    }
    let Ok(children) = fs::read_dir(directory) else {
        return;
    };
    for child in children.flatten() {
        let path = child.path();
        if path.is_dir() {
            collect_shortcuts(&path, output, depth + 1);
        } else if path
            .extension()
            .is_some_and(|ext| ext.eq_ignore_ascii_case("lnk"))
        {
            if let Some(name) = path
                .file_stem()
                .map(|value| value.to_string_lossy().into_owned())
            {
                output.push(AppEntry { name, path });
            }
        }
    }
}

fn wide(value: impl AsRef<OsStr>) -> Vec<u16> {
    value.as_ref().encode_wide().chain(Some(0)).collect()
}
fn normalize(value: &str) -> String {
    value.trim().to_lowercase().replace(['-', '_'], " ")
}
fn is_protected(normalized: &str) -> bool {
    [
        "system",
        "registry",
        "idle",
        "csrss",
        "wininit",
        "winlogon",
        "services",
        "lsass",
        "smss",
        "dwm",
        "explorer",
        "провідник",
        "проводник",
        "jarvis",
        "powershell",
    ]
    .contains(&normalized)
}

fn builtin_app(app: &str) -> Option<(&'static str, &'static str)> {
    match app.trim().to_lowercase().as_str() {
        "calculator" | "калькулятор" | "calc" => Some(("calc.exe", "Калькулятор")),
        "notepad" | "блокнот" => Some(("notepad.exe", "Блокнот")),
        "paint" | "паінт" | "пейнт" => Some(("mspaint.exe", "Paint")),
        "explorer" | "провідник" | "проводник" => {
            Some(("explorer.exe", "Провідник"))
        }
        "cmd" | "командний рядок" => Some(("cmd.exe", "Командний рядок")),
        "task manager" | "диспетчер завдань" | "диспетчер задач" => {
            Some(("taskmgr.exe", "Диспетчер завдань"))
        }
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use std::time::Instant;

    use super::{get_running_apps, is_protected, normalize};
    #[test]
    fn protects_critical_processes() {
        for name in ["system", "lsass", "WINLOGON", "explorer", "jarvis"] {
            assert!(is_protected(&normalize(name)));
        }
        assert!(!is_protected(&normalize("notepad")));
    }

    #[test]
    #[ignore = "manual latency benchmark; read-only Windows API"]
    fn benchmark_native_running_apps() {
        let mut samples = Vec::new();
        for _ in 0..20 {
            let started = Instant::now();
            get_running_apps().expect("native window enumeration failed");
            samples.push(started.elapsed().as_micros() as u64);
        }
        samples.sort_unstable();
        println!(
            "native_running_apps median={}us p95={}us",
            samples[samples.len() / 2],
            samples[18]
        );
    }
}
