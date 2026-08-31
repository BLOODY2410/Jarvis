use crate::core::messages::Message;

#[derive(Debug, Clone)]
pub struct ConversationContext {
    system_prompt: String,
    messages: Vec<Message>,
    max_turns: usize,
    last_entity: Option<String>,
    session_active: bool,
}

impl ConversationContext {
    pub fn new(system_prompt: impl Into<String>, max_turns: usize) -> Self {
        Self {
            system_prompt: system_prompt.into(),
            messages: vec![],
            max_turns: max_turns.max(1),
            last_entity: None,
            session_active: true,
        }
    }
    pub fn push_user(&mut self, text: impl Into<String>) {
        self.messages.push(Message::user(text));
        self.prune();
    }
    pub fn push_assistant(&mut self, message: Message) {
        self.messages.push(message);
        self.prune();
    }
    pub fn push_tool(&mut self, message: Message) {
        self.messages.push(message);
        self.prune();
    }
    pub fn record_event(&mut self, event: impl Into<String>) {
        let event = event.into();
        self.last_entity = Some(event.clone());
        self.messages.push(Message::system(format!(
            "Коротка подія локального стану: {event}."
        )));
        self.prune();
    }
    pub fn set_session_active(&mut self, active: bool) {
        self.session_active = active;
    }
    pub fn window(&self) -> Vec<Message> {
        let mut result = vec![Message::system(format!(
            "{}\n\nСтан сесії: {}.{}",
            self.system_prompt,
            if self.session_active {
                "активна"
            } else {
                "неактивна"
            },
            self.last_entity
                .as_ref()
                .map(|entity| format!(" Остання сутність: {entity}."))
                .unwrap_or_default()
        ))];
        result.extend(self.messages.clone());
        result
    }
    #[cfg(test)]
    pub fn turn_count(&self) -> usize {
        self.messages
            .iter()
            .filter(|message| message.role == "user")
            .count()
    }
    fn prune(&mut self) {
        let mut users = 0;
        let mut start = self.messages.len();
        for (index, message) in self.messages.iter().enumerate().rev() {
            if message.role == "user" {
                users += 1;
                if users > self.max_turns {
                    start = index + 1;
                    break;
                }
            }
            start = index;
        }
        if users > self.max_turns {
            self.messages.drain(..start);
        }
        while self
            .messages
            .first()
            .is_some_and(|message| message.role == "tool")
        {
            self.messages.remove(0);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::ConversationContext;
    use crate::core::messages::Message;
    #[test]
    fn context_is_shared_and_capped() {
        let mut context = ConversationContext::new("system", 2);
        for number in 0..4 {
            context.push_user(format!("u{number}"));
            context.push_assistant(Message::assistant(format!("a{number}")));
        }
        assert_eq!(context.turn_count(), 2);
        let window = context.window();
        assert!(
            window
                .iter()
                .any(|message| message.content.as_deref() == Some("u3"))
        );
        assert!(
            !window
                .iter()
                .any(|message| message.content.as_deref() == Some("u0"))
        );
    }
}
