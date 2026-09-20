from jarvis_v2.gui import parse_env, update_env


def test_update_env_preserves_comments_and_unrelated_settings() -> None:
    source = "# private\nGEMINI_API_KEY=old\nKEEP=this\n"
    updated = update_env(source, {"GEMINI_API_KEY": "new", "JARVIS_DEBUG": "true"})
    assert updated == "# private\nGEMINI_API_KEY=new\nKEEP=this\nJARVIS_DEBUG=true\n"


def test_parse_env_ignores_comments() -> None:
    assert parse_env("# GEMINI_API_KEY=hidden\nGEMINI_API_KEY=live\n") == {"GEMINI_API_KEY": "live"}
