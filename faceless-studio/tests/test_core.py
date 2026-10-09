import http.server
import json
import subprocess
import threading
from functools import partial
from pathlib import Path

import pytest

from studio import captions, db, llm, pipeline, settings, topics, tts, worker
from studio.visuals import SOURCES, Request, card, mediaserver


def _beat(text, **kw):
    b = {"narration": text, "kind": "auto", "image_prompt": "a dark cell", "stock_query": "cell",
         "movie_title": "", "movie_year": 0, "source_start": -1, "emphasis": False}
    b.update(kw)
    return b


def _script(beats):
    return {"title": "Test video", "description": "d", "hashtags": ["a"], "thumbnail_text": "WOW",
            "character": "", "style": "film still", "format": "timeline_story", "beats": beats}


def _fake_tts(s, text, out, job_id=None):
    dur = max(2.0, len(text.split()) / 2.7)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=f=220:d={dur}", str(out)], check=True)
    return tts._even(text, dur), dur


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(tts, "synthesize", _fake_tts)
    monkeypatch.setitem(SOURCES, "ai_image", [card.Card()])


def test_locate_keeps_punctuation_and_offsets():
    text = "Hour one. The door slams, hard!"
    words = [{"text": t, "start": i, "end": i + 1} for i, t in enumerate(["Hour", "one", "The", "door", "slams", "hard"])]
    out = tts._locate(words, text)
    assert [w["text"] for w in out] == ["Hour", "one.", "The", "door", "slams,", "hard!"]
    assert out[2]["char"] == text.index("The")


def test_beat_times_follow_words():
    beats = [_beat("Hour one."), _beat("The door slams.")]
    text = " ".join(b["narration"] for b in beats)
    words = tts._even(text, 4.0)
    times = pipeline.beat_times(beats, words, 4.0)
    assert times[0][0] == 0.0
    assert times[0][1] == pytest.approx(times[1][0])
    assert times[1][1] == pytest.approx(4.4)


def test_captions_break_on_sentences(tmp_path):
    s = settings.Settings(caption_words=3)
    words = tts._locate([{"text": t, "start": i * .3, "end": i * .3 + .3} for i, t in
                         enumerate(["Hour", "one", "The", "door", "slams"])], "Hour one. The door slams.")
    ass = captions.build(s, words, 1080, 1920, tmp_path / "c.ass").read_text()
    events = [l for l in ass.splitlines() if l.startswith("Dialogue")]
    assert len(events) == 5
    assert "THE" not in events[0] and "HOUR" in events[0]


def test_full_pipeline_offline(offline, monkeypatch):
    script = _script([_beat("Hour one. The cell door slams behind you.", emphasis=True),
                      _beat("Your cellmate has not blinked once."),
                      _beat("Hour six. Dinner wobbles on the tray.")])
    monkeypatch.setattr(llm, "structured", lambda *a, **k: json.loads(json.dumps(script)))
    jid = worker.enqueue("Your last 48 hours", "timeline_story", "autopilot",
                         {"resolution": 360, "max_shot_seconds": 2.0, "music": False})
    pipeline.run(jid)
    j = db.job(jid)
    assert j["status"] == "done", j["log"]
    out = json.loads(j["output_json"])
    for name in ("vertical", "horizontal"):
        p = Path(out["videos"][name])
        assert p.exists()
        from studio import media
        assert media.duration(p) == pytest.approx(media.duration(Path(settings.DATA) / "jobs" / str(jid) / "narration.mp3") + 0.4, abs=0.3)
    meta = json.loads((Path(out["folder"]) / "metadata.json").read_text())
    assert meta["title"] == "Test video"


def test_approval_pauses_then_resumes(offline, monkeypatch):
    monkeypatch.setattr(llm, "structured", lambda *a, **k: _script([_beat("Short one."), _beat("Short two.")]))
    jid = worker.enqueue("t", "what_if", "autopilot", {"require_approval": True, "resolution": 360, "horizontal": False})
    pipeline.run(jid)
    assert db.job(jid)["status"] == "awaiting_approval"
    db.update_job(jid, status="queued")
    pipeline.run(jid)
    assert db.job(jid)["status"] == "done"


def test_footage_narration_uses_source(offline, monkeypatch, tmp_path):
    src = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=d=12:s=640x360:r=25", str(src)], check=True)
    seen = {}

    def fake(s, system, content, schema, **kw):
        if isinstance(content, list):
            seen["images"] = sum(1 for c in content if isinstance(c, dict) and c.get("type") == "image")
        return _script([_beat("First thing.", kind="source", source_start=1.0),
                        _beat("Then this happens.", kind="source", source_start=6.0)])
    monkeypatch.setattr(llm, "structured", fake)
    jid = worker.enqueue("narrate", "footage_narration", "autopilot",
                         {"resolution": 360, "horizontal": False}, source_video=str(src))
    pipeline.run(jid)
    j = db.job(jid)
    assert j["status"] == "done", j["log"]
    assert seen["images"] >= 3
    shots = json.loads((settings.DATA / "jobs" / str(jid) / "shots.json").read_text())
    assert {s["asset"]["source"] for s in shots} == {"source"}


def test_topic_refill_dedupes(monkeypatch):
    s = settings.Settings(trend_research=False)
    out = {"topics": [{"title": "What if the sun went out", "format": "what_if", "angle": "x"},
                      {"title": "what if the sun went out", "format": "what_if", "angle": "dupe"},
                      {"title": "Your first day as a gladiator", "format": "day_in_life", "angle": "y"}]}
    monkeypatch.setattr(llm, "structured", lambda *a, **k: out)
    assert topics.refill(s, 3) == 2
    assert topics.refill(s, 3) == 0


def test_mediaserver_follows_iframe_to_stream(tmp_path):
    root = tmp_path / "site"
    (root / "embed" / "movie" / "603").mkdir(parents=True)
    (root / "files").mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=d=60:s=320x240:r=25",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(root / "files" / "m.mp4")], check=True)
    (root / "embed" / "movie" / "603" / "index.html").write_text('<html><iframe src="/player.html"></iframe></html>')
    (root / "player.html").write_text('<video><source src="/files/m.mp4" type="video/mp4"></video>')
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        s = settings.Settings(mediaserver_enabled=True, mediaserver_base=f"http://127.0.0.1:{srv.server_port}",
                              mediaserver_movie_path="/embed/movie/{year}/")
        beat = _beat("The Matrix.", movie_title="The Matrix", movie_year=603)
        req = Request(beat=beat, index=0, duration=3.0, script=_script([beat]), workdir=tmp_path, job_id=None)
        asset = mediaserver.MediaServer().fetch(s, req)
        assert asset.type == "video" and asset.duration >= 3.0
    finally:
        srv.shutdown()


def test_featherless_writer_repairs_messy_json(monkeypatch):
    replies = [
        "Sure! Here you go: {not json",
        "<think>plan the script</think>\n```json\n" + json.dumps({
            "title": "T", "description": "d", "hashtags": "#jail, #story", "thumbnail_text": "WOW",
            "character": "", "style": "s",
            "beats": [{"narration": "Hour one.", "kind": "weird", "image_prompt": "cell", "movie_year": "1999"}],
        }) + "\n```",
    ]
    seen = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((self.path, self.headers["Authorization"], body))
            out = json.dumps({"choices": [{"message": {"content": replies[len(seen) - 1]}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        from studio import scriptgen
        monkeypatch.setattr(scriptgen, "_fit_length", lambda s, sc, w, j: sc)
        s = settings.Settings(llm_provider="featherless", featherless_api_key="k",
                              featherless_base_url=f"http://127.0.0.1:{srv.server_port}/v1")
        script = scriptgen.write(s, "timeline_story", "Your last 48 hours in jail")
    finally:
        srv.shutdown()
    assert len(seen) == 2  # one repair round-trip
    path, auth, body = seen[0]
    assert path == "/v1/chat/completions" and auth == "Bearer k"
    assert body["model"] == "deepseek-ai/DeepSeek-V3-0324"
    b = script["beats"][0]
    assert script["hashtags"] == ["jail", "story"]
    assert b["kind"] == "auto" and b["movie_year"] == 1999 and b["stock_query"] == "" and b["source_start"] == -1


def test_claude_schema_has_no_defaults():
    from studio import scriptgen
    assert '"default"' not in json.dumps(llm._strip_defaults(scriptgen.SCHEMA))


def test_overlong_script_gets_one_trim_pass(monkeypatch):
    from studio import scriptgen
    long = _script([_beat("word " * 60) for _ in range(5)])
    short = _script([_beat("one two three four five six seven eight.") for _ in range(9)])
    long.pop("format"); short.pop("format")  # real model output has no format field
    calls = []
    monkeypatch.setattr(llm, "structured", lambda *a, **k: calls.append(1) or (long if len(calls) == 1 else short))
    out = scriptgen.write(settings.Settings(target_seconds=30), "timeline_story", "x")
    assert len(calls) == 2 and len(out["beats"]) == 9 and out["format"] == "timeline_story"


def test_tighten_trims_long_pauses_and_shifts_words(tmp_path):
    a = tmp_path / "a.mp3"
    # 1s tone, 1s silence, 1s tone
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "sine=f=300:d=1,apad=pad_dur=1[a];sine=f=300:d=1[b];[a][b]concat=n=2:v=0:a=1",
                    str(a)], check=True)
    words = [{"text": "one", "start": 0.0, "end": 1.0, "char": 0},
             {"text": "two", "start": 2.0, "end": 3.0, "char": 4}]
    out = tts.tighten(a, words, 0.3)
    from studio import media
    assert media.duration(a) == pytest.approx(2.3, abs=0.1)
    assert out[1]["start"] == pytest.approx(1.3, abs=0.01)
    assert out[0]["start"] == 0.0


def test_art_style_is_forced_onto_script(monkeypatch):
    from studio import scriptgen
    sc = _script([_beat("Hello there, friend.")])
    sc["style"] = "photorealistic"
    monkeypatch.setattr(llm, "structured", lambda *a, **k: sc)
    out = scriptgen.write(settings.Settings(art_style="comic"), "timeline_story", "x")
    assert out["style"] == settings.ART_STYLES["comic"]
    req = Request(beat=out["beats"][0], index=0, duration=2, script=out, workdir=Path("."), job_id=1)
    assert req.prompt().startswith("graphic novel comic book art")
