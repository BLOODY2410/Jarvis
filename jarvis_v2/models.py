from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class Route(StrEnum):
    CONVERSATION = "conversation"
    PC_AGENT = "pc_agent"
    LIVE_CURRENT = "live_current"
    VISION = "vision"


class PersonalityMode(StrEnum):
    ACTION = "ACTION"
    CASUAL = "CASUAL"
    BANTER = "BANTER"
    SERIOUS = "SERIOUS"
    LIVE_INFO = "LIVE_INFO"
    PC_AGENT = "PC_AGENT"


class IntentBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str


class SetVolumeIntent(IntentBase):
    kind: Literal["set_volume"] = "set_volume"
    level: int = Field(ge=0, le=100)


class AdjustVolumeIntent(IntentBase):
    kind: Literal["adjust_volume"] = "adjust_volume"
    delta: int = Field(ge=-100, le=100)


class SetMuteIntent(IntentBase):
    kind: Literal["set_mute"] = "set_mute"
    muted: bool


class OpenAppIntent(IntentBase):
    kind: Literal["open_app"] = "open_app"
    app: str = Field(min_length=1, max_length=80)


class CloseAppIntent(IntentBase):
    kind: Literal["close_app"] = "close_app"
    app: str = Field(min_length=1, max_length=80)


class OpenUrlIntent(IntentBase):
    kind: Literal["open_url"] = "open_url"
    url: str = Field(min_length=8, max_length=2048)


class WindowsSettingsIntent(IntentBase):
    kind: Literal["windows_settings"] = "windows_settings"
    page: Literal[
        "settings",
        "display",
        "sound",
        "network",
        "bluetooth",
        "apps",
        "notifications",
        "privacy",
        "windows_update",
    ]


class ScreenshotIntent(IntentBase):
    kind: Literal["screenshot"] = "screenshot"
    analyze: bool = False


class ListAppsIntent(IntentBase):
    kind: Literal["list_apps"] = "list_apps"


class MediaIntent(IntentBase):
    kind: Literal["media"] = "media"
    action: Literal["play_pause", "next", "previous"]


class PowerIntent(IntentBase):
    kind: Literal["power"] = "power"
    action: Literal["shutdown", "restart", "sleep"]


class UnknownIntent(IntentBase):
    kind: Literal["unknown"] = "unknown"
    reason: str = Field(default="", max_length=200)


Intent = Annotated[
    SetVolumeIntent
    | AdjustVolumeIntent
    | SetMuteIntent
    | OpenAppIntent
    | CloseAppIntent
    | OpenUrlIntent
    | WindowsSettingsIntent
    | ScreenshotIntent
    | ListAppsIntent
    | MediaIntent
    | PowerIntent
    | UnknownIntent,
    Field(discriminator="kind"),
]


class IntentEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    route: Route
    mode: PersonalityMode
    confidence: float = Field(ge=0, le=1)
    intents: list[Intent] = Field(default_factory=list, max_length=8)
    query: str | None = Field(default=None, max_length=2000)
    needs_clarification: bool = False
    clarification: str | None = Field(default=None, max_length=300)


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    arguments: dict[str, object]


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str
    success: bool
    message: str
    data: dict[str, object] = Field(default_factory=dict)


class GroundingSource(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str
    url: str


class ProviderResponse(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    sources: list[GroundingSource] = Field(default_factory=list)
    provider: str = ""
    model: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    fallback_count: int = 0
    raw: object | None = None


class AgentReply(BaseModel):
    text: str
    voice_text: str
    route: Route
    mode: PersonalityMode
    tool_results: list[ToolResult] = Field(default_factory=list)
    sources: list[GroundingSource] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    screenshot: Path | None = None
