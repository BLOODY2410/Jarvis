use std::{
    error::Error,
    io::Cursor,
    num::{NonZeroU16, NonZeroU32},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
        mpsc,
    },
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

use reqwest::Client;
use rodio::{DeviceSinkBuilder, Player, buffer::SamplesBuffer};
use serde::Serialize;

#[derive(Clone)]
pub struct VoiceClient {
    http: Client,
    base_url: String,
}

#[derive(Clone, Default)]
pub struct PlaybackCancellation(Arc<AtomicBool>);

impl PlaybackCancellation {
    pub fn cancel(&self) {
        self.0.store(true, Ordering::Release);
    }

    fn is_cancelled(&self) -> bool {
        self.0.load(Ordering::Acquire)
    }
}

#[derive(Serialize)]
struct SynthesizeRequest<'a> {
    text: &'a str,
}

impl VoiceClient {
    pub fn new(base_url: String) -> Self {
        Self {
            http: Client::new(),
            base_url: base_url.trim_end_matches('/').to_owned(),
        }
    }

    pub async fn speak(&self, text: &str) -> Result<(), Box<dyn Error + Send + Sync>> {
        self.speak_cancellable(text, PlaybackCancellation::default())
            .await
    }

    pub async fn speak_ack(&self, name: &str) -> Result<(), Box<dyn Error + Send + Sync>> {
        let response = self
            .http
            .get(format!("{}/ack/{name}", self.base_url))
            .timeout(Duration::from_secs(2))
            .send()
            .await?;
        if response.status() == reqwest::StatusCode::NO_CONTENT {
            return tokio::task::spawn_blocking(play_activation_cue)
                .await
                .map_err(|error| -> Box<dyn Error + Send + Sync> {
                    format!("ack cue worker failed: {error}").into()
                })?;
        }
        let response = response.error_for_status()?;
        let wav = response.bytes().await?.to_vec();
        tokio::task::spawn_blocking(move || play_wav(wav, PlaybackCancellation::default()))
            .await
            .map_err(|error| -> Box<dyn Error + Send + Sync> {
                format!("ack playback worker failed: {error}").into()
            })??;
        Ok(())
    }

    pub async fn speak_cancellable(
        &self,
        text: &str,
        cancellation: PlaybackCancellation,
    ) -> Result<(), Box<dyn Error + Send + Sync>> {
        let request_started = unix_ms();
        let response = self
            .http
            .post(format!("{}/synthesize/stream", self.base_url))
            .timeout(Duration::from_secs(60))
            .json(&SynthesizeRequest { text })
            .send()
            .await?;

        if !response.status().is_success() {
            let status = response.status();
            let details = response.text().await.unwrap_or_default();
            return Err(format!("TTS service повернув {status}: {details}").into());
        }

        let sample_rate = response
            .headers()
            .get("X-Jarvis-Sample-Rate")
            .and_then(|value| value.to_str().ok())
            .and_then(|value| value.parse::<u32>().ok())
            .unwrap_or(44_100);
        let channels = response
            .headers()
            .get("X-Jarvis-Channels")
            .and_then(|value| value.to_str().ok())
            .and_then(|value| value.parse::<u16>().ok())
            .unwrap_or(1);
        let mut response = response;
        let (sender, receiver) = mpsc::sync_channel::<Option<Vec<f32>>>(8);
        let playback_cancel = cancellation.clone();
        let playback = tokio::task::spawn_blocking(move || {
            play_pcm_stream(receiver, sample_rate, channels, playback_cancel)
        });
        let mut pending = Vec::new();
        let mut first_byte = None;
        let mut playback_start = None;
        while let Some(chunk) = response.chunk().await? {
            if first_byte.is_none() {
                first_byte = Some(unix_ms());
            }
            pending.extend_from_slice(&chunk);
            if pending.len() < (sample_rate as usize * channels as usize * 2 / 25) {
                continue;
            }
            if pending.len() % 2 != 0 {
                continue;
            }
            let samples = pcm16_to_f32(&pending);
            pending.clear();
            if playback_start.is_none() {
                playback_start = Some(unix_ms());
            }
            if sender.send(Some(samples)).is_err() || cancellation.is_cancelled() {
                break;
            }
        }
        if !pending.is_empty() {
            pending.truncate(pending.len() & !1);
            let _ = sender.send(Some(pcm16_to_f32(&pending)));
        }
        let _ = sender.send(None);
        drop(sender);
        playback
            .await
            .map_err(|error| -> Box<dyn Error + Send + Sync> {
                format!("потік відтворення завершився з помилкою: {error}").into()
            })??;
        let done = unix_ms();
        println!(
            "[Latency] tts_request_start={} tts_first_byte={} playback_start={} playback_done={} tts_total={} ms",
            request_started,
            metric_ms(first_byte.map(|value| value.saturating_sub(request_started))),
            metric_ms(playback_start.map(|value| value.saturating_sub(request_started))),
            done,
            done.saturating_sub(request_started),
        );

        Ok(())
    }
}

pub fn play_activation_cue() -> Result<(), Box<dyn Error + Send + Sync>> {
    let sample_rate = 16_000u32;
    let duration_ms = 75u32;
    let samples: Vec<f32> = (0..sample_rate * duration_ms / 1_000)
        .map(|index| {
            let t = index as f32 / sample_rate as f32;
            let envelope = 1.0 - index as f32 / (sample_rate * duration_ms / 1_000) as f32;
            (std::f32::consts::TAU * 880.0 * t).sin() * envelope * 0.12
        })
        .collect();
    let (sender, receiver) = mpsc::sync_channel(2);
    sender.send(Some(samples))?;
    sender.send(None)?;
    play_pcm_stream(receiver, sample_rate, 1, PlaybackCancellation::default())
}

fn play_pcm_stream(
    receiver: mpsc::Receiver<Option<Vec<f32>>>,
    sample_rate: u32,
    channels: u16,
    cancellation: PlaybackCancellation,
) -> Result<(), Box<dyn Error + Send + Sync>> {
    let mut sink = DeviceSinkBuilder::open_default_sink()?;
    sink.log_on_drop(false);
    let player = Player::connect_new(sink.mixer());
    let started = Instant::now();
    while let Ok(message) = receiver.recv() {
        if cancellation.is_cancelled() || started.elapsed() > Duration::from_secs(90) {
            player.stop();
            return Ok(());
        }
        let Some(samples) = message else { break };
        if !samples.is_empty() {
            player.append(SamplesBuffer::new(
                NonZeroU16::new(channels).unwrap_or(NonZeroU16::MIN),
                NonZeroU32::new(sample_rate).unwrap_or(NonZeroU32::MIN),
                samples,
            ));
        }
    }
    while !player.empty() {
        if cancellation.is_cancelled() {
            player.stop();
            break;
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    Ok(())
}

fn pcm16_to_f32(bytes: &[u8]) -> Vec<f32> {
    bytes
        .chunks_exact(2)
        .map(|sample| i16::from_le_bytes([sample[0], sample[1]]) as f32 / 32768.0)
        .collect()
}

fn unix_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

fn metric_ms(value: Option<u64>) -> String {
    value.map_or_else(|| "n/a".to_owned(), |value| value.to_string())
}

fn play_wav(
    wav: Vec<u8>,
    cancellation: PlaybackCancellation,
) -> Result<(), Box<dyn Error + Send + Sync>> {
    let mut sink = DeviceSinkBuilder::open_default_sink()?;
    sink.log_on_drop(false);
    let player = rodio::play(sink.mixer(), Cursor::new(wav))?;
    while !player.empty() {
        if cancellation.is_cancelled() {
            player.stop();
            break;
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::VoiceClient;

    // Run explicitly while voice_service is active:
    // cargo test voice_service_playback -- --ignored --nocapture
    #[tokio::test]
    #[ignore = "requires the local voice sidecar and an audio output device"]
    async fn voice_service_playback() {
        let url = std::env::var("JARVIS_TTS_TEST_URL")
            .unwrap_or_else(|_| "http://127.0.0.1:8765".to_owned());
        VoiceClient::new(url)
            .speak("Наскрізний тест завершено. Український голосовий модуль працює.")
            .await
            .expect("Rust Core could not synthesize or play the WAV response");
    }
}
