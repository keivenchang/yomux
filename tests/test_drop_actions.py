from yolomux_lib import drop_actions
from yolomux_lib import filesystem


def test_drop_action_server_head_and_info_use_validated_files(monkeypatch, tmp_path):
    monkeypatch.setenv(filesystem.FS_ROOTS_ENV, str(tmp_path))
    path = tmp_path / "app.log"
    path.write_text("first\nsecond\n", encoding="utf-8")

    preview, preview_status = drop_actions.run_drop_action({"action": "server-head", "paths": [str(path)]})
    info, info_status = drop_actions.run_drop_action({"action": "server-info", "paths": [str(path)]})

    assert preview_status == 200
    assert preview["title"] == "File preview"
    assert "first\nsecond" in preview["body"]
    assert preview["result"] == {
        "title_key": "drop.result.title.filePreview",
        "title_params": {},
        "blocks": [{"path": str(path), "sections": [[{"raw": "first\nsecond"}]]}],
    }
    assert info_status == 200
    assert f"path: {path}" in info["body"]
    assert "kind: file" in info["body"]
    assert info["result"]["title_key"] == "drop.result.title.fileInformation"
    assert info["result"]["blocks"][0]["sections"][0][:2] == [
        {"key": "drop.result.info.path", "params": {"path": str(path)}, "fallback": ""},
        {"key": "drop.result.info.kind", "params": {"kind": "file"}, "fallback": ""},
    ]


def test_drop_action_server_log_errors_counts_warning_tokens(monkeypatch, tmp_path):
    monkeypatch.setenv(filesystem.FS_ROOTS_ENV, str(tmp_path))
    path = tmp_path / "server.log"
    path.write_text("ok\nWARN slow request\nTraceback follows\nfatal: failed\n", encoding="utf-8")

    result, status = drop_actions.run_drop_action({"action": "server-log-errors", "paths": [str(path)]})

    assert status == 200
    assert result["title"] == "Log errors and warnings"
    assert "summary:" in result["body"]
    assert "WARN slow request" in result["body"]
    assert "fatal: failed" in result["body"]
    assert result["result"]["title_key"] == "drop.result.title.logErrors"
    assert result["result"]["blocks"][0] == {
        "path": str(path),
        "sections": [
            [{"key": "drop.result.log.summary", "params": {"summary": "warn=1, traceback=1, failed=1, fatal=1"}, "fallback": ""}],
            [
                {"raw": "2: WARN slow request"},
                {"raw": "3: Traceback follows"},
                {"raw": "4: fatal: failed"},
            ],
        ],
    }


def test_drop_action_server_data_stats_handles_csv_and_json(monkeypatch, tmp_path):
    monkeypatch.setenv(filesystem.FS_ROOTS_ENV, str(tmp_path))
    csv_path = tmp_path / "data.csv"
    json_path = tmp_path / "data.json"
    csv_path.write_text("name,count\nalpha,2\nbeta,4\n,6\n", encoding="utf-8")
    json_path.write_text('[{"kind": "a"}, {"kind": "b", "count": 2}]', encoding="utf-8")

    csv_result, csv_status = drop_actions.run_drop_action({"action": "server-data-stats", "paths": [str(csv_path)]})
    json_result, json_status = drop_actions.run_drop_action({"action": "server-data-stats", "paths": [str(json_path)]})

    assert csv_status == 200
    assert "rows scanned: 3" in csv_result["body"]
    assert "count: numeric count=3" in csv_result["body"]
    assert "chart:" in csv_result["body"]
    assert csv_result["result"]["title_key"] == "drop.result.title.dataStats"
    csv_messages = csv_result["result"]["blocks"][0]["sections"][0]
    assert csv_messages[0] == {"key": "drop.result.data.rowsScanned", "params": {"count": 3}, "fallback": ""}
    assert any(message["key"] == "drop.result.data.numericField" for message in csv_messages)
    assert json_status == 200
    assert "type: JSON array" in json_result["body"]
    assert "object keys:" in json_result["body"]
    assert json_result["result"]["blocks"][0]["sections"][0] == [
        {"key": "drop.result.data.typeJsonArray", "params": {}, "fallback": ""},
        {"key": "drop.result.data.items", "params": {"count": 2}, "fallback": ""},
        {"key": "drop.result.data.objectKeys", "params": {"keys": "kind(2), count(1)"}, "fallback": ""},
    ]


def test_drop_action_server_ocr_reports_unavailable_without_tesseract(monkeypatch, tmp_path):
    monkeypatch.setenv(filesystem.FS_ROOTS_ENV, str(tmp_path))
    monkeypatch.setattr(drop_actions.shutil, "which", lambda _name: None)
    path = tmp_path / "shot.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n")

    result, status = drop_actions.run_drop_action({"action": "server-ocr", "paths": [str(path)]})

    assert status == 200
    assert result["unavailable"] is True
    assert "tesseract" in result["body"]
    assert result["result"] == {
        "title_key": "drop.result.title.ocrUnavailable",
        "title_params": {},
        "blocks": [{
            "path": "",
            "sections": [[{
                "key": "drop.result.ocr.unavailable",
                "params": {"executable": "tesseract"},
                "fallback": "",
            }]],
        }],
    }


def test_drop_action_rejects_missing_and_unknown_actions(monkeypatch, tmp_path):
    monkeypatch.setenv(filesystem.FS_ROOTS_ENV, str(tmp_path))
    path = tmp_path / "file.txt"
    path.write_text("hello", encoding="utf-8")

    missing_action, missing_action_status = drop_actions.run_drop_action({"paths": [str(path)]})
    missing_path, missing_path_status = drop_actions.run_drop_action({"action": "server-info"})
    unknown, unknown_status = drop_actions.run_drop_action({"action": "server-nope", "paths": [str(path)]})

    assert missing_action_status == 400
    assert missing_action["error"] == "action is required"
    assert missing_action["user_message"] == {
        "key": "drop.error.actionRequired",
        "params": {},
        "fallback": "action is required",
    }
    assert missing_path_status == 400
    assert missing_path["error"] == "path is required"
    assert missing_path["user_message"]["key"] == "drop.error.pathRequired"
    assert unknown_status == 400
    assert unknown["error"] == "unknown action: server-nope"
    assert unknown["user_message"] == {
        "key": "drop.error.unknownAction",
        "params": {"action": "server-nope"},
        "fallback": "unknown action: server-nope",
    }
