"""fal.ai: paid AI images (Flux) and image-to-video (Kling etc.) via the queue API.

Model ids are settings, so swapping to a newer model is a settings change.
"""
from __future__ import annotations

import base64
import time

from .. import db, media, net
from . import Asset, Request

IMAGE_USD = 0.004
VIDEO_USD = 0.35


def _run(key: str, model: str, payload: dict, timeout: float = 900) -> dict:
    headers = {"Authorization": f"Key {key}"}
    with net.client(60) as c:
        r = c.post(f"https://queue.fal.run/{model}", headers=headers, json=payload)
        r.raise_for_status()
        sub = r.json()
        status_url, response_url = sub["status_url"], sub["response_url"]
        deadline = time.time() + timeout
        while time.time() < deadline:
            st = c.get(status_url, headers=headers).json()
            if st.get("status") == "COMPLETED":
                res = c.get(response_url, headers=headers)
                res.raise_for_status()
                return res.json()
            if st.get("status") in ("FAILED", "ERROR"):
                raise RuntimeError(f"fal job failed: {st}")
            time.sleep(3)
    raise TimeoutError(f"fal {model} timed out")


class FalImage:
    name = "fal_image"
    paid = True

    def available(self, s, req) -> bool:
        return s.image_provider == "fal" and bool(s.secret("fal_key"))

    def estimate(self, s, req) -> float:
        return IMAGE_USD

    def fetch(self, s, req: Request) -> Asset:
        out = _run(s.secret("fal_key"), s.fal_image_model, {
            "prompt": req.prompt(), "image_size": {"width": 1280, "height": 1280}, "num_images": 1,
        })
        dest = req.workdir / f"beat{req.index:03d}_v{req.variant}_fal.jpg"
        net.download(out["images"][0]["url"], dest)
        db.record_spend(req.job_id, "fal", IMAGE_USD, s.fal_image_model)
        return Asset(dest, "image", source=self.name)


class FalVideo:
    name = "fal_video"
    paid = True

    def available(self, s, req) -> bool:
        return s.video_provider == "fal" and bool(s.secret("fal_key"))

    def estimate(self, s, req) -> float:
        return VIDEO_USD + IMAGE_USD

    def fetch(self, s, req: Request) -> Asset:
        # Make a still first (best available image source), then animate it
        from . import SOURCES
        still = None
        for src in SOURCES["ai_image"]:
            if src.available(s, req):
                still = src.fetch(s, req)
                break
        if still is None:
            raise RuntimeError("no image source for image-to-video")
        data_uri = "data:image/jpeg;base64," + base64.b64encode(still.path.read_bytes()).decode()
        out = _run(s.secret("fal_key"), s.fal_video_model, {
            "prompt": req.beat["narration"] + ". " + req.beat["image_prompt"],
            "image_url": data_uri, "duration": "5",
        })
        dest = req.workdir / f"beat{req.index:03d}_falvid.mp4"
        net.download(out["video"]["url"], dest)
        db.record_spend(req.job_id, "fal", VIDEO_USD, s.fal_video_model)
        return Asset(dest, "video", duration=media.duration(dest), source=self.name,
                     meta={"still": str(still.path)})
