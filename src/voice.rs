use std::{
    error::Error,
    io::Cursor,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    time::Duration,
};

use reqwest::Client;
use rodio::DeviceSinkBuilder;
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

    pub async fn speak_cancellable(
        &self,
        text: &str,
        cancellation: PlaybackCancellation,
    ) -> Result<(), Box<dyn Error + Send + Sync>> {
        let response = self
            .http
            .post(format!("{}/synthesize", self.base_url))
            .timeout(Duration::from_secs(60))
            .json(&SynthesizeRequest { text })
            .send()
            .await?;

        if !response.status().is_success() {
            let status = response.status();
            let details = response.text().await.unwrap_or_default();
            return Err(format!("TTS service повернув {status}: {details}").into());
        }

        let wav = response.bytes().await?.to_vec();
        tokio::task::spawn_blocking(move || play_wav(wav, cancellation))
            .await
            .map_err(|error| -> Box<dyn Error + Send + Sync> {
                format!("потік відтворення завершився з помилкою: {error}").into()
            })??;

        Ok(())
    }
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
        std::thread::sleep(Duration::from_millis(20));
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
