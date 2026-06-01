import json
import os
import threading

os.environ.setdefault("YOLOMUX_CONFIG_DIR", "/tmp/yolomux-test-config")

from yolomux_lib import events


def use_temp_state(monkeypatch, tmp_path):
    monkeypatch.setattr(events, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(events, "STATE_PATH", tmp_path / "state.json")


def test_state_write_uses_unique_temp_file(monkeypatch, tmp_path):
    use_temp_state(monkeypatch, tmp_path)

    events.write_yolomux_state({"notify_enabled": True})

    assert json.loads((tmp_path / "state.json").read_text(encoding="utf-8")) == {"notify_enabled": True}
    assert not (tmp_path / "state.json.tmp").exists()
    assert list(tmp_path.glob(".state.json.*.tmp")) == []


def test_state_updates_are_locked_across_threads(monkeypatch, tmp_path):
    use_temp_state(monkeypatch, tmp_path)
    events.write_yolomux_state({"base": True})
    barrier = threading.Barrier(12)

    def update(index):
        barrier.wait()
        events.update_yolomux_state({f"key_{index}": index})

    threads = [threading.Thread(target=update, args=(index,)) for index in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    state = events.read_yolomux_state()
    assert state["base"] is True
    for index in range(12):
        assert state[f"key_{index}"] == index
