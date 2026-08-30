use super::powershell;

const AUDIO_API: &str = r#"
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class JarvisAudio {
    [ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
    private class MMDeviceEnumerator { }

    private enum EDataFlow { Render, Capture, All }
    private enum ERole { Console, Multimedia, Communications }

    [Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IMMDeviceEnumerator {
        int NotImpl1();
        [PreserveSig] int GetDefaultAudioEndpoint(EDataFlow dataFlow, ERole role, out IMMDevice device);
    }

    [Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IMMDevice {
        [PreserveSig] int Activate(ref Guid iid, int context, IntPtr activationParams,
            [MarshalAs(UnmanagedType.IUnknown)] out object endpointVolume);
    }

    [Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IAudioEndpointVolume {
        int RegisterControlChangeNotify(IntPtr notify);
        int UnregisterControlChangeNotify(IntPtr notify);
        int GetChannelCount(out uint count);
        int SetMasterVolumeLevel(float level, Guid context);
        int SetMasterVolumeLevelScalar(float level, Guid context);
        int GetMasterVolumeLevel(out float level);
        int GetMasterVolumeLevelScalar(out float level);
        int SetChannelVolumeLevel(uint channel, float level, Guid context);
        int SetChannelVolumeLevelScalar(uint channel, float level, Guid context);
        int GetChannelVolumeLevel(uint channel, out float level);
        int GetChannelVolumeLevelScalar(uint channel, out float level);
        int SetMute([MarshalAs(UnmanagedType.Bool)] bool muted, Guid context);
        int GetMute(out bool muted);
    }

    private static IAudioEndpointVolume Endpoint() {
        var enumerator = (IMMDeviceEnumerator)(new MMDeviceEnumerator());
        IMMDevice device;
        Marshal.ThrowExceptionForHR(enumerator.GetDefaultAudioEndpoint(EDataFlow.Render, ERole.Multimedia, out device));
        Guid iid = typeof(IAudioEndpointVolume).GUID;
        object endpoint;
        Marshal.ThrowExceptionForHR(device.Activate(ref iid, 23, IntPtr.Zero, out endpoint));
        return (IAudioEndpointVolume)endpoint;
    }

    public static void SetVolume(float percent) {
        var endpoint = Endpoint();
        endpoint.SetMasterVolumeLevelScalar(percent / 100.0f, Guid.Empty);
        endpoint.SetMute(false, Guid.Empty);
        Marshal.ReleaseComObject(endpoint);
    }

    public static void SetMuted(bool muted) {
        var endpoint = Endpoint();
        endpoint.SetMute(muted, Guid.Empty);
        Marshal.ReleaseComObject(endpoint);
    }
}
'@
"#;

pub fn set_volume(level: u8) -> Result<String, String> {
    let script = format!(
        "{AUDIO_API}\n[JarvisAudio]::SetVolume([single]$env:JARVIS_VOLUME_LEVEL)\nWrite-Output $env:JARVIS_VOLUME_LEVEL"
    );
    let level = level.to_string();
    powershell(&script, &[("JARVIS_VOLUME_LEVEL", &level)])
        .map(|_| format!("Гучність встановлено на {level}%."))
}

pub fn set_muted(muted: bool) -> Result<String, String> {
    let script = format!(
        "{AUDIO_API}\n[JarvisAudio]::SetMuted($env:JARVIS_MUTED -eq 'true')\nWrite-Output 'OK'"
    );
    let value = if muted { "true" } else { "false" };
    powershell(&script, &[("JARVIS_MUTED", value)]).map(|_| {
        if muted {
            "Системний звук вимкнено.".to_owned()
        } else {
            "Системний звук увімкнено.".to_owned()
        }
    })
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
