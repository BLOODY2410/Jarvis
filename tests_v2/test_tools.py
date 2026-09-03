from jarvis_v2.tools import SETTINGS_URIS, ToolRegistry


class FakeBackend:
    def set_volume(self, level):
        from jarvis_v2.models import ToolResult

        return ToolResult(tool="set_volume", success=True, message=f"volume={level}", data={"level": level})

    def adjust_volume(self, delta):
        from jarvis_v2.models import ToolResult

        return ToolResult(tool="adjust_volume", success=True, message=f"delta={delta}")

    def set_mute(self, muted):
        from jarvis_v2.models import ToolResult

        return ToolResult(tool="set_mute", success=True, message=f"muted={muted}")

    def open_app(self, app):
        from jarvis_v2.models import ToolResult

        return ToolResult(tool="open_app", success=True, message=f"dispatched={app}")

    def close_app(self, app):
        from jarvis_v2.models import ToolResult

        return ToolResult(tool="close_app", success=True, message=f"closed={app}")

    def open_url(self, url):
        from jarvis_v2.models import ToolResult

        return ToolResult(tool="open_url", success=True, message=f"url={url}")

    def open_settings(self, page):
        from jarvis_v2.models import ToolResult

        return ToolResult(
            tool="windows_settings", success=page in SETTINGS_URIS, message=SETTINGS_URIS.get(page, "bad")
        )

    def screenshot(self, output_path=None):
        from jarvis_v2.models import ToolResult

        return ToolResult(tool="screenshot", success=False, message="not in unit test")

    def list_apps(self):
        from jarvis_v2.models import ToolResult

        return ToolResult(tool="list_apps", success=True, message="apps")

    def media(self, action):
        from jarvis_v2.models import ToolResult

        return ToolResult(tool="media", success=True, message=action)


def test_tool_arguments_are_validated() -> None:
    registry = ToolRegistry(FakeBackend())
    result = registry.execute("set_volume", {"level": 101})
    assert not result.success
    result = registry.execute("open_url", {"url": "file:///C:/Windows"})
    assert not result.success
    assert not registry.execute("does_not_exist", {}).success


def test_windows_settings_uri_contract() -> None:
    registry = ToolRegistry(FakeBackend())
    result = registry.execute("windows_settings", {"page": "sound"})
    assert result.success
    assert result.message == "ms-settings:sound"
