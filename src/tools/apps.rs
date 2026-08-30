use std::process::Command;

use super::powershell;

pub fn open_app(app: &str) -> Result<String, String> {
    if let Some((executable, display_name)) = builtin_app(app) {
        return Command::new(executable)
            .spawn()
            .map(|_| format!("{display_name} відкрито."))
            .map_err(|error| format!("Не вдалося відкрити {display_name}: {error}"));
    }

    let script = r#"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$query = $env:JARVIS_APP_QUERY
if ([string]::IsNullOrWhiteSpace($query)) { throw 'Назва програми порожня.' }

$shortcutRoots = @(
    "$env:APPDATA\Microsoft\Windows\Start Menu\Programs",
    "$env:ProgramData\Microsoft\Windows\Start Menu\Programs",
    [Environment]::GetFolderPath('Desktop'),
    "$env:PUBLIC\Desktop"
)

foreach ($root in $shortcutRoots) {
    if (-not (Test-Path -LiteralPath $root)) { continue }
    $shortcut = Get-ChildItem -LiteralPath $root -Filter '*.lnk' -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.BaseName -like "*$query*" } |
        Select-Object -First 1
    if ($shortcut) {
        Start-Process -FilePath $shortcut.FullName
        Write-Output $shortcut.BaseName
        exit 0
    }
}

$startApp = Get-StartApps -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like "*$query*" } |
    Select-Object -First 1
if ($startApp) {
    Start-Process explorer.exe -ArgumentList "shell:AppsFolder\$($startApp.AppID)"
    Write-Output $startApp.Name
    exit 0
}

$exeName = if ($query.EndsWith('.exe')) { $query } else { "$query.exe" }
$command = Get-Command $exeName -ErrorAction SilentlyContinue
if ($command) {
    Start-Process -FilePath $command.Source
    Write-Output $query
    exit 0
}

throw "Програму '$query' не знайдено."
"#;

    powershell(script, &[("JARVIS_APP_QUERY", app)])
        .map(|name| format!("{} відкрито.", name.trim()))
}

pub fn close_app(app: &str) -> Result<String, String> {
    let normalized = app.trim().trim_end_matches(".exe");
    let protected = [
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
    ];
    if protected
        .iter()
        .any(|name| normalized.eq_ignore_ascii_case(name))
    {
        return Err(format!("Процес '{normalized}' захищено від закриття."));
    }

    let script = r#"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$query = $env:JARVIS_APP_QUERY
$processes = Get-Process -ErrorAction SilentlyContinue | Where-Object {
    $_.ProcessName -ieq $query -or
    (-not [string]::IsNullOrWhiteSpace($_.MainWindowTitle) -and $_.MainWindowTitle -like "*$query*")
}
if (-not $processes) { throw "Запущену програму '$query' не знайдено." }

$names = @($processes | Select-Object -ExpandProperty ProcessName -Unique)
$processes | Stop-Process -ErrorAction Stop
Write-Output ($names -join ', ')
"#;

    powershell(script, &[("JARVIS_APP_QUERY", normalized)])
        .map(|names| format!("Закрито: {names}."))
}

pub fn get_running_apps() -> Result<String, String> {
    let script = r#"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$apps = Get-Process -ErrorAction SilentlyContinue |
    Where-Object { -not [string]::IsNullOrWhiteSpace($_.MainWindowTitle) } |
    Sort-Object ProcessName |
    Select-Object @{Name='name';Expression={$_.ProcessName}},
                  @{Name='title';Expression={$_.MainWindowTitle}},
                  @{Name='pid';Expression={$_.Id}}
if ($apps) { $apps | ConvertTo-Json -Compress } else { '[]' }
"#;
    powershell(script, &[])
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
