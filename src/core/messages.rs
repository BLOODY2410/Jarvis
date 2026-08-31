use serde::{Deserialize, Serialize};

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Message {
    pub role: String,

    #[serde(skip_serializing_if = "Option::is_none")]
    pub content: Option<String>,

    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_calls: Option<Vec<ToolCall>>,

    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_call_id: Option<String>,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct ToolCall {
    pub id: String,

    #[serde(rename = "type")]
    pub tool_type: String,

    pub function: FunctionCall,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct FunctionCall {
    pub name: String,
    pub arguments: String,
}

impl Message {
    pub fn system(text: impl Into<String>) -> Self {
        Self::text("system", text)
    }

    pub fn user(text: impl Into<String>) -> Self {
        Self::text("user", text)
    }

    #[cfg(test)]
    pub fn assistant(text: impl Into<String>) -> Self {
        Self::text("assistant", text)
    }

    pub fn tool(tool_call_id: impl Into<String>, result: impl Into<String>) -> Self {
        Self {
            role: "tool".to_owned(),
            content: Some(result.into()),
            tool_calls: None,
            tool_call_id: Some(tool_call_id.into()),
        }
    }

    fn text(role: &str, text: impl Into<String>) -> Self {
        Self {
            role: role.to_owned(),
            content: Some(text.into()),
            tool_calls: None,
            tool_call_id: None,
        }
    }
}
