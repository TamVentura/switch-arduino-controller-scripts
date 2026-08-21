import os
import threading


class SwitchGate:
    def __init__(self):
        self._event = threading.Event()
        self._event.set()

    def pause(self):
        self._event.clear()

    def run(self):
        self._event.set()

    def wait(self, timeout=None):
        # Pass through the wait method so users can still wait on it
        return self._event.wait(timeout)

    def toggle_pause(self):
        if self._event.is_set():
            self.pause()
        else:
            self.run()

    @property
    def is_running(self):
        return self._event.is_set()


_pause_event: SwitchGate | None = None

switch_gate = lambda: _get_pause_event()


def _get_pause_event() -> SwitchGate:
    global _pause_event
    if _pause_event is None:
        _pause_event = SwitchGate()
    return _pause_event


def get_file_relative_path(filepath: str):
    return os.path.join(os.path.dirname(__file__), filepath)
