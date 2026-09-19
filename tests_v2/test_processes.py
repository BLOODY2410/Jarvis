from jarvis_v2.processes import process_is_running


def test_current_process_is_detected_without_a_signal() -> None:
    import os

    assert process_is_running(os.getpid())


def test_invalid_pid_is_not_running() -> None:
    assert not process_is_running(-1)
