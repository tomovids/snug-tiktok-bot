"""Google Gemini: Nano Banana images (now, or half price through the batch service) and text.

Batch jobs take minutes to hours (up to 24 h), so the bot asks for a day's pictures well before they're
needed; anything not back in time is made straight away at the normal price.
"""
from __future__ import annotations

import base64
import io
import json
import re
import time

import requests
from PIL import Image

BASE = "https://generativelanguage.googleapis.com/v1beta"
DONE_STATES = {"BATCH_STATE_SUCCEEDED", "BATCH_STATE_FAILED", "BATCH_STATE_CANCELLED", "BATCH_STATE_EXPIRED"}


class GeminiError(Exception):
    pass


def image_part(img: Image.Image, max_side: int = 1344) -> dict:
    img = img.convert("RGB")
    if max(img.size) > max_side:
        img = img.copy()
        img.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode()}}


def image_request(prompt: str, base: Image.Image | None = None, aspect: str = "9:16") -> dict:
    parts = ([image_part(base)] if base is not None else []) + [{"text": prompt}]
    return {"contents": [{"parts": parts}],
            "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": aspect}}}


def image_from(response: dict) -> Image.Image:
    for cand in response.get("candidates") or []:
        for p in (cand.get("content") or {}).get("parts") or []:
            inline = p.get("inlineData") or p.get("inline_data")
            if inline and inline.get("data"):
                return Image.open(io.BytesIO(base64.b64decode(inline["data"]))).convert("RGB")
    reason = (response.get("promptFeedback") or {}).get("blockReason") or \
        ((response.get("candidates") or [{}])[0].get("finishReason"))
    raise GeminiError(f"no image in Gemini's answer ({reason or 'no reason given'})")


def text_from(response: dict) -> str:
    parts = ((response.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts)


def parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise GeminiError(f"no JSON in Gemini's answer: {text[:200]}")
    return json.loads(m.group(0))


class Gemini:
    def __init__(self, key: str, image_model: str, text_model: str, session: requests.Session | None = None,
                 sleep=time.sleep):
        self.key, self.image_model, self.text_model = key, image_model, text_model
        self.http = session or requests.Session()
        self.sleep = sleep
        self.usage = {"images": 0, "batch_images": 0, "text": 0}

    def _post(self, url: str, body: dict, tries: int = 4) -> dict:
        for attempt in range(tries):
            try:
                r = self.http.post(url, headers={"x-goog-api-key": self.key}, json=body, timeout=240)
            except requests.RequestException as e:
                err = GeminiError(f"Gemini didn't answer: {e}")
            else:
                if r.ok:
                    return r.json()
                err = GeminiError(f"Gemini {r.status_code}: {r.text[:300]}")
                if r.status_code not in (429, 500, 502, 503, 504):
                    raise err
            if attempt < tries - 1:
                self.sleep(15 * (attempt + 1))
        raise err

    # ---- right now -------------------------------------------------------------------------------

    def image(self, prompt: str, base: Image.Image | None = None) -> Image.Image:
        data = self._post(f"{BASE}/models/{self.image_model}:generateContent", image_request(prompt, base))
        self.usage["images"] += 1
        return image_from(data)

    def text_json(self, prompt: str, images: list[Image.Image] | None = None, system: str = "") -> dict:
        parts = [image_part(i, 1024) for i in images or []] + [{"text": prompt}]
        body = {"contents": [{"parts": parts}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 1.0}}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        data = self._post(f"{BASE}/models/{self.text_model}:generateContent", body)
        self.usage["text"] += 1
        return parse_json(text_from(data))

    # ---- half price, later -----------------------------------------------------------------------

    def batch_submit(self, requests_by_key: dict[str, dict], name: str) -> str:
        body = {"batch": {"display_name": name, "input_config": {"requests": {"requests": [
            {"request": req, "metadata": {"key": key}} for key, req in requests_by_key.items()]}}}}
        data = self._post(f"{BASE}/models/{self.image_model}:batchGenerateContent", body)
        self.usage["batch_images"] += len(requests_by_key)
        return data["name"]

    def batch_get(self, job: str) -> tuple[str, dict[str, dict]]:
        """(state, {key: response or {"error": ...}}); the results are empty until the job is done."""
        r = self.http.get(f"{BASE}/{job}", headers={"x-goog-api-key": self.key}, timeout=120)
        if not r.ok:
            raise GeminiError(f"Gemini {r.status_code}: {r.text[:300]}")
        op = r.json()
        state = (op.get("metadata") or {}).get("state") or op.get("state") or ""
        if op.get("done") and not state:
            state = "BATCH_STATE_SUCCEEDED" if "response" in op else "BATCH_STATE_FAILED"
        results = {}
        for item in _inlined(op):
            key = (item.get("metadata") or {}).get("key")
            if key:
                results[key] = item.get("response") or {"error": item.get("error") or "no response"}
        return state, results

    def batch_cancel(self, job: str) -> None:
        self.http.post(f"{BASE}/{job}:cancel", headers={"x-goog-api-key": self.key}, timeout=60)


def _inlined(op: dict) -> list[dict]:
    """The per-request results, wherever this API version puts them."""
    for holder in (op.get("response") or {}, (op.get("metadata") or {}).get("output") or {}, op.get("output") or {}):
        inl = holder.get("inlinedResponses")
        if isinstance(inl, dict):
            inl = inl.get("inlinedResponses")
        if isinstance(inl, list):
            return inl
    return []
