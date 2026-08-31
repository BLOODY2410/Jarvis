use std::{
    collections::VecDeque,
    error::Error,
    fs::{self, OpenOptions},
    io,
    io::Write,
    process::Command,
    sync::{Mutex, OnceLock},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

use crate::{
    ai::GroqClient,
    core::fast_command::match_fast_command,
    core::messages::Message,
    tools::ToolRegistry,
    voice::{PlaybackCancellation, VoiceClient, play_activation_cue},
    voice_input::{VoiceEvent, VoiceInputClient},
};

const SYSTEM_PROMPT: &str = r#"Ти JARVIS — персональний AI-асистент користувача Windows.

МОВА І ЗВЕРТАННЯ
Завжди відповідай природною українською мовою.
Звертайся до користувача словом «сер», але лише зрідка й там, де це звучить природно. Не додавай звертання до кожної репліки.

МАНЕРА
Говори як стриманий кінематографічний AI-дворецький: спокійно, точно, інтелігентно й максимально лаконічно.
Відразу переходь до суті. Не повторюй запит користувача й не перефразовуй його замість відповіді.
Не додавай сервісних завершальних фраз, пропозицій подальшої допомоги чи нагадувань про свою присутність.
Не будь театральним або пафосним. Не використовуй емодзі. Не став окличні знаки без потреби.
Ледь помітна суха іронія дозволена рідко, лише доречно й не довше однієї короткої фрази. Не жартуй заради жарту.
Пиши короткими завершеними реченнями з природними паузами для TTS. Не використовуй SSML.

ДІЇ ТА БЕЗПЕКА
Керуй комп'ютером через доступні інструменти, коли вони доречні.
Не стверджуй, що виконав дію, доки інструмент не підтвердив успіх.
Не вигадуй назви, шляхи, процеси, результати інструментів або факти.
Перед закриттям програми, переміщенням або перезаписом файла переконайся, що саме цього просив користувач.
При помилці або ризику спочатку спокійно назви факт, потім дай одну коротку рекомендацію.
Якщо запит неоднозначний, постав одне коротке уточнення.

АКТУАЛЬНІ ДАНІ
У тебе немає інструмента вебпошуку чи перевіреного live-джерела.
Не вигадуй актуальні новини, погоду, ціни, курси, спортивні результати, статуси сервісів або свіжі події.
Коли потрібне актуальне джерело, прямо й коротко скажи, що його немає, і що ти не станеш вигадувати.

БЮДЖЕТ ГОЛОСОВОЇ ВІДПОВІДІ
Проста виконана дія: 2–10 слів.
Просте питання: 1–2 короткі речення.
Складне питання: не більше 3 коротких речень або орієнтовно 25–40 слів, якщо користувач явно не попросив деталі.
Для потенційно довгої відповіді спочатку дай коротке резюме. Не озвучуй довгі списки без прямого прохання.
Не скорочуй критичні застереження з безпеки.

ОРІЄНТИРИ СТИЛЮ
Привітання: «Вітаю, сер.»
Стан систем: «Усі системи працюють штатно, сер.»
Успішна дія: «YouTube відкрито.» або «Visual Studio Code відкрито, сер.»
Немає live-джерела: «Актуального джерела новин у мене поки немає, сер. Не стану вигадувати.»
Невдала дія: «Не вдалося відкрити програму, сер. Windows її не знайшла.»
Рідкісна суха іронія: «Не цілком, сер. Але, підозрюю, це вас не зупинить.»"#;
const NORMAL_SHUTDOWN_MESSAGE: &str = "До зустрічі, сер.";
const EMERGENCY_SHUTDOWN_MESSAGE: &str = "Прослуховування аварійно припинено.";

pub struct Agent {
    groq: GroqClient,
    tools: ToolRegistry,
    history: Vec<Message>,
    max_tool_rounds: usize,
    voice: Option<VoiceClient>,
    voice_input: Option<VoiceInputClient>,
    conversation_timeout: Duration,
    max_context_turns: usize,
}

enum PlaybackOutcome {
    Completed,
    Transcript(String),
    Stopped,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum VoiceState {
    Idle,
    Activated,
    Listening,
    Transcribing,
    Executing,
    Speaking,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum PowerAction {
    Shutdown,
    Restart,
    Sleep,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum LocalControl {
    EndSession,
    SleepAssistant,
    ShutdownAssistant,
    RequestPower(PowerAction),
    Confirm,
    Cancel,
}

fn listening_deadline(state: VoiceState, now: Instant, timeout: Duration) -> Option<Instant> {
    (state == VoiceState::Listening).then_some(now + timeout)
}

fn transition(
    state: &mut VoiceState,
    conversation_deadline: &mut Option<Instant>,
    next: VoiceState,
    reason: &str,
    timeout: Duration,
) {
    if *state != next {
        if std::env::var("JARVIS_PROFILE")
            .map(|value| value.eq_ignore_ascii_case("debug"))
            .unwrap_or(false)
        {
            println!("[Voice State] {:?} -> {:?} reason={reason}", *state, next);
        }
        *state = next;
    }
    *conversation_deadline = listening_deadline(next, Instant::now(), timeout);
}

impl Agent {
    pub fn new(
        groq: GroqClient,
        voice: Option<VoiceClient>,
        voice_input: Option<VoiceInputClient>,
        max_tool_rounds: usize,
        conversation_timeout_secs: u64,
        max_context_turns: usize,
    ) -> Self {
        Self {
            groq,
            tools: ToolRegistry::new(),
            history: vec![Message::system(SYSTEM_PROMPT)],
            max_tool_rounds,
            voice,
            voice_input,
            conversation_timeout: Duration::from_secs(conversation_timeout_secs),
            max_context_turns,
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
                Err(error) => eprintln!("\nJARVIS: {error}\n"),
            }
        }

        Ok(())
    }

    async fn run_voice(&mut self, input: VoiceInputClient) -> Result<(), Box<dyn Error>> {
        println!(
            "Голосовий режим активний. Скажіть «Джарвіс» або натисніть Ctrl+Alt+J. Ctrl+C — аварійний вихід.\n"
        );
        input.set_state(false, false).await?;
        let mut voice_state = VoiceState::Idle;
        let mut conversation_deadline: Option<Instant> = None;
        let mut pending_power: Option<PowerAction> = None;

        loop {
            let wait = conversation_deadline
                .map(|deadline| deadline.saturating_duration_since(Instant::now()))
                .unwrap_or(Duration::from_secs(30))
                .min(Duration::from_secs(30));

            if wait.is_zero() {
                input.set_state(false, false).await?;
                transition(
                    &mut voice_state,
                    &mut conversation_deadline,
                    VoiceState::Idle,
                    "conversation_timeout",
                    self.conversation_timeout,
                );
                println!(
                    "[Voice Input] Розмовну сесію завершено. Скажіть «Джарвіс» або натисніть Ctrl+Alt+J."
                );
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
                    println!("\nJARVIS: {EMERGENCY_SHUTDOWN_MESSAGE}");
                    break;
                }
            };

            match event {
                VoiceEvent::Wake => {
                    transition(
                        &mut voice_state,
                        &mut conversation_deadline,
                        VoiceState::Activated,
                        "activation_event",
                        self.conversation_timeout,
                    );
                    println!("[Voice Input] Слухаю...");
                    if self.voice.is_some() {
                        input.set_state(true, true).await?;
                        transition(
                            &mut voice_state,
                            &mut conversation_deadline,
                            VoiceState::Speaking,
                            "local_activation_cue",
                            self.conversation_timeout,
                        );
                        if let Err(error) = tokio::task::spawn_blocking(play_activation_cue)
                            .await
                            .map_err(io::Error::other)?
                        {
                            eprintln!("[Voice Core] {error}");
                        }
                    }
                    input.set_state(true, false).await?;
                    transition(
                        &mut voice_state,
                        &mut conversation_deadline,
                        VoiceState::Listening,
                        "activation_cue_complete",
                        self.conversation_timeout,
                    );
                }
                VoiceEvent::SpeechStarted => {
                    if voice_state == VoiceState::Listening {
                        transition(
                            &mut voice_state,
                            &mut conversation_deadline,
                            VoiceState::Transcribing,
                            "speech_started",
                            self.conversation_timeout,
                        );
                    }
                }
                VoiceEvent::Listening => {
                    if voice_state == VoiceState::Transcribing {
                        input.set_state(true, false).await?;
                        transition(
                            &mut voice_state,
                            &mut conversation_deadline,
                            VoiceState::Listening,
                            "stt_filtered_or_empty",
                            self.conversation_timeout,
                        );
                    }
                }
                VoiceEvent::Transcript {
                    text,
                    mic_end_unix_ms,
                    stt_first_byte_unix_ms,
                    stt_done_unix_ms,
                } => {
                    transition(
                        &mut voice_state,
                        &mut conversation_deadline,
                        VoiceState::Transcribing,
                        "transcript_received",
                        self.conversation_timeout,
                    );
                    let text = clean_wake_word(&text);
                    if text.is_empty() {
                        input.set_state(true, false).await?;
                        transition(
                            &mut voice_state,
                            &mut conversation_deadline,
                            VoiceState::Listening,
                            "empty_transcript",
                            self.conversation_timeout,
                        );
                        continue;
                    }
                    println!("Ви: {text}");
                    if let Some(control) = match_local_control(&text) {
                        input.set_state(false, true).await?;
                        let (answer, idle_after, shutdown_core) = match control {
                            LocalControl::EndSession => {
                                pending_power = None;
                                (NORMAL_SHUTDOWN_MESSAGE.to_owned(), true, false)
                            }
                            LocalControl::SleepAssistant => {
                                pending_power = None;
                                ("Зрозумів, сер.".to_owned(), true, false)
                            }
                            LocalControl::ShutdownAssistant => {
                                pending_power = None;
                                (NORMAL_SHUTDOWN_MESSAGE.to_owned(), true, true)
                            }
                            LocalControl::RequestPower(action) => {
                                pending_power = Some(action);
                                (power_confirmation_prompt(action).to_owned(), false, false)
                            }
                            LocalControl::Confirm => match pending_power.take() {
                                Some(action) => match execute_power_action(action) {
                                    Ok(answer) => (answer, false, false),
                                    Err(error) => (error, false, false),
                                },
                                None => {
                                    ("Немає дії для підтвердження, сер.".to_owned(), false, false)
                                }
                            },
                            LocalControl::Cancel => {
                                pending_power = None;
                                ("Скасовано.".to_owned(), false, false)
                            }
                        };
                        println!("\nJARVIS: {answer}\n");
                        if let Some(voice) = &self.voice
                            && let Err(error) = voice.speak(&answer).await
                        {
                            eprintln!("[Voice Core] {error}");
                        }
                        if shutdown_core {
                            let _ = input.set_state(false, false).await;
                            break;
                        }
                        if idle_after {
                            input.set_state(false, false).await?;
                            transition(
                                &mut voice_state,
                                &mut conversation_deadline,
                                VoiceState::Idle,
                                "local_session_control",
                                self.conversation_timeout,
                            );
                        } else {
                            input.set_state(true, false).await?;
                            transition(
                                &mut voice_state,
                                &mut conversation_deadline,
                                VoiceState::Listening,
                                "local_control_complete",
                                self.conversation_timeout,
                            );
                        }
                        continue;
                    }
                    if let Some(answer) = guarded_local_answer(&text) {
                        input.set_state(true, true).await?;
                        println!("[Local Router] matched=true");
                        println!("\nJARVIS: {answer}\n");
                        if let Some(voice) = self.voice.clone() {
                            transition(
                                &mut voice_state,
                                &mut conversation_deadline,
                                VoiceState::Speaking,
                                "local_reply",
                                self.conversation_timeout,
                            );
                            if matches!(
                                self.speak_with_barge_in(&input, &voice, answer).await,
                                PlaybackOutcome::Stopped
                            ) {
                                return self.run_text().await;
                            }
                        } else {
                            input.set_state(true, false).await?;
                        }
                        transition(
                            &mut voice_state,
                            &mut conversation_deadline,
                            VoiceState::Listening,
                            "local_reply_complete",
                            self.conversation_timeout,
                        );
                        continue;
                    }
                    if let Some(command) = match_fast_command(&text) {
                        let intent_at = unix_ms();
                        let tool_start = unix_ms();
                        transition(
                            &mut voice_state,
                            &mut conversation_deadline,
                            VoiceState::Executing,
                            "fast_intent_matched",
                            self.conversation_timeout,
                        );
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
                        println!(
                            "[Fast Path] intent={} args={}",
                            command.intent, command.arguments
                        );
                        println!("\nJARVIS: {answer}\n");
                        let tts_start = self.voice.as_ref().map(|_| unix_ms());
                        log_fast_latency(
                            mic_end_unix_ms,
                            stt_first_byte_unix_ms,
                            stt_done_unix_ms,
                            intent_at,
                            tool_start,
                            tool_done,
                            tts_start,
                        );

                        if let Some(voice) = self.voice.clone() {
                            transition(
                                &mut voice_state,
                                &mut conversation_deadline,
                                VoiceState::Speaking,
                                "fast_acknowledgement",
                                self.conversation_timeout,
                            );
                            let playback = if answer == command.acknowledgement
                                && command.acknowledgement_cache.is_some()
                            {
                                input.set_state(true, true).await?;
                                match voice
                                    .speak_ack(command.acknowledgement_cache.unwrap_or("done"))
                                    .await
                                {
                                    Ok(true) => {
                                        let _ = input.set_state(true, false).await;
                                        PlaybackOutcome::Completed
                                    }
                                    Ok(false) => {
                                        self.speak_with_barge_in(&input, &voice, &answer).await
                                    }
                                    Err(error) => {
                                        eprintln!("[Voice Core] {error}");
                                        self.speak_with_barge_in(&input, &voice, &answer).await
                                    }
                                }
                            } else {
                                self.speak_with_barge_in(&input, &voice, &answer).await
                            };
                            match playback {
                                PlaybackOutcome::Transcript(interrupted_text)
                                    if !interrupted_text.is_empty() =>
                                {
                                    println!("Ви (перебивання): {interrupted_text}");
                                }
                                PlaybackOutcome::Stopped => return self.run_text().await,
                                PlaybackOutcome::Completed | PlaybackOutcome::Transcript(_) => {}
                            }
                            transition(
                                &mut voice_state,
                                &mut conversation_deadline,
                                VoiceState::Listening,
                                "fast_ack_complete",
                                self.conversation_timeout,
                            );
                        } else {
                            input.set_state(true, false).await?;
                            transition(
                                &mut voice_state,
                                &mut conversation_deadline,
                                VoiceState::Listening,
                                "tool_complete_no_tts",
                                self.conversation_timeout,
                            );
                        }
                        continue;
                    }
                    if looks_like_uncertain_computer_command(&text) {
                        let answer = "Не розібрав команду, сер. Повторіть.";
                        input.set_state(true, true).await?;
                        println!("[Safety Guard] uncertain_command=true tool_action=false");
                        println!("\nJARVIS: {answer}\n");
                        if let Some(voice) = self.voice.clone() {
                            let _ = self.speak_with_barge_in(&input, &voice, answer).await;
                        } else {
                            input.set_state(true, false).await?;
                        }
                        transition(
                            &mut voice_state,
                            &mut conversation_deadline,
                            VoiceState::Listening,
                            "uncertain_command_rejected",
                            self.conversation_timeout,
                        );
                        continue;
                    }
                    // LLM fallback keeps the microphone closed while the agent
                    // thinks and performs tools, not only during audible TTS.
                    input.set_state(true, true).await?;
                    transition(
                        &mut voice_state,
                        &mut conversation_deadline,
                        VoiceState::Executing,
                        "llm_fallback",
                        self.conversation_timeout,
                    );
                    let llm_started = unix_ms();
                    match self.respond_voice(&text).await {
                        Ok(answer) => {
                            let llm_done = unix_ms();
                            println!(
                                "[Latency] llm_path=true speech_end->llm_start={} llm_total={} ms",
                                metric(elapsed_ms(mic_end_unix_ms, Some(llm_started))),
                                llm_done.saturating_sub(llm_started)
                            );
                            println!("\nJARVIS: {answer}\n");
                            if let Some(voice) = self.voice.clone() {
                                transition(
                                    &mut voice_state,
                                    &mut conversation_deadline,
                                    VoiceState::Speaking,
                                    "llm_response_ready",
                                    self.conversation_timeout,
                                );
                                match self.speak_with_barge_in(&input, &voice, &answer).await {
                                    PlaybackOutcome::Transcript(interrupted_text)
                                        if !interrupted_text.is_empty() =>
                                    {
                                        println!("Ви (перебивання): {interrupted_text}");
                                        input.set_state(true, true).await?;
                                        match self.respond_voice(&interrupted_text).await {
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
                                            Err(error) => eprintln!("JARVIS: {error}"),
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
                                transition(
                                    &mut voice_state,
                                    &mut conversation_deadline,
                                    VoiceState::Listening,
                                    "llm_playback_complete",
                                    self.conversation_timeout,
                                );
                            } else {
                                input.set_state(true, false).await?;
                                transition(
                                    &mut voice_state,
                                    &mut conversation_deadline,
                                    VoiceState::Listening,
                                    "llm_complete_no_tts",
                                    self.conversation_timeout,
                                );
                            }
                        }
                        Err(error) => {
                            let answer = error.to_string();
                            eprintln!("\nJARVIS: {answer}\n");
                            if let Some(voice) = &self.voice
                                && let Err(tts_error) = voice.speak(&answer).await
                            {
                                eprintln!("[Voice Core] {tts_error}");
                            }
                            input.set_state(true, false).await?;
                            transition(
                                &mut voice_state,
                                &mut conversation_deadline,
                                VoiceState::Listening,
                                "llm_error",
                                self.conversation_timeout,
                            );
                        }
                    }
                }
                VoiceEvent::Error { message } => {
                    eprintln!("[Voice Input] {message}");
                    if voice_state == VoiceState::Transcribing {
                        input.set_state(true, false).await?;
                        transition(
                            &mut voice_state,
                            &mut conversation_deadline,
                            VoiceState::Listening,
                            "stt_error",
                            self.conversation_timeout,
                        );
                    }
                }
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
        self.respond_with_mode(input, false).await
    }

    async fn respond_voice(&mut self, input: &str) -> Result<String, Box<dyn Error>> {
        self.respond_with_mode(input, true).await
    }

    async fn respond_with_mode(
        &mut self,
        input: &str,
        voice_mode: bool,
    ) -> Result<String, Box<dyn Error>> {
        if let Some(answer) = guarded_local_answer(input) {
            return Ok(answer.to_owned());
        }
        if looks_like_uncertain_computer_command(input) {
            return Ok("Не розібрав команду, сер. Повторіть.".to_owned());
        }
        self.history.push(Message::user(input));

        for _ in 0..self.max_tool_rounds {
            let context = context_window(&self.history, self.max_context_turns);
            let mut message = self.groq.chat(&context, &self.tools).await?;
            let tool_calls = message.tool_calls.clone().unwrap_or_default();
            let content = message.content.clone();

            if tool_calls.is_empty() {
                let answer = finalize_assistant_response(
                    input,
                    &content.unwrap_or_else(|| "Готово.".to_owned()),
                    voice_mode,
                );
                message.content = Some(answer.clone());
                self.history.push(message);
                prune_history(&mut self.history, self.max_context_turns);
                return Ok(answer);
            }
            self.history.push(message);

            for call in tool_calls {
                let tool_started = unix_ms();
                println!(
                    "[Latency] llm_tool={} tool_started={tool_started}",
                    call.function.name
                );
                let result = self
                    .tools
                    .execute(&call.function.name, &call.function.arguments);
                let tool_done = unix_ms();
                println!(
                    "[Latency] llm_tool={} tool_done={tool_done} tool_total={} ms",
                    call.function.name,
                    tool_done.saturating_sub(tool_started)
                );
                self.history.push(Message::tool(call.id, result));
            }
        }

        Err(io::Error::other("Перевищено ліміт послідовних викликів інструментів").into())
    }
}

fn match_local_control(input: &str) -> Option<LocalControl> {
    let text = normalize_for_guard(input);
    match text.as_str() {
        "завершити розмову" | "закінчити розмову" | "досить" | "на цьому все" => {
            Some(LocalControl::EndSession)
        }
        "іди в sleep mode" | "засни" | "сплячий режим" => {
            Some(LocalControl::SleepAssistant)
        }
        "вимкнись" | "заверши роботу" | "вимкни jarvis" | "вимкни джарвіс" => {
            Some(LocalControl::ShutdownAssistant)
        }
        "вимкни комп ютер" | "вимкни комп'ютер" | "вимкни компьютер" => {
            Some(LocalControl::RequestPower(PowerAction::Shutdown))
        }
        "перезавантаж комп ютер" | "перезавантаж комп'ютер" | "перезавантаж компьютер" => {
            Some(LocalControl::RequestPower(PowerAction::Restart))
        }
        "приспи комп ютер" | "приспи комп'ютер" | "приспи компьютер" => {
            Some(LocalControl::RequestPower(PowerAction::Sleep))
        }
        "так підтверджую" | "підтверджую" | "так виконуй" => {
            Some(LocalControl::Confirm)
        }
        "скасуй" | "ні скасуй" | "відміна" => Some(LocalControl::Cancel),
        _ => None,
    }
}

fn power_confirmation_prompt(action: PowerAction) -> &'static str {
    match action {
        PowerAction::Shutdown => "Підтвердьте вимкнення комп'ютера словами «Так, підтверджую».",
        PowerAction::Restart => {
            "Підтвердьте перезавантаження комп'ютера словами «Так, підтверджую»."
        }
        PowerAction::Sleep => "Підтвердьте присипання комп'ютера словами «Так, підтверджую».",
    }
}

fn execute_power_action(action: PowerAction) -> Result<String, String> {
    let (program, arguments, answer): (&str, &[&str], &str) = match action {
        PowerAction::Shutdown => ("shutdown.exe", &["/s", "/t", "0"], "Вимикаю комп'ютер."),
        PowerAction::Restart => (
            "shutdown.exe",
            &["/r", "/t", "0"],
            "Перезавантажую комп'ютер.",
        ),
        PowerAction::Sleep => (
            "rundll32.exe",
            &["powrprof.dll,SetSuspendState", "0,1,0"],
            "Переводжу комп'ютер у сплячий режим.",
        ),
    };
    Command::new(program)
        .args(arguments)
        .spawn()
        .map(|_| answer.to_owned())
        .map_err(|_| "Не вдалося виконати команду живлення, сер.".to_owned())
}

fn guarded_local_answer(input: &str) -> Option<&'static str> {
    let text = normalize_for_guard(input);
    if matches!(
        text.as_str(),
        "привіт" | "вітаю" | "добрий день" | "добрий вечір"
    ) {
        return Some("Вітаю, сер.");
    }
    if text.contains("як себе")
        || text == "як справи"
        || text == "як ти"
        || text.contains("як почуваєшся")
        || text.contains("як ти почуваєшся")
    {
        return Some("Усі системи працюють штатно, сер.");
    }
    if matches!(text.as_str(), "дякую" | "спасибі") {
        return Some("Будь ласка, сер.");
    }
    if matches!(text.as_str(), "ау" | "ти тут" | "джарвіс" | "джарвис") {
        return Some("Я тут, сер.");
    }
    if matches!(text.as_str(), "ок" | "окей" | "добре" | "ясно") {
        return Some("Зрозумів.");
    }

    let asks_now = [
        "зараз",
        "сьогодні",
        "актуаль",
        "останні",
        "свіжі",
        "що по",
        "які новини",
        "яка погода",
        "який курс",
        "яка ціна",
        "скільки коштує",
        "хто виграв",
        "який рахунок",
    ]
    .iter()
    .any(|marker| text.contains(marker));
    let short_live_query = text.split_whitespace().count() <= 4
        && !["історія", "що таке", "чому", "як працює"]
            .iter()
            .any(|marker| text.contains(marker));
    let requires_live_source = asks_now || short_live_query;

    if text.contains("новин") && requires_live_source {
        return Some("Актуального джерела новин у мене поки немає, сер. Не стану вигадувати.");
    }
    if text.contains("погод") && requires_live_source {
        return Some("Актуального джерела погоди у мене поки немає, сер. Не стану вигадувати.");
    }
    if requires_live_source
        && (text.contains("курс долара")
            || text.contains("курс євро")
            || text.contains("ціна")
            || text.contains("коштує")
            || text.contains("біткоїн")
            || text.contains("bitcoin"))
    {
        return Some(
            "Актуального джерела цін і курсів у мене поки немає, сер. Не стану вигадувати.",
        );
    }
    if requires_live_source
        && (text.contains("рахунок")
            || text.contains("матч")
            || text.contains("турнір")
            || text.contains("виграв"))
    {
        return Some(
            "Актуального спортивного джерела у мене поки немає, сер. Не стану вигадувати.",
        );
    }
    if requires_live_source
        && (text.contains("статус сервіс")
            || text.contains("працює сервіс")
            || text.contains("лежить сервіс"))
    {
        return Some(
            "Актуального джерела статусу сервісів у мене поки немає, сер. Не стану вигадувати.",
        );
    }
    None
}

fn normalize_for_guard(input: &str) -> String {
    input
        .to_lowercase()
        .chars()
        .map(|character| {
            if character.is_alphanumeric() {
                character
            } else {
                ' '
            }
        })
        .collect::<String>()
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
}

fn finalize_assistant_response(input: &str, answer: &str, voice_mode: bool) -> String {
    let answer = replace_legacy_address(answer);
    let answer = remove_generic_outro(&answer);
    let answer = normalize_and_dedupe_short_response(&answer);
    let answer = if is_echo_response(input, &answer) {
        "Потрібне коротке уточнення, сер.".to_owned()
    } else {
        answer
    };
    if voice_mode {
        enforce_voice_budget(input, &answer)
    } else {
        answer
    }
}

fn looks_like_uncertain_computer_command(input: &str) -> bool {
    let text = normalize_for_guard(input);
    let first = text.split_whitespace().next().unwrap_or_default();
    let commandish = [
        "відкрий",
        "відкри",
        "відкрес",
        "відкрей",
        "відкрай",
        "запусти",
        "включи",
        "увімкни",
        "закрий",
        "зупини",
        "вимкни",
        "постав",
        "перемкни",
        "натисни",
        "повернися",
    ];
    commandish
        .iter()
        .any(|verb| bounded_distance(first, verb, 2) <= 2)
}

fn bounded_distance(left: &str, right: &str, limit: usize) -> usize {
    if left.chars().count().abs_diff(right.chars().count()) > limit {
        return limit + 1;
    }
    let right: Vec<_> = right.chars().collect();
    let mut previous: Vec<usize> = (0..=right.len()).collect();
    for (row, a) in left.chars().enumerate() {
        let mut current = vec![row + 1];
        for (column, b) in right.iter().enumerate() {
            current.push(
                (current[column] + 1)
                    .min(previous[column + 1] + 1)
                    .min(previous[column] + usize::from(a != *b)),
            );
        }
        previous = current;
    }
    previous[right.len()]
}

fn context_window(history: &[Message], max_turns: usize) -> Vec<Message> {
    let system = history
        .first()
        .filter(|message| message.role == "system")
        .cloned();
    let user_indices: Vec<_> = history
        .iter()
        .enumerate()
        .filter_map(|(index, message)| (message.role == "user").then_some(index))
        .collect();
    let start = user_indices
        .get(user_indices.len().saturating_sub(max_turns))
        .copied()
        .unwrap_or(1);
    let latest_user = history
        .iter()
        .rposition(|message| message.role == "user")
        .unwrap_or(history.len());
    system
        .into_iter()
        .chain(
            history[start..]
                .iter()
                .enumerate()
                .map(|(offset, message)| {
                    let absolute = start + offset;
                    if message.role == "tool" && absolute < latest_user {
                        compact_tool_message(message)
                    } else {
                        message.clone()
                    }
                }),
        )
        .collect()
}

fn compact_tool_message(message: &Message) -> Message {
    let summary = message
        .content
        .as_deref()
        .and_then(|content| serde_json::from_str::<serde_json::Value>(content).ok())
        .map(|payload| {
            if payload.get("success").and_then(serde_json::Value::as_bool) == Some(true) {
                "{\"success\":true,\"result\":\"completed\"}".to_owned()
            } else {
                "{\"success\":false,\"error\":\"failed\"}".to_owned()
            }
        })
        .unwrap_or_else(|| "{\"success\":true,\"result\":\"completed\"}".to_owned());
    Message::tool(message.tool_call_id.clone().unwrap_or_default(), summary)
}

fn prune_history(history: &mut Vec<Message>, max_turns: usize) {
    *history = context_window(history, max_turns);
}

fn normalize_and_dedupe_short_response(answer: &str) -> String {
    let mut spaced = String::with_capacity(answer.len() + 4);
    let chars: Vec<_> = answer.chars().collect();
    for (index, character) in chars.iter().enumerate() {
        spaced.push(*character);
        if matches!(character, '.' | '!' | '?')
            && chars
                .get(index + 1)
                .is_some_and(|next| !next.is_whitespace())
        {
            spaced.push(' ');
        }
    }
    let mut units = Vec::new();
    let mut start = 0;
    for (index, character) in spaced.char_indices() {
        if matches!(character, '.' | '!' | '?') {
            let unit = spaced[start..=index].trim();
            if !unit.is_empty()
                && units.last().is_none_or(|previous: &&str| {
                    normalize_for_guard(previous) != normalize_for_guard(unit)
                })
            {
                units.push(unit);
            }
            start = index + character.len_utf8();
        }
    }
    let tail = spaced[start..].trim();
    if !tail.is_empty() {
        units.push(tail);
    }
    let mut result = units.join(" ");
    if result.split_whitespace().count() <= 45 {
        result = keep_one_address(&result);
    }
    result
}

fn keep_one_address(answer: &str) -> String {
    let mut result = answer.to_owned();
    let mut seen = false;
    let mut search_from = 0;
    loop {
        let lower = result.to_lowercase();
        let Some(relative) = lower[search_from..].find("сер") else {
            break;
        };
        let start = search_from + relative;
        let end = start + "сер".len();
        let before_ok = start == 0
            || !lower[..start]
                .chars()
                .next_back()
                .is_some_and(char::is_alphanumeric);
        let after_ok = end == lower.len()
            || !lower[end..]
                .chars()
                .next()
                .is_some_and(char::is_alphanumeric);
        if !before_ok || !after_ok {
            search_from = end;
            continue;
        }
        if !seen {
            seen = true;
            search_from = end;
            continue;
        }
        let mut remove_start = start;
        while remove_start > 0 && result[..remove_start].ends_with(' ') {
            remove_start -= 1;
        }
        if remove_start > 0 && result[..remove_start].ends_with(',') {
            remove_start -= 1;
        }
        result.replace_range(remove_start..end, "");
        search_from = remove_start;
    }
    result
        .replace(" ,", ",")
        .replace("  ", " ")
        .trim()
        .to_owned()
}

fn replace_legacy_address(answer: &str) -> String {
    let lower: String = ['п', 'а', 'н', 'е'].into_iter().collect();
    let title: String = ['П', 'а', 'н', 'е'].into_iter().collect();
    answer.replace(&title, "Сер").replace(&lower, "сер")
}

fn remove_generic_outro(answer: &str) -> String {
    let lower = answer.to_lowercase();
    let cut_at = [
        "якщо потрібно",
        "якщо знадоблюся",
        "дайте знати",
        "я поруч",
        "до ваших послуг",
        "чим ще можу допомогти",
        "звертайтесь",
    ]
    .iter()
    .filter_map(|marker| lower.find(marker))
    .min();
    let cleaned = cut_at.map_or(answer, |index| &answer[..index]).trim();
    if cleaned.is_empty() {
        "Готово.".to_owned()
    } else {
        cleaned.to_owned()
    }
}

fn is_echo_response(input: &str, answer: &str) -> bool {
    let input = normalize_for_guard(input);
    let answer_normalized = normalize_for_guard(answer);
    if input.is_empty() || answer_normalized.is_empty() {
        return false;
    }
    if input == answer_normalized {
        return true;
    }
    let answer_is_question = answer.trim_end().ends_with('?');
    let input_words: Vec<_> = input.split_whitespace().collect();
    let answer_words: Vec<_> = answer_normalized.split_whitespace().collect();
    let shared = input_words
        .iter()
        .filter(|word| answer_words.contains(word))
        .count();
    answer_is_question && shared * 5 >= input_words.len() * 4
}

fn enforce_voice_budget(input: &str, answer: &str) -> String {
    let request = normalize_for_guard(input);
    if [
        "детально",
        "докладно",
        "повністю",
        "розгорнуто",
        "усі подробиці",
    ]
    .iter()
    .any(|marker| request.contains(marker))
        || ["небезп", "ризик", "втрата даних", "видал", "перезапис"]
            .iter()
            .any(|marker| answer.to_lowercase().contains(marker))
    {
        return answer.to_owned();
    }

    let mut sentence_ends = 0;
    let mut end_index = answer.len();
    for (index, character) in answer.char_indices() {
        if matches!(character, '.' | '!' | '?') {
            sentence_ends += 1;
            if sentence_ends == 3 {
                end_index = index + character.len_utf8();
                break;
            }
        }
    }
    let concise = answer[..end_index].trim();
    let words: Vec<_> = concise.split_whitespace().collect();
    if words.len() <= 45 {
        return concise.to_owned();
    }
    let mut shortened = words[..40].join(" ");
    while shortened.ends_with([',', ';', ':', '—', '-']) {
        shortened.pop();
    }
    if !shortened.ends_with(['.', '!', '?']) {
        shortened.push('.');
    }
    shortened
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
    stt_first_byte: Option<u64>,
    stt_done: Option<u64>,
    intent_at: u64,
    tool_start: u64,
    tool_done: u64,
    tts_start: Option<u64>,
) {
    let total_end = tts_start.unwrap_or(tool_done);
    println!(
        "[Latency] fast_path=true speech_end->stt_first_byte={} speech_end->stt_done={} stt_done->intent={} intent->tool_start={} tool_start->tool_done={} tool_done->tts_start={} speech_end->tool_start={} total={} ms",
        metric(elapsed_ms(mic_end, stt_first_byte)),
        metric(elapsed_ms(mic_end, stt_done)),
        metric(elapsed_ms(stt_done, Some(intent_at))),
        metric(elapsed_ms(Some(intent_at), Some(tool_start))),
        metric(elapsed_ms(Some(tool_start), Some(tool_done))),
        metric(elapsed_ms(Some(tool_done), tts_start)),
        metric(elapsed_ms(mic_end, Some(tool_start))),
        metric(elapsed_ms(mic_end, Some(total_end))),
    );
    if let Some(speech_to_tool_start) = elapsed_ms(mic_end, Some(tool_start)) {
        record_latency_sample("fast_path", speech_to_tool_start, mic_end, tool_start);
    }
}

fn record_latency_sample(path: &str, latency_ms: u64, speech_end: Option<u64>, tool_start: u64) {
    static FAST_SAMPLES: OnceLock<Mutex<VecDeque<u64>>> = OnceLock::new();
    let samples = FAST_SAMPLES.get_or_init(|| Mutex::new(VecDeque::with_capacity(200)));
    if let Ok(mut values) = samples.lock() {
        if values.len() == 200 {
            values.pop_front();
        }
        values.push_back(latency_ms);
        if values.len() >= 20 && values.len() % 5 == 0 {
            let mut sorted: Vec<_> = values.iter().copied().collect();
            sorted.sort_unstable();
            let median = sorted[sorted.len() / 2];
            let p95 = sorted[((sorted.len() as f64 * 0.95).ceil() as usize)
                .saturating_sub(1)
                .min(sorted.len() - 1)];
            println!(
                "[Benchmark] path={path} samples={} median={median}ms p95={p95}ms",
                sorted.len()
            );
        }
    }
    let _ = fs::create_dir_all("logs");
    if let Ok(mut file) = OpenOptions::new()
        .create(true)
        .append(true)
        .open("logs/latency.jsonl")
    {
        let line = serde_json::json!({
            "path": path,
            "speech_end_unix_ms": speech_end,
            "tool_start_unix_ms": tool_start,
            "speech_end_to_tool_start_ms": latency_ms,
        });
        let _ = writeln!(file, "{line}");
    }
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
        match intent {
            "open_app" => "Не вдалося відкрити програму, сер. Windows її не знайшла.".to_owned(),
            "open_url" => "Не вдалося відкрити сайт, сер.".to_owned(),
            "close_app" => "Не вдалося закрити програму, сер.".to_owned(),
            _ => payload
                .get("error")
                .and_then(serde_json::Value::as_str)
                .unwrap_or("Не вдалося виконати команду.")
                .to_owned(),
        }
    }
}

#[cfg(test)]
mod tests {
    use std::time::{Duration, Instant};

    use super::{
        EMERGENCY_SHUTDOWN_MESSAGE, LocalControl, NORMAL_SHUTDOWN_MESSAGE, PowerAction,
        SYSTEM_PROMPT, VoiceState, clean_wake_word, context_window, fast_answer,
        finalize_assistant_response, guarded_local_answer, listening_deadline,
        looks_like_uncertain_computer_command, match_local_control, transition,
    };
    use crate::core::fast_command::match_fast_command;
    use crate::core::messages::Message;

    #[test]
    fn strips_supported_wake_words() {
        assert_eq!(
            clean_wake_word("Джарвіс, відкрий YouTube"),
            "відкрий YouTube"
        );
        assert_eq!(clean_wake_word("hey jarvis open browser"), "open browser");
        assert_eq!(clean_wake_word("Яка погода?"), "Яка погода?");
    }

    #[test]
    fn transcript_to_fast_path_to_cached_ack_contract() {
        // Integration boundary without live microphone/cloud/tool mutation:
        // Ukrainian transcript -> matcher -> mocked safe tool result -> static ack.
        let transcript = clean_wake_word("Джарвіс, відкрий мені ютуб");
        let command = match_fast_command(&transcript).expect("must bypass the LLM");
        assert_eq!(command.intent, "open_url");
        let tool_result = r#"{"success":true,"result":"mock-safe-tool"}"#;
        assert_eq!(
            fast_answer(command.intent, tool_result, command.acknowledgement),
            command.acknowledgement
        );
        assert_eq!(command.acknowledgement, "YouTube відкрито.");
        assert_eq!(command.acknowledgement_cache, Some("opened"));
    }

    #[test]
    fn current_data_without_source_is_guarded_locally() {
        assert_eq!(
            guarded_local_answer("Що по новинах?"),
            Some("Актуального джерела новин у мене поки немає, сер. Не стану вигадувати.")
        );
        assert!(guarded_local_answer("Новини").is_some());
        assert!(guarded_local_answer("Історія новин України").is_none());
        assert!(guarded_local_answer("Розкажи історію газет").is_none());
    }

    #[test]
    fn system_health_question_is_answered_without_echo() {
        assert_eq!(
            guarded_local_answer("Як себе чувствуєш?"),
            Some("Усі системи працюють штатно, сер.")
        );
    }

    #[test]
    fn trivial_small_talk_is_local() {
        assert_eq!(guarded_local_answer("Дякую."), Some("Будь ласка, сер."));
        assert_eq!(
            guarded_local_answer("Як справи?"),
            Some("Усі системи працюють штатно, сер.")
        );
        assert_eq!(guarded_local_answer("Ти тут?"), Some("Я тут, сер."));
        assert_eq!(guarded_local_answer("Ок"), Some("Зрозумів."));
    }

    #[test]
    fn uncertain_imperative_never_reaches_tool_calling() {
        assert!(looks_like_uncertain_computer_command("відкрес тим"));
        assert!(!looks_like_uncertain_computer_command("розкажи про Steam"));
    }

    #[test]
    fn adjacent_duplicate_sentences_and_addresses_are_removed() {
        assert_eq!(
            finalize_assistant_response("Відкрий Steam", "Steam відкрито.Steam відкрито.", false),
            "Steam відкрито."
        );
        let answer =
            finalize_assistant_response("Як справи?", "Вітаю, сер. У мене все гаразд, сер.", false);
        assert_eq!(answer.matches("сер").count(), 1);
    }

    #[test]
    fn context_keeps_system_and_only_recent_turns() {
        let mut history = vec![Message::system("system")];
        for number in 0..10 {
            history.push(Message::user(format!("user-{number}")));
            history.push(Message::assistant(format!("assistant-{number}")));
        }
        let context = context_window(&history, 4);
        assert_eq!(context.first().unwrap().role, "system");
        assert_eq!(
            context
                .iter()
                .filter(|message| message.role == "user")
                .count(),
            4
        );
        assert!(
            context
                .iter()
                .any(|message| message.content.as_deref() == Some("user-9"))
        );
        assert!(
            !context
                .iter()
                .any(|message| message.content.as_deref() == Some("user-0"))
        );
    }

    #[test]
    fn assistant_controls_are_distinct_from_power_controls() {
        assert_eq!(
            match_local_control("Завершити розмову"),
            Some(LocalControl::EndSession)
        );
        assert_eq!(
            match_local_control("Засни"),
            Some(LocalControl::SleepAssistant)
        );
        assert_eq!(
            match_local_control("Вимкнись"),
            Some(LocalControl::ShutdownAssistant)
        );
        assert_eq!(
            match_local_control("Вимкни комп'ютер"),
            Some(LocalControl::RequestPower(PowerAction::Shutdown))
        );
        assert_ne!(NORMAL_SHUTDOWN_MESSAGE, EMERGENCY_SHUTDOWN_MESSAGE);
        assert!(!NORMAL_SHUTDOWN_MESSAGE.contains("аварійно"));
    }

    #[test]
    fn powershell_launchers_explicitly_use_utf8_without_mojibake_literal() {
        for script in [
            include_str!("../../start.ps1"),
            include_str!("../../status.ps1"),
            include_str!("../../stop.ps1"),
            include_str!("../../test.ps1"),
            include_str!("../../test-and-start.ps1"),
        ] {
            assert!(script.contains("[Console]::OutputEncoding = $utf8"));
            assert!(!script.contains("Р”Р¶"));
        }
    }

    #[test]
    fn cinematic_guard_removes_legacy_address_and_generic_outro() {
        let legacy: String = ['п', 'а', 'н', 'е'].into_iter().collect();
        let outro = ["Як", "що потрібно щось ще, дайте", " знати."].concat();
        let raw = format!("Готово, {legacy}. {outro}");
        let answer = finalize_assistant_response("Відкрий програму", &raw, true);
        assert_eq!(answer, "Готово, сер.");
        assert!(!answer.contains(&legacy));
        assert!(!SYSTEM_PROMPT.contains(&legacy));
    }

    #[test]
    fn voice_budget_caps_unrequested_long_answers() {
        let answer = finalize_assistant_response(
            "Поясни коротко",
            "Перше речення. Друге речення. Третє речення. Четверте речення. П'яте речення.",
            true,
        );
        assert_eq!(answer, "Перше речення. Друге речення. Третє речення.");
    }

    #[test]
    fn idle_timeout_only_exists_while_listening() {
        let timeout = Duration::from_secs(60);
        let activated_at = Instant::now();
        assert!(listening_deadline(VoiceState::Activated, activated_at, timeout).is_none());
        let first_listening = listening_deadline(VoiceState::Listening, activated_at, timeout)
            .expect("listening needs a deadline");
        assert_eq!(first_listening, activated_at + timeout);

        for speaking_secs in [30, 40, 90, 120] {
            let speaking_at = activated_at + Duration::from_secs(5);
            assert!(listening_deadline(VoiceState::Speaking, speaking_at, timeout).is_none());
            let playback_done = speaking_at + Duration::from_secs(speaking_secs);
            let refreshed = listening_deadline(VoiceState::Listening, playback_done, timeout)
                .expect("deadline must restart after playback");
            assert_eq!(refreshed, playback_done + timeout);
            assert!(refreshed > first_listening);
        }

        let mut state = VoiceState::Speaking;
        let mut deadline = None;
        transition(
            &mut state,
            &mut deadline,
            VoiceState::Listening,
            "synthetic_playback_complete",
            timeout,
        );
        assert_eq!(state, VoiceState::Listening);
        let remaining = deadline
            .expect("transition must create a fresh deadline")
            .saturating_duration_since(Instant::now());
        assert!(remaining > timeout - Duration::from_millis(50));
    }
}
