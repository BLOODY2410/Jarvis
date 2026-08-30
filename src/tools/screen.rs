use std::{
    env, fs,
    path::PathBuf,
    time::{SystemTime, UNIX_EPOCH},
};

use super::powershell;

pub fn take_screenshot(output_path: Option<&str>) -> Result<String, String> {
    let path = match output_path {
        Some(path) => PathBuf::from(path.trim().trim_matches('"')),
        None => {
            let home = env::var_os("USERPROFILE")
                .map(PathBuf::from)
                .ok_or_else(|| "Не вдалося визначити папку користувача.".to_owned())?;
            let timestamp = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map_err(|error| format!("Помилка системного часу: {error}"))?
                .as_secs();
            home.join("Pictures")
                .join("Jarvis")
                .join(format!("screenshot-{timestamp}.png"))
        }
    };

    if !path
        .extension()
        .is_some_and(|extension| extension.eq_ignore_ascii_case("png"))
    {
        return Err("Знімок екрана потрібно зберігати у файлі .png".to_owned());
    }
    if path.exists() {
        return Err(format!("Файл '{}' уже існує.", path.display()));
    }
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("Не вдалося створити папку для знімка: {error}"))?;
    }

    let path_text = path.to_string_lossy().into_owned();
    let script = r#"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$bounds = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bitmap = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
try {
    $graphics.CopyFromScreen($bounds.Left, $bounds.Top, 0, 0, $bitmap.Size)
    $bitmap.Save($env:JARVIS_SCREENSHOT_PATH, [System.Drawing.Imaging.ImageFormat]::Png)
} finally {
    $graphics.Dispose()
    $bitmap.Dispose()
}
Write-Output $env:JARVIS_SCREENSHOT_PATH
"#;

    powershell(script, &[("JARVIS_SCREENSHOT_PATH", &path_text)])
        .map(|_| format!("Знімок екрана збережено: '{}'.", path.display()))
}
