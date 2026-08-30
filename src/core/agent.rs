use std::{
    error::Error,
    io,
    io::Write,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

use crate::{
    ai::GroqClient,
    core::fast_command::match_fast_command,
    core::messages::Message,
    tools::ToolRegistry,
    voice::{PlaybackCancellation, VoiceClient},
    voice_input::{VoiceEvent, VoiceInputClient},
};

const SYSTEM_PROMPT: &str = r#"Ти JARVIS — персональний AI-асистент користувача Windows.

Завжди відповідай українською мовою, навіть якщо користувач звернувся іншою мовою.
Ти можеш керувати комп'ютером через доступні інструменти. Якщо доречний інструмент існує — використовуй його замість інструкцій користувачеві.
Не стверджуй, що виконав дію, доки інструмент не підтвердив успіх. Якщо дія не вдалася, чесно і стисло поясни результат.
Для складного запиту можеш послідовно викликати кілька інструментів. Не вигадуй назви, шляхи, процеси чи результати.
Перед закриттям програми, переміщенням або перезаписом файла переконайся, що саме цього просив користувач.
Відповідай коротко, природно, спокійно й професійно. Не описуй технічні деталі простих виконаних дій.
Манера мовлення нагадує стриманого кінематографічного AI-дворецького: точність, спокій, легка суха іронія та шанобливе «пане» там, де це звучить природно.
Для привітань і підтверджень варіюй короткі фрази на кшталт: «Вітаю, пане. Усі системи готові», «До ваших послуг, пане», «Виконую», «Завдання завершено», «Системи працюють штатно».
Для завершення відповіді іноді використовуй: «Готово, пане», «Як завжди, до ваших послуг», «Якщо знадоблюся — я поруч». Не повторюй одну формулу в кожній репліці й не перетворюй відповідь на театральний монолог.
Коли ситуація ризикована, спокійно попередь про наслідки перед дією. Коли користувач помиляється, виправ його тактовно й без зверхності.
Голосове введення може надходити після wake word «Джарвіс» через Whisper. AI-аналіз зображень наразі недоступний."#;

pub struct Agent {
    groq: GroqClient,
    tools: ToolRegistry,
    history: Vec<Message>,
    max_tool_rounds: usize,
    voice: Option<VoiceClient>,
    voice_input: Option<VoiceInputClient>,
    conversation_timeout: Duration,
}

enum PlaybackOutcome {
    Completed,
    Transcript(String),
    Stopped,
}

impl Agent {
    pub fn new(
        groq: GroqClient,
        voice: Option<VoiceClient>,
        voice_input: Option<VoiceInputClient>,
        max_tool_rounds: usize,
        conversation_timeout_secs: u64,
    ) -> Self {
        Self {
            groq,
            tools: ToolRegistry::new(),
            history: vec![Message::system(SYSTEM_PROMPT)],
            max_tool_rounds,
            voice,
            voice_input,
            conversation_timeout: Duration::from_secs(conversation_timeout_secs),
        }
    }

    pub async fn run(&mut self) -> Result<(), Box<dyn Error>> {
        println!("JARVIS Desktop Core запущено.");
        if let Some(input) = self.voice_input.clone() {
            match input.health().await {
                Ok(()) => return self.run_voice(input).await,
                Err(error) => eprintln!(
                    "[Voice Input] Sidecar недоступний ({error}). Переходжу в текстовий режим."
                ),
            }
        }
        self.run_text().await
    }

    async fn run_text(&mut self) -> Result<(), Box<dyn Error>> {
        println!("Напишіть 'exit', щоб вийти.\n");

        loop {
            print!("Ви: ");
            io::stdout().flush()?;

            let mut input = String::new();
            if io::stdin().read_line(&mut input)? == 0 {
                break;
            }

            let input = input.trim();
            if input.eq_ignore_ascii_case("exit") || input.eq_ignore_ascii_case("quit") {
                println!("JARVIS: До зустрічі.");
                break;
            }
            if input.is_empty() {
                continue;
            }

            match self.respond(input).await {
                Ok(answer) => {
                    println!("\nJARVIS: {answer}\n");
                    if let Some(voice) = &self.voice
                        && let Err(error) = voice.speak(&answer).await
                    {
                        eprintln!("[Voice Core] {error}");
                    }
                }
                Err(error) => eprintln!("\nJARVIS: Сталася помилка: {error}\n"),
            }
        }

        Ok(())
    }

    async fn run_voice(&mut self, input: VoiceInputClient) -> Result<(), Box<dyn Error>> {
        println!("Голосовий режим активний. Натисніть Ctrl+Alt+J. Ctrl+C — аварійний вихід.\n");
        input.set_state(false, false).await?;
        let mut conversation_deadline: Option<Instant> = None;

        loop {
            let wait = conversation_deadline
                .map(|deadline| deadline.saturating_duration_since(Instant::now()))
                .unwrap_or(Duration::from_secs(30))
                .min(Duration::from_secs(30));

            if wait.is_zero() {
                conversation_deadline = None;
                input.set_state(false, false).await?;
                println!("[Voice Input] Розмовну сесію завершено. Натисніть Ctrl+Alt+J.");
                continue;
            }

            let event = tokio::select! {
                result = input.next_event(wait) => match result {
                    Ok(event) => event,
                    Err(error) => {
                        eprintln!("[Voice Input] Зв'язок із sidecar втрачено ({error}). Переходжу в текстовий режим.");
                        return self.run_text().await;
                    }
                },
                _ = tokio::signal::ctrl_c() => {
                    let _ = input.set_state(false, false).await;
                    println!("\nJARVIS: Прослуховування аварійно припинено.");
                    break;
                }
            };

            match event {
                VoiceEvent::Wake => {
                    conversation_deadline = Some(Instant::now() + self.conversation_timeout);
                    println!("[Voice Input] Слухаю...");
                    if let Some(voice) = self.voice.clone() {
                        input.set_state(true, true).await?;
                        if let Err(error) = voice.speak("До ваших послуг, пане.").await
                        {
                            eprintln!("[Voice Core] {error}");
                        }
                    }
                    input.set_state(true, false).await?;
                }
                VoiceEvent::Transcript {
                    text,
                    mic_end_unix_ms,
                    stt_done_unix_ms,
                } => {
                    let text = clean_wake_word(&text);
                    if text.is_empty() {
                        continue;
                    }
                    println!("Ви: {text}");
                    if let Some(command) = match_fast_command(&text) {
                        let intent_at = unix_ms();
                        let tool_start = unix_ms();
                        let tools = self.tools.clone();
                        let intent = command.intent;
                        let arguments = command.arguments.clone();
                        // The tool starts on a blocking worker immediately. In
                        // parallel, the existing sidecar contract closes the
                        // microphone for half-duplex execution/TTS.
                        let tool_task =
                            tokio::task::spawn_blocking(move || tools.execute(intent, &arguments));
                        input.set_state(true, true).await?;
                        let result = tool_task.await.map_err(io::Error::other)?;
                        let tool_done = unix_ms();
                        let answer = fast_answer(command.intent, &result, command.acknowledgement);
                        self.history.push(Message::user(&text));
                        self.history.push(Message::assistant(&answer));
                        println!(
                            "[Fast Path] intent={} args={}",
                            command.intent, command.arguments
                        );
                        println!("\nJARVIS: {answer}\n");
                        conversation_deadline = Some(Instant::now() + self.conversation_timeout);

                        let tts_start = self.voice.as_ref().map(|_| unix_ms());
                        log_fast_latency(
                            mic_end_unix_ms,
                            stt_done_unix_ms,
                            intent_at,
                            tool_start,
                            tool_done,
                            tts_start,
                        );

                        if let Some(voice) = self.voice.clone() {
                            match self.speak_with_barge_in(&input, &voice, &answer).await {
                                PlaybackOutcome::Transcript(interrupted_text)
                                    if !interrupted_text.is_empty() =>
                                {
                                    println!("Ви (перебивання): {interrupted_text}");
                                }
                                PlaybackOutcome::Stopped => return self.run_text().await,
                                PlaybackOutcome::Completed | PlaybackOutcome::Transcript(_) => {}
                            }
                        } else {
                            input.set_state(true, false).await?;
                        }
                        continue;
                    }
                    // LLM fallback keeps the microphone closed while the agent
                    // thinks and performs tools, not only during audible TTS.
                    input.set_state(true, true).await?;
                    match self.respond(&text).await {
                        Ok(answer) => {
                            println!("\nJARVIS: {answer}\n");
                            conversation_deadline =
                                Some(Instant::now() + self.conversation_timeout);
                            if let Some(voice) = self.voice.clone() {
                                match self.speak_with_barge_in(&input, &voice, &answer).await {
                                    PlaybackOutcome::Transcript(interrupted_text)
                                        if !interrupted_text.is_empty() =>
                                    {
                                        println!("Ви (перебивання): {interrupted_text}");
                                        input.set_state(true, true).await?;
                                        match self.respond(&interrupted_text).await {
                                            Ok(new_answer) => {
                                                println!("\nJARVIS: {new_answer}\n");
                                                if matches!(
                                                    self.speak_with_barge_in(
                                                        &input,
                                                        &voice,
                                                        &new_answer,
                                                    )
                                                    .await,
                                                    PlaybackOutcome::Stopped
                                                ) {
                                                    return self.run_text().await;
                                                }
                                            }
                                            Err(error) => {
                                                eprintln!("JARVIS: Сталася помилка: {error}")
                                            }
                                        }
                                    }
                                    PlaybackOutcome::Stopped => {
                                        println!(
                                            "[Voice Input] Прослуховування зупинено. Переходжу в текстовий режим."
                                        );
                                        return self.run_text().await;
                                    }
                                    PlaybackOutcome::Completed | PlaybackOutcome::Transcript(_) => {
                                    }
                                }
                            } else {
                                input.set_state(true, false).await?;
                            }
                        }
                        Err(error) => {
                            eprintln!("\nJARVIS: Сталася помилка: {error}\n");
                            input.set_state(true, false).await?;
                        }
                    }
                }
                VoiceEvent::Error { message } => eprintln!("[Voice Input] {message}"),
                VoiceEvent::Stopped => {
                    println!(
                        "[Voice Input] Прослуховування зупинено. Переходжу в текстовий режим."
                    );
                    return self.run_text().await;
                }
                VoiceEvent::Interrupt | VoiceEvent::Timeout => {}
            }
        }
        Ok(())
    }

    async fn speak_with_barge_in(
        &self,
        input: &VoiceInputClient,
        voice: &VoiceClient,
        answer: &str,
    ) -> PlaybackOutcome {
        let cancellation = PlaybackCancellation::default();
        let playback = voice.speak_cancellable(answer, cancellation.clone());
        tokio::pin!(playback);
        let mut interrupted = false;
        let mut outcome = PlaybackOutcome::Completed;

        loop {
            tokio::select! {
                result = &mut playback => {
                    if let Err(error) = result { eprintln!("[Voice Core] {error}"); }
                    break;
                }
                event = input.next_event(Duration::from_secs(30)) => match event {
                    Ok(VoiceEvent::Interrupt) => {
                        interrupted = true;
                        cancellation.cancel();
                    }
                    Ok(VoiceEvent::Transcript { text, .. }) => {
                        cancellation.cancel();
                        outcome = PlaybackOutcome::Transcript(clean_wake_word(&text));
                        break;
                    }
                    Ok(VoiceEvent::Error { message }) => eprintln!("[Voice Input] {message}"),
                    Ok(VoiceEvent::Stopped) => {
                        cancellation.cancel();
                        outcome = PlaybackOutcome::Stopped;
                        break;
                    }
                    Ok(_) => {}
                    Err(error) => { eprintln!("[Voice Input] {error}"); break; }
                }
            }
            if interrupted && matches!(outcome, PlaybackOutcome::Completed) {
                // Playback is already stopping; keep listening until STT emits the replacement command.
                continue;
            }
        }
        let _ = input.set_state(true, false).await;
        outcome
    }

    async fn respond(&mut self, input: &str) -> Result<String, Box<dyn Error>> {
        self.history.push(Message::user(input));

        for _ in 0..self.max_tool_rounds {
            let message = self.groq.chat(&self.history, &self.tools).await?;
            let tool_calls = message.tool_calls.clone().unwrap_or_default();
            let content = message.content.clone();
            self.history.push(message);

            if tool_calls.is_empty() {
                return Ok(content.unwrap_or_else(|| "Готово.".to_owned()));
            }

            for call in tool_calls {
                let result = self
                    .tools
                    .execute(&call.function.name, &call.function.arguments);
                self.history.push(Message::tool(call.id, result));
            }
        }

        Err(io::Error::other("Перевищено ліміт послідовних викликів інструментів").into())
    }
}

fn clean_wake_word(text: &str) -> String {
    let trimmed = text.trim();
    let lower = trimmed.to_lowercase();
    for prefix in [
        "гей джарвіс",
        "хей джарвіс",
        "джарвіс",
        "hey jarvis",
        "jarvis",
    ] {
        if lower.starts_with(prefix) {
            return trimmed[prefix.len()..]
                .trim_start_matches(|character: char| {
                    character.is_whitespace() || ",.!:;—-".contains(character)
                })
                .trim()
                .to_owned();
        }
    }
    trimmed.to_owned()
}

fn unix_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

fn elapsed_ms(start: Option<u64>, end: Option<u64>) -> Option<u64> {
    Some(end?.saturating_sub(start?))
}

fn log_fast_latency(
    mic_end: Option<u64>,
    stt_done: Option<u64>,
    intent_at: u64,
    tool_start: u64,
    tool_done: u64,
    tts_start: Option<u64>,
) {
    let total_end = tts_start.unwrap_or(tool_done);
    println!(
        "[Latency] fast_path=true mic_end->stt_done={} stt_done->intent={} intent->tool_start={} tool_start->tool_done={} tool_done->tts_start={} total={} ms",
        metric(elapsed_ms(mic_end, stt_done)),
        metric(elapsed_ms(stt_done, Some(intent_at))),
        metric(elapsed_ms(Some(intent_at), Some(tool_start))),
        metric(elapsed_ms(Some(tool_start), Some(tool_done))),
        metric(elapsed_ms(Some(tool_done), tts_start)),
        metric(elapsed_ms(mic_end, Some(total_end))),
    );
}

fn metric(value: Option<u64>) -> String {
    value.map_or_else(|| "n/a".to_owned(), |value| value.to_string())
}

fn fast_answer(intent: &str, result: &str, acknowledgement: &str) -> String {
    let payload: serde_json::Value = match serde_json::from_str(result) {
        Ok(payload) => payload,
        Err(_) => return acknowledgement.to_owned(),
    };
    if payload.get("success").and_then(serde_json::Value::as_bool) == Some(true) {
        if intent == "get_running_apps"
            && let Some(raw_apps) = payload.get("result").and_then(serde_json::Value::as_str)
            && let Ok(apps) = serde_json::from_str::<serde_json::Value>(raw_apps)
        {
            let items = apps.as_array().cloned().unwrap_or_else(|| vec![apps]);
            let mut names = Vec::new();
            for item in items {
                if let Some(name) = item.get("name").and_then(serde_json::Value::as_str)
                    && !names.iter().any(|existing| existing == name)
                {
                    names.push(name.to_owned());
                }
                if names.len() == 8 {
                    break;
                }
            }
            if !names.is_empty() {
                return format!("Запущені програми: {}.", names.join(", "));
            }
        }
        acknowledgement.to_owned()
    } else {
        payload
            .get("error")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("Не вдалося виконати команду.")
            .to_owned()
    }
}

#[cfg(test)]
mod tests {
    use super::clean_wake_word;

    #[test]
    fn strips_supported_wake_words() {
        assert_eq!(
            clean_wake_word("Джарвіс, відкрий YouTube"),
            "відкрий YouTube"
        );
        assert_eq!(clean_wake_word("hey jarvis open browser"), "open browser");
        assert_eq!(clean_wake_word("Яка погода?"), "Яка погода?");
    }
}
