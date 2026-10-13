"""Offline tests for scripts/moth.py: HTTP is replaced by a fake."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/moth-quantum/scripts"))
import moth  # noqa: E402


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setenv("MOTH_API_KEY", "moth_test")
    monkeypatch.setattr(moth, "POLL_SECONDS", 0)
    monkeypatch.setattr(moth, "BACKOFF_SECONDS", 0)


def fake_http(responses, calls):
    """Return a _http replacement that pops (status, body) per call and records requests."""
    def _http(method, url, *, body=None, headers=None):
        calls.append({"method": method, "url": url,
                      "body": json.loads(body) if body and url.startswith(moth.API) else body,
                      "headers": headers or {}})
        status, payload = responses.pop(0)
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return status, raw
    return _http


def test_submit_wraps_params_and_input_files(monkeypatch):
    calls = []
    monkeypatch.setattr(moth, "_http", fake_http([(202, {"job_id": "j1", "status": "queued"})], calls))

    job_id = moth.submit("qrc-gen-v2", {"length": 30}, {"state": "job:t1/state"})

    assert job_id == "j1"
    assert calls[0]["url"] == f"{moth.API}/engines/qrc-gen-v2/process"
    assert calls[0]["body"] == {"params": {"length": 30}, "input_files": {"state": "job:t1/state"}}
    assert calls[0]["headers"]["Authorization"] == "Bearer moth_test"


def test_call_raises_problem_details_on_422(monkeypatch):
    problem = {"status": 422, "errors": [{"location": "body.params.shots", "message": "expected integer >= 1"}]}
    monkeypatch.setattr(moth, "_http", fake_http([(422, problem)], []))

    with pytest.raises(moth.MothError) as err:
        moth.call("POST", "/engines/coin-toss-v1/process", {"params": {"shots": 0}})

    assert err.value.status == 422
    assert err.value.body == problem


def test_call_retries_after_429(monkeypatch):
    calls = []
    monkeypatch.setattr(moth, "_http", fake_http([(429, {}), (200, {"user_id": "u1"})], calls))

    assert moth.call("GET", "/me") == {"user_id": "u1"}
    assert len(calls) == 2


def test_call_retries_cloudflare_520(monkeypatch):
    calls = []
    monkeypatch.setattr(moth, "_http", fake_http([(520, b"<html>"), (200, {"ok": True})], calls))

    assert moth.call("GET", "/engines/blur-v1") == {"ok": True}
    assert len(calls) == 2


def test_wait_stops_at_terminal_status(monkeypatch):
    states = [(200, {"status": s}) for s in ("queued", "processing", "fetching", "completed")]
    monkeypatch.setattr(moth, "_http", fake_http(states, []))

    assert moth.wait("j1", quiet=True)["status"] == "completed"


def test_upload_runs_three_steps_with_returned_headers(monkeypatch, tmp_path):
    calls = []
    file = tmp_path / "song.mid"
    file.write_bytes(b"MThd")
    responses = [
        (201, {"asset_id": "a1", "upload": {"url": "https://storage.test/put",
                                            "headers": {"Content-Type": "audio/midi"}}}),
        (200, b""),
        (200, {"status": "uploaded"}),
    ]
    monkeypatch.setattr(moth, "_http", fake_http(responses, calls))

    assert moth.upload(file) == "a1"
    assert calls[0]["body"] == {"filename": "song.mid", "content_type": "audio/midi", "size_bytes": 4}
    assert calls[1]["method"] == "PUT" and calls[1]["headers"] == {"Content-Type": "audio/midi"}
    assert "Authorization" not in calls[1]["headers"]
    assert calls[2]["url"] == f"{moth.API}/assets/a1/complete"


def test_result_downloads_outputs_without_auth(monkeypatch, tmp_path):
    calls = []
    res = {"outputs": [{"slot": "result", "filename": "out.png", "url": "https://storage.test/get"}],
           "result": None}
    monkeypatch.setattr(moth, "_http", fake_http([(200, res), (200, b"PNG")], calls))

    moth.result("j1", tmp_path)

    assert (tmp_path / "out.png").read_bytes() == b"PNG"
    assert "Authorization" not in calls[1]["headers"]


def test_check_slots_rejects_unknown_and_missing():
    definition = {"input_files": [{"name": "image", "required": True}, {"name": "mask", "required": False}]}

    moth._check_slots(definition, {"image": None, "mask": None})
    with pytest.raises(SystemExit, match="unknown input slot"):
        moth._check_slots(definition, {"img": None, "image": None})
    with pytest.raises(SystemExit, match="missing required"):
        moth._check_slots(definition, {"mask": None})


def test_http_sends_own_user_agent(monkeypatch):
    seen = {}

    class Resp:
        status = 200
        def read(self):
            return b"{}"
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False

    def fake_urlopen(req, timeout):
        seen["ua"] = req.get_header("User-agent")
        return Resp()

    monkeypatch.setattr(moth.urllib.request, "urlopen", fake_urlopen)
    moth._http("GET", f"{moth.API}/me")

    assert seen["ua"] == moth.USER_AGENT


def test_parse_files_keeps_references_and_ids(tmp_path):
    asset = "9f8e7d6c-5b4a-4d21-8abc-def012345678"
    files = moth._parse_files(["state=job:t1/state", f"image={asset}"], dry_run=True)

    assert files == {"state": "job:t1/state", "image": asset}


def test_parse_files_rejects_missing_file():
    with pytest.raises(SystemExit, match="is not a file"):
        moth._parse_files(["image=no-such-photo.png"], dry_run=True)
