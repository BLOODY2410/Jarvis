use super::powershell;
use windows::{
    Win32::{
        Media::Audio::Endpoints::IAudioEndpointVolume,
        Media::Audio::{IMMDeviceEnumerator, MMDeviceEnumerator, eMultimedia, eRender},
        System::Com::{CLSCTX_ALL, COINIT_MULTITHREADED, CoCreateInstance, CoInitializeEx},
    },
    core::GUID,
};

fn with_endpoint<T>(
    operation: impl FnOnce(&IAudioEndpointVolume) -> windows::core::Result<T>,
) -> Result<T, String> {
    // COM may already be initialized with another apartment model. In that case
    // CoCreateInstance is still valid on the current thread, so only hard failures
    // from the actual endpoint operation are surfaced.
    unsafe {
        let _ = CoInitializeEx(None, COINIT_MULTITHREADED);
        let enumerator: IMMDeviceEnumerator =
            CoCreateInstance(&MMDeviceEnumerator, None, CLSCTX_ALL)
                .map_err(|error| format!("CoreAudio enumerator недоступний: {error}"))?;
        let device = enumerator
            .GetDefaultAudioEndpoint(eRender, eMultimedia)
            .map_err(|error| format!("Аудіовихід Windows не знайдено: {error}"))?;
        let endpoint: IAudioEndpointVolume = device
            .Activate(CLSCTX_ALL, None)
            .map_err(|error| format!("CoreAudio endpoint недоступний: {error}"))?;
        operation(&endpoint).map_err(|error| format!("CoreAudio operation failed: {error}"))
    }
}

pub fn set_volume(level: u8) -> Result<String, String> {
    let level = level.min(100);
    with_endpoint(|endpoint| unsafe {
        endpoint.SetMasterVolumeLevelScalar(level as f32 / 100.0, &GUID::zeroed())?;
        endpoint.SetMute(false, &GUID::zeroed())
    })?;
    Ok(format!("Гучність встановлено на {level}%."))
}

pub fn set_muted(muted: bool) -> Result<String, String> {
    with_endpoint(|endpoint| unsafe { endpoint.SetMute(muted, &GUID::zeroed()) })?;
    Ok(if muted {
        "Системний звук вимкнено.".to_owned()
    } else {
        "Системний звук увімкнено.".to_owned()
    })
}

pub fn adjust_volume(delta: i8) -> Result<String, String> {
    let next = with_endpoint(|endpoint| unsafe {
        let current = endpoint.GetMasterVolumeLevelScalar()?;
        let next = (current + delta as f32 / 100.0).clamp(0.0, 1.0);
        endpoint.SetMasterVolumeLevelScalar(next, &GUID::zeroed())?;
        endpoint.SetMute(false, &GUID::zeroed())?;
        Ok(next)
    })?;
    Ok(format!(
        "Гучність встановлено приблизно на {}%.",
        (next * 100.0).round()
    ))
}

pub fn get_system_info() -> Result<String, String> {
    let script = r#"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$os = Get-CimInstance Win32_OperatingSystem
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$computer = Get-CimInstance Win32_ComputerSystem
$disks = Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | ForEach-Object {
    [ordered]@{
        drive = $_.DeviceID
        size_gb = [math]::Round($_.Size / 1GB, 1)
        free_gb = [math]::Round($_.FreeSpace / 1GB, 1)
    }
}
[ordered]@{
    computer_name = $env:COMPUTERNAME
    windows = $os.Caption
    version = $os.Version
    architecture = $os.OSArchitecture
    cpu = $cpu.Name.Trim()
    logical_processors = $computer.NumberOfLogicalProcessors
    memory_total_gb = [math]::Round($computer.TotalPhysicalMemory / 1GB, 1)
    memory_free_gb = [math]::Round($os.FreePhysicalMemory / 1MB, 1)
    uptime_hours = [math]::Round(((Get-Date) - $os.LastBootUpTime).TotalHours, 1)
    disks = @($disks)
} | ConvertTo-Json -Depth 4 -Compress
"#;
    powershell(script, &[])
}
