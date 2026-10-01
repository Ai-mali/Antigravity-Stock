"""Vision scan engine for the AC Stock Tracker backend.

UI-free port of the extractor's provider calls: Gemini, Alibaba (Qwen-VL),
ChatGPT and Claude vision APIs, each with a per-provider key list and
automatic failover to the next key on quota/5xx errors, plus the serial
parsing rules (dash ranges expand only when the count matches Qty, flagged
rows for review).

Config lives in ~/.model_serial_extractor.json so keys saved by the old
desktop app carry over.
"""

import base64
import json
import re
import urllib.request
from pathlib import Path

CONFIG_PATH = Path.home() / ".model_serial_extractor.json"

PROVIDERS = {
    "gemini": {
        "label": "Gemini",
        "models": ["gemini-flash-latest", "gemini-2.5-flash",
                   "gemini-2.0-flash", "gemini-3.5-flash"],
        "default": "gemini-flash-latest",
        "url": None,  # uses the google-genai SDK
    },
    "alibaba": {
        "label": "Alibaba Qwen-VL",
        "models": ["qwen-vl-max", "qwen-vl-plus"],
        "default": "qwen-vl-max",
        "url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/"
               "chat/completions",
    },
    "openai": {
        "label": "OpenAI",
        "models": ["gpt-4o-mini", "gpt-4o"],
        "default": "gpt-4o-mini",
        "url": "https://api.openai.com/v1/chat/completions",
    },
    "anthropic": {
        "label": "Anthropic Claude",
        "models": ["claude-sonnet-4-5", "claude-haiku-4-5"],
        "default": "claude-sonnet-4-5",
        "url": "https://api.anthropic.com/v1/messages",
    },
    "deepseek": {
        "label": "DeepSeek",
        "models": ["deepseek-v3", "deepseek-r1"],
        "default": "deepseek-v3",
        "url": "https://api.deepseek.com/chat/completions",
    },
}

PROMPT = """Extract model numbers and serial numbers from this Daikin equipment parts list.
No = the line item number in the leftmost No column.
Model = equipment model code from the Material column (e.g. BCR50FF, FTKU60AV1F).
Serial = every serial number after "Serial No:" — copy each one exactly as
printed, comma-separated. Keep ranges as written, e.g. "K016634 - K016636".
Desc = the description text of the line item.
Qty = quantity number from the Quantity column.
Rules:
- Each "Serial No:" block belongs ONLY to the row it is printed under.
  NEVER merge serials from different rows and never move serials to a
  neighboring row, even if two blocks look similar.
- If a serial block's row has no Material/model code (blank cell, cut off,
  or continued from a previous page), return it as its OWN row with model
  left EMPTY — do not attach those serials to another model.
- If a row has a model but no Serial No, return it with serial empty and
  put its description text in desc.
- If a row has a model AND a description AND a Serial No block, put the
  description in desc and the serials in serial.
- Do not invent or drop serials: copy every one that is printed.
- Serials are codes containing digits (e.g. K000361, E030850). Words
  without digits (DAIKIN, REFNET, JOINT, KIT, WIFI, CASSETTE, MULTI,
  VRV) are description text — put them in desc, NEVER in serial.
- Ignore handwritten checkmarks, ticks, and pen annotations.
Return ONLY a JSON array, one object per row, including rows without serials:
[{"no":"","model":"","serial":"","qty":"","desc":""}]
"""

_RANGE_RE = re.compile(
    r"([A-Za-z]{0,4})(\d{3,})\s*[-–]\s*([A-Za-z]{0,4})(\d{3,})")
# Short-digit ranges (K1 - K5, A10 - A12): tried only as a fallback after
# the strict pattern — real serials like K7Z-88123 never match because the
# character before the dash is a letter.
_RANGE_SHORT_RE = re.compile(
    r"([A-Za-z]{0,4})(\d{1,2})\s*[-–]\s*([A-Za-z]{0,4})(\d{1,2})")

_MIME_BY_EXT = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                "webp": "image/webp", "bmp": "image/bmp"}


# ------------------------------------------------------------- config
def load_providers() -> dict:
    """Return {provider: {"keys": [{key, remark}], "model": str}}."""
    out = {p: {"keys": [], "model": PROVIDERS[p]["default"]}
           for p in PROVIDERS}
    try:
        data = json.loads(CONFIG_PATH.read_text())
    except Exception:
        data = {}
    saved = data.get("providers")
    if isinstance(saved, dict):
        for p, cfg in saved.items():
            if p in out and isinstance(cfg, dict):
                if isinstance(cfg.get("keys"), list):
                    out[p]["keys"] = [k for k in cfg["keys"] if k]
                if cfg.get("model"):
                    out[p]["model"] = cfg["model"]
    # migrate legacy key fields from the desktop extractor builds
    for eng in ("gemini", "alibaba"):
        legacy = data.get(f"{eng}_keys")
        if isinstance(legacy, list):
            out[eng]["keys"] += [k for k in legacy if k]
    flat = data.get("api_keys")
    if isinstance(flat, list):
        out["gemini"]["keys"] += [k for k in flat if k]
    old = data.get("api_key", "")
    if old:
        out["gemini"]["keys"].append(old)
    for p in out:
        seen = []
        for k in out[p]["keys"]:
            seen.append(k if isinstance(k, dict) else {"key": k,
                                                       "remark": ""})
        dedup = {}
        for entry in seen:
            if entry.get("key"):
                dedup[entry["key"]] = entry
        out[p]["keys"] = list(dedup.values())
    return out


def save_providers(providers: dict, active: dict | None = None) -> None:
    data = {}
    try:
        data = json.loads(CONFIG_PATH.read_text())
    except Exception:
        pass
    data["providers"] = providers
    if active is not None:
        data["active"] = active
    try:
        CONFIG_PATH.write_text(json.dumps(data))
    except Exception:
        pass


def load_active(providers: dict) -> dict:
    """Active provider/model — saved choice, else first provider with a key."""
    try:
        data = json.loads(CONFIG_PATH.read_text())
        a = data.get("active") or {}
        p, m = a.get("provider"), a.get("model")
        if p in providers:
            return {"provider": p,
                    "model": m or providers[p]["model"]}
    except Exception:
        pass
    for p, cfg in providers.items():
        if cfg["keys"]:
            return {"provider": p, "model": cfg["model"]}
    return {"provider": "gemini", "model": providers["gemini"]["model"]}


def mask_key(key: str) -> str:
    return key[:6] + "..." + key[-4:] if len(key) > 10 else key


def list_models_detailed(provider: str, key: str = None) -> dict:
    """Fetch the live model list from the provider API with smart fast & free filtering.
    Returns dict: {"ok": bool, "models": list[str], "recommended": list[str], "other": list[str], "error": str|None}
    """
    static = PROVIDERS.get(provider, {}).get("models", [])
    if not key:
        keys = load_providers().get(provider, {}).get("keys", [])
        if not keys:
            return {
                "ok": False,
                "models": static,
                "recommended": static,
                "other": [],
                "error": f"No API key registered for {PROVIDERS.get(provider, {}).get('label', provider)}. Please register a key first."
            }
        key = keys[0]["key"]

    try:
        if provider == "gemini":
            url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
            req = urllib.request.Request(url, headers={"User-Agent": "ACStockTracker/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = json.loads(resp.read().decode())
            
            raw_models = body.get("models", [])
            valid_models = []
            
            # Non-OCR/specialized keywords to exclude (TTS, audio transcribe, music, robotics, computer-use, deep research, etc.)
            excluded_keywords = [
                "embedding", "aqa", "imagen", "learnlm", "bison", "gecko", "gemini-1.0",
                "gemini-pro-vision", "tts", "transcribe", "lyria", "veo", "robotics",
                "computer-use", "deep-research", "antigravity", "gemma", "banana"
            ]

            for m in raw_models:
                m_name = m.get("name", "").split("/")[-1]
                methods = m.get("supportedGenerationMethods", [])
                if "generateContent" not in methods:
                    continue
                if not m_name.startswith("gemini-"):
                    continue
                lower = m_name.lower()
                if any(bad in lower for bad in excluded_keywords):
                    continue
                valid_models.append(m_name)
            
            # Categorize: Fast & Free Flash OCR models vs Pro/High-Accuracy Multimodal
            recommended = [m for m in valid_models if "flash" in m.lower() and "image" not in m.lower()]
            other = [m for m in valid_models if m not in recommended]
            
            # Prioritize standard fast & free flash releases for OCR
            def flash_rank(m):
                m_low = m.lower()
                if "2.5-flash" in m_low and "lite" not in m_low: return 0
                if "2.0-flash" in m_low and "lite" not in m_low: return 1
                if "1.5-flash" in m_low and "8b" not in m_low: return 2
                if m_low == "gemini-flash-latest": return 3
                if "2.5-flash-lite" in m_low: return 4
                if "2.0-flash-lite" in m_low: return 5
                if m_low == "gemini-flash-lite-latest": return 6
                if "3.5-flash" in m_low and "lite" not in m_low: return 7
                if "3-flash" in m_low: return 8
                return 20
            recommended.sort(key=flash_rank)
            
            combined = recommended + other if (recommended or other) else static
            return {
                "ok": True,
                "models": combined,
                "recommended": recommended,
                "other": other,
                "error": None
            }

        if provider == "anthropic":
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/models",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01", "User-Agent": "ACStockTracker/1.0"}
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = json.loads(resp.read().decode())
            ids = [m.get("id", "") for m in body.get("data", []) if m.get("id", "").startswith("claude")]
            recommended = [i for i in ids if "haiku" in i.lower()]
            other = [i for i in ids if i not in recommended]
            combined = recommended + other if ids else static
            return {"ok": True, "models": combined, "recommended": recommended, "other": other, "error": None}

        # OpenAI compatible
        url_base = PROVIDERS.get(provider, {}).get("url")
        if not url_base:
            return {"ok": True, "models": static, "recommended": static, "other": [], "error": None}
        base = url_base.rsplit("/chat/completions", 1)[0]
        req = urllib.request.Request(base + "/models", headers={"Authorization": f"Bearer {key}", "User-Agent": "ACStockTracker/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode())
        ids = [m.get("id", "") for m in body.get("data", []) if m.get("id")]
        
        if provider == "alibaba":
            valid = [i for i in ids if "vl" in i.lower()]
            recommended = [i for i in valid if any(f in i.lower() for f in ["flash", "plus", "7b", "max"])]
            other = [i for i in valid if i not in recommended]
            combined = recommended + other if valid else static
            return {"ok": True, "models": combined, "recommended": recommended, "other": other, "error": None}
        elif provider == "openai":
            valid = [i for i in ids if i.startswith("gpt-4") or i.startswith("gpt-4o")]
            recommended = [i for i in valid if "mini" in i.lower()]
            other = [i for i in valid if i not in recommended]
            combined = recommended + other if valid else static
            return {"ok": True, "models": combined, "recommended": recommended, "other": other, "error": None}
        elif provider == "deepseek":
            valid = [i for i in ids if "deepseek" in i.lower()]
            return {"ok": True, "models": valid or static, "recommended": valid or static, "other": [], "error": None}

        return {"ok": True, "models": ids or static, "recommended": ids or static, "other": [], "error": None}

    except Exception as ex:
        err_msg = str(ex)
        return {
            "ok": False,
            "models": static,
            "recommended": static,
            "other": [],
            "error": f"Live fetch failed: {err_msg}"
        }


def list_models(provider: str) -> list[str]:
    """Backward-compatible helper returning simple list of models."""
    res = list_models_detailed(provider)
    return res.get("models", [])


# ------------------------------------------------------------- parsing
def expand_serial_range(text: str) -> str:
    """Expand inclusive ranges like 'K016634 - K016636' into serials.

    Same-prefix ranges only; anything else is returned unchanged.
    """
    def _sub(m):
        pa, na, pb, nb = m.group(1), m.group(2), m.group(3), m.group(4)
        pb = pb or pa  # shorthand: 'AB12-15' means 'AB12 - AB15'
        if pa != pb or len(na) != len(nb):
            return m.group(0)
        start, end = int(na), int(nb)
        if end < start or end - start > 500:
            return m.group(0)
        return ", ".join(f"{pa}{i:0{len(na)}d}"
                         for i in range(start, end + 1))

    expanded = _RANGE_RE.sub(_sub, text)
    return _RANGE_SHORT_RE.sub(_sub, expanded)


def _split_ranges(text: str) -> str:
    """Treat dashes as separators — 'A123 - A125' becomes 'A123, A125'.

    When the right side omits the letters ('AB12-13'), it inherits the
    left prefix ('AB12, AB13') rather than emitting a bare number.
    """
    def _sub(m):
        pa, na, pb, nb = m.group(1), m.group(2), m.group(3), m.group(4)
        return f"{pa}{na}, {pb or pa}{nb}"
    out = _RANGE_RE.sub(_sub, text)
    return _RANGE_SHORT_RE.sub(_sub, out)


def _serial_count(serial: str) -> int:
    return len([s for s in serial.split(",") if s.strip()])


def _qty_int(qty: str):
    try:
        return int(re.sub(r"[^0-9]", "", qty) or 0)
    except ValueError:
        return 0


def parse_scan_json(raw: str) -> list[dict]:
    """Pull the JSON array out of a model response (handles markdown fences)."""
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("no JSON array found in response")
    data = json.loads(text[start: end + 1])
    if not isinstance(data, list):
        raise ValueError("response is not a JSON array")
    rows = []
    for item in data:
        if not isinstance(item, dict):
            continue
        model = str(item.get("model", "")).strip()
        raw_serial = str(item.get("serial", "")).strip()
        desc = str(item.get("desc", "")).strip()
        no = str(item.get("no", "")).strip()
        qty = str(item.get("qty", "")).strip()
        # keep rows that have a model OR serials — a model cut off from a
        # previous page still gets its serials, flagged for review
        if not (model or raw_serial or desc):
            continue
        serial, flag = _resolve_serials(raw_serial, qty)
        if not raw_serial:
            flag = "no_serial"
        if not model:
            flag = flag or "no_model"
        serials = [s.strip().upper() for s in serial.split(",") if s.strip()]
        # description words the model placed in serial (no digits)
        # belong in desc, not the serial list
        real = [s for s in serials if any(c.isdigit() for c in s)]
        words = [s for s in serials if s not in real]
        if words:
            extra = " ".join(words)
            desc = f"{desc} {extra}".strip() if desc else extra
            serials = real
            if not serials:
                flag = "no_serial"
        # A token that still looks like an unresolved range must never be
        # emitted as a unit ID — demote it to desc and flag for review.
        ranged = [s for s in serials if re.search(r"\d\s*[-–]\s*\d", s)]
        if ranged:
            extra = " ".join(ranged)
            desc = f"{desc} {extra}".strip() if desc else extra
            serials = [s for s in serials if s not in ranged]
            flag = flag or "qty_mismatch"
            if not serials:
                flag = "no_serial"
        sev, warn = _flag_detail(flag, len(serials), qty)
        rows.append({"no": no, "model": model.upper(), "qty": qty,
                     "serial": ", ".join(serials), "serials": serials,
                     "desc": desc, "flag": flag or "",
                     "sev": sev, "warn": warn})
    return rows


def _flag_detail(flag: str, n_serials: int, qty: str) -> tuple:
    """Map a flag code to (severity, human-readable message)."""
    parts, sev = [], ""
    q = _qty_int(qty)
    if flag == "qty_mismatch":
        parts.append(f"{n_serials} serials captured but Qty declares "
                     f"{q or '?'} — serials may be missing or merged from "
                     "another row")
        sev = "error"
    elif flag == "qty_fixed":
        parts.append("dash separators were treated as separate serials "
                     f"to match the declared Qty {q or '?'} — please verify")
        sev = "warn"
    if flag == "no_serial":
        parts.append("no serial number on this row — description captured "
                     "instead; still usable")
        sev = sev or "warn"
    if flag == "no_model":
        parts.append("no model name on this row — assign a model before "
                     "committing")
        sev = "error"
    return sev, "; ".join(parts)


def _resolve_serials(raw_serial: str, qty: str) -> tuple:
    """Pick the serial interpretation whose count matches Qty, else flag.

    A dash inside a serial list is ambiguous: a real range (K361-K368) or a
    separator the model read as a dash (K308 - K323 meaning two units).
    Expand it only when the expanded count equals the printed quantity.
    Returns (serial_text, flag).
    """
    if not raw_serial:
        return "", ""
    q = _qty_int(qty)
    expanded = expand_serial_range(raw_serial)
    split = (_split_ranges(raw_serial)
             if (_RANGE_RE.search(raw_serial)
                 or _RANGE_SHORT_RE.search(raw_serial)) else None)
    if not q:
        return expanded, ""
    if _serial_count(expanded) == q:
        return expanded, ""
    if split and _serial_count(split) == q:
        # dash was really a separator
        return split, "qty_fixed"
    # neither matches the printed quantity — keep expanded but flag for review
    return expanded, "qty_mismatch"


# ------------------------------------------------------------- providers
def _retryable_http(err: Exception) -> bool:
    s = str(err)
    return any(t in s for t in ("500", "502", "503", "429",
                                "Throttling", "quota", "Timeout",
                                "overloaded", "UNAVAILABLE",
                                "RESOURCE_EXHAUSTED"))


def _scan_gemini(img_bytes: bytes, mime: str, cfg: dict, log) -> str:
    from google import genai
    from google.genai import types

    contents = [types.Part.from_bytes(data=img_bytes, mime_type=mime), PROMPT]
    model = cfg["model"]
    keys = cfg["keys"]
    resp, last_err = None, None
    for ki, entry in enumerate(keys):
        if len(keys) > 1:
            log(f"Using Gemini key {ki + 1} of {len(keys)}...")
        client = genai.Client(api_key=entry["key"])
        import time
        for attempt in range(3):
            try:
                resp = client.models.generate_content(model=model,
                                                      contents=contents)
                break
            except Exception as ex:
                last_err = ex
                if not _retryable_http(ex):
                    raise
                if attempt < 2:
                    log(f"Gemini busy — retrying in {2 * (attempt + 1)}s...")
                    time.sleep(2 * (attempt + 1))
        if resp is not None:
            break
        if ki + 1 < len(keys):
            log(f"Key {ki + 1} exhausted — switching to key {ki + 2}.")
    if resp is None:
        raise last_err
    return resp.text or ""


def _scan_openai_compat(provider: str, img_bytes: bytes, mime: str,
                        cfg: dict, log) -> str:
    label = PROVIDERS[provider]["label"]
    url = PROVIDERS[provider]["url"]
    data_url = f"data:{mime};base64," + base64.b64encode(img_bytes).decode()
    payload = json.dumps({
        "model": cfg["model"],
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": PROMPT},
            ],
        }],
    }).encode()

    def _post(key: str) -> str:
        req = urllib.request.Request(
            url, data=payload,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            body = json.loads(r.read().decode())
        return body["choices"][0]["message"]["content"]

    import time
    text, last_err = None, None
    keys = cfg["keys"]
    for ki, entry in enumerate(keys):
        if len(keys) > 1:
            log(f"Using {label} key {ki + 1} of {len(keys)}...")
        for attempt in range(3):
            try:
                text = _post(entry["key"])
                break
            except Exception as ex:
                last_err = ex
                if not _retryable_http(ex):
                    raise
                if attempt < 2:
                    log(f"{label} busy — retrying in {2 * (attempt + 1)}s...")
                    time.sleep(2 * (attempt + 1))
        if text is not None:
            break
        if ki + 1 < len(keys):
            log(f"Key {ki + 1} failed — switching to key {ki + 2}.")
    if text is None:
        raise last_err
    return text


def _scan_anthropic(img_bytes: bytes, mime: str, cfg: dict, log) -> str:
    payload = json.dumps({
        "model": cfg["model"],
        "max_tokens": 4096,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": mime,
                            "data": base64.b64encode(img_bytes).decode()}},
                {"type": "text", "text": PROMPT},
            ],
        }],
    }).encode()

    def _post(key: str) -> str:
        req = urllib.request.Request(
            PROVIDERS["anthropic"]["url"], data=payload,
            headers={"x-api-key": key,
                     "anthropic-version": "2023-06-01",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            body = json.loads(r.read().decode())
        return "".join(b.get("text", "") for b in body.get("content", []))

    import time
    text, last_err = None, None
    keys = cfg["keys"]
    for ki, entry in enumerate(keys):
        if len(keys) > 1:
            log(f"Using Claude key {ki + 1} of {len(keys)}...")
        for attempt in range(3):
            try:
                text = _post(entry["key"])
                break
            except Exception as ex:
                last_err = ex
                if not _retryable_http(ex):
                    raise
                if attempt < 2:
                    log(f"Claude busy — retrying in {2 * (attempt + 1)}s...")
                    time.sleep(2 * (attempt + 1))
        if text is not None:
            break
        if ki + 1 < len(keys):
            log(f"Key {ki + 1} failed — switching to key {ki + 2}.")
    if text is None:
        raise last_err
    return text


_SCANNERS = {
    "gemini": lambda i, m, c, log: _scan_gemini(i, m, c, log),
    "alibaba": lambda i, m, c, log: _scan_openai_compat("alibaba", i, m, c, log),
    "openai": lambda i, m, c, log: _scan_openai_compat("openai", i, m, c, log),
    "deepseek": lambda i, m, c, log: _scan_openai_compat("deepseek", i, m, c, log),
    "anthropic": lambda i, m, c, log: _scan_anthropic(i, m, c, log),
}


def scan_image(img_bytes: bytes, filename: str, log) -> list[dict]:
    """Run the active provider on the photo; returns parsed rows.

    `log` is a callable(str) — status lines for the UI log console.
    Raises RuntimeError with a user-facing message on failure.
    """
    providers = load_providers()
    active = load_active(providers)
    pid = active["provider"]
    cfg = providers.get(pid) or {}
    if not cfg.get("keys"):
        raise RuntimeError(
            f"No API key saved for {PROVIDERS[pid]['label']} — "
            "open Scan Model and add one.")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    mime = _MIME_BY_EXT.get(ext, "image/jpeg")
    label = PROVIDERS[pid]["label"]
    log(f"Sending to {label} ({cfg['model']}), waiting for response...")
    raw = _SCANNERS[pid](img_bytes, mime, cfg, log)
    try:
        rows = parse_scan_json(raw)
    except Exception as ex:
        raise RuntimeError(f"Could not parse {label} response: {ex}")
    n_serial = sum(len(r["serials"]) for r in rows)
    log(f"Extract complete. {len(rows)} models and "
        f"{n_serial} serial numbers found.")
    flagged = [r["no"] for r in rows if r.get("flag")]
    if flagged:
        log(f"{len(flagged)} row(s) need review: "
            + ", ".join(f"No {n or '?'}" for n in flagged))
    return rows
