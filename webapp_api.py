"""Isolated HTTPS-proxy-facing Telegram WebApp check-in API.

This module deliberately imports no bot module; all application integrations are
injected through :func:`build_api_app`.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import secrets
import sqlite3
import tempfile
import time
from collections import OrderedDict
from datetime import datetime, time as datetime_time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl

from aiohttp import web

MAX_FRAME_BYTES = 1_000_000
MAX_TOTAL_BYTES = 6_000_000
MIN_FRAMES = 3
MAX_FRAMES = 6
CHALLENGE_TTL = 300
ALLOWED_ACTIONS = ("checkin", "lunch_out", "lunch_in", "checkout")
MAX_CHALLENGES = 4096


def verify_init_data(init_data: str, bot_token: str, *, now_epoch: float | None = None) -> dict[str, Any] | None:
    """Verify raw Telegram WebApp initData and return its signed fields, or None.

    Follows Telegram's WebAppData HMAC procedure, excludes ``hash`` only from
    data_check_string, and enforces a 12-hour age and a
    60-second future-clock allowance. Never accepts initDataUnsafe values.
    """
    if not init_data or not bot_token:
        return None
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
        fields: dict[str, str] = {}
        for key, value in pairs:
            if key in fields:
                return None
            fields[key] = value
        supplied_hash = fields.get("hash", "")
        if len(supplied_hash) != 64:
            return None
        int(supplied_hash, 16)
        check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()) if k != "hash")
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, supplied_hash.lower()):
            return None
        auth_date = int(fields["auth_date"])
        current = time.time() if now_epoch is None else now_epoch
        if auth_date < current - 12 * 3600 or auth_date > current + 60:
            return None
        user = json.loads(fields["user"])
        if not isinstance(user, dict) or not isinstance(user.get("id"), int) or isinstance(user.get("id"), bool):
            return None
        fields["user"] = user  # type: ignore[assignment]
        fields["auth_date"] = str(auth_date)
        return fields  # type: ignore[return-value]
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def detect_face_liveness(frame_paths: list[str | Path]) -> bool:
    """Prototype-only Haar face presence/motion check; not certified liveness.

    Requires faces in at least three frames and variation in their detected
    bounding boxes. It is a lightweight signal, not anti-spoofing security.
    """
    try:
        import cv2
        import numpy as np
        detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        boxes: list[tuple[int, int, int, int]] = []
        for path in frame_paths:
            image = cv2.imread(str(path))
            if image is None:
                continue
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            found = detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(80, 80))
            if len(found):
                x, y, w, h = max(found, key=lambda b: b[2] * b[3])
                boxes.append((int(x), int(y), int(w), int(h)))
        if len(boxes) < MIN_FRAMES:
            return False
        centers = np.array([(x + w / 2, y + h / 2, w, h) for x, y, w, h in boxes], dtype=float)
        return bool(np.max(np.ptp(centers, axis=0)) >= 8)
    except Exception:
        return False


def build_api_app(*, token: str, allowed_origin: str | None, office_lat: float,
                  office_lon: float, office_radius_m: float, temp_dir: str | Path,
                  get_user: Callable[[int], Any], get_event: Callable[[int, str], Any],
                  get_explanation: Callable[[int], Any],
                  add_event: Callable[..., Any], compare_face: Callable[..., Any],
                  notify_admins: Callable[[str], Any], format_name: Callable[[Any], str],
                  late_minutes: Callable[[str], int], hhmm: Callable[[str], str],
                  now_local: Callable[[], datetime],
                  distance_from_office: Callable[[float, float], float],
                  clock: Callable[[], float] = time.time,
                  liveness_check: Callable[[list[str | Path]], bool] = detect_face_liveness) -> web.Application:
    """Build API app using bot-owned lookups and callbacks, without importing bot.py.

    ``get_user(uid)`` must return only active/approved users (or None). The row
    must contain ``face_path``. ``get_event(uid, 'checkin')`` tests today's
    event and ``get_explanation(uid)`` tests for the required late-arrival note.
    ``add_event`` is called as ``(uid, 'checkin', lat, lon, distance, 1, 1)``.
    Async and synchronous callbacks are supported.
    """
    if allowed_origin and (allowed_origin == "*" or not allowed_origin.strip()):
        raise ValueError("allowed_origin must be one exact origin")
    app = web.Application(client_max_size=MAX_TOTAL_BYTES + 128_000)
    challenges: OrderedDict[str, dict[str, Any]] = OrderedDict()
    static_root = Path(__file__).resolve().parent / "webapp"

    def prune() -> None:
        now = clock()
        for cid in [k for k, v in challenges.items() if v["expires"] <= now]:
            challenges.pop(cid, None)
        while len(challenges) > MAX_CHALLENGES:
            challenges.popitem(last=False)

    async def invoke(fn: Callable, *args):
        result = fn(*args)
        return await result if asyncio.iscoroutine(result) else result

    @web.middleware
    async def security_middleware(request: web.Request, handler):
        # Same-origin browser GETs can omit Origin. The isolated 8443 Nginx
        # block overwrites X-Cue-Origin with its fixed, approved origin.
        origin = request.headers.get("Origin") or request.headers.get("X-Cue-Origin")
        if request.method == "OPTIONS":
            if not allowed_origin:
                return web.json_response({"error": "service_unavailable"}, status=503)
            if origin != allowed_origin:
                return web.json_response({"error": "origin_denied"}, status=403)
            response = web.Response(status=204)
        else:
            if not allowed_origin:
                return web.json_response({"error": "service_unavailable"}, status=503)
            if origin != allowed_origin:
                return web.json_response({"error": "origin_denied"}, status=403)
            try:
                response = await handler(request)
            except web.HTTPException as exc:
                response = web.json_response({"error": "request_invalid"}, status=exc.status)
            except Exception:
                response = web.json_response({"error": "request_failed"}, status=500)
        response.headers["Access-Control-Allow-Origin"] = allowed_origin
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Telegram-Init-Data"
        response.headers["Vary"] = "Origin"
        response.headers["Cache-Control"] = "no-store"
        return response

    app.middlewares.append(security_middleware)

    def authenticated_uid(request: web.Request) -> int | None:
        data = verify_init_data(request.headers.get("X-Telegram-Init-Data", ""), token, now_epoch=clock())
        return int(data["user"]["id"]) if data else None

    async def challenge(request: web.Request):
        uid = authenticated_uid(request)
        if uid is None:
            return web.json_response({"error": "unauthorized"}, status=401)
        user = await invoke(get_user, uid)
        if not user:
            return web.json_response({"error": "not_approved"}, status=403)
        action = request.query.get("action", "")
        if action not in ALLOWED_ACTIONS:
            return web.json_response({"error": "invalid_action"}, status=400)
        if await invoke(get_event, uid, action):
            return web.json_response({"error": "duplicate_action"}, status=409)
        required_before = ALLOWED_ACTIONS[:ALLOWED_ACTIONS.index(action)]
        for previous in required_before:
            if not await invoke(get_event, uid, previous):
                return web.json_response({"error": "sequence_invalid"}, status=409)
        if action == "checkin" and now_local().time() >= datetime_time(9, 30) and not await invoke(get_explanation, uid):
            return web.json_response({"error": "late_reason_required"}, status=428)
        prune()
        cid = secrets.token_urlsafe(32)
        left, right = secrets.randbelow(9) + 1, secrets.randbelow(9) + 1
        direction = secrets.choice(("left", "right"))
        challenges[cid] = {"uid": uid, "action": action, "expires": clock() + CHALLENGE_TTL, "left": left, "right": right, "direction": direction}
        prune()
        direction_uz = "chapga" if direction == "left" else "o‘ngga"
        return web.json_response({"challenge_id": cid, "instruction": f"Kameraga qarab boshingizni {direction_uz} buring, so‘ng yana kameraga qarang.", "expires_in": CHALLENGE_TTL})

    async def attendance(request: web.Request):
        uid = authenticated_uid(request)
        if uid is None:
            return web.json_response({"error": "unauthorized"}, status=401)
        user = await invoke(get_user, uid)
        if not user:
            return web.json_response({"error": "not_approved"}, status=403)
        if request.content_type != "multipart/form-data":
            return web.json_response({"error": "multipart_required"}, status=400)
        reader = await request.multipart()
        values: dict[str, str] = {}
        files: list[tuple[str, bytes]] = []
        total = 0
        async for part in reader:
            if part.name in ("latitude", "longitude", "challenge_id", "action"):
                if part.filename:
                    return web.json_response({"error": "invalid_form"}, status=400)
                value = await part.text()
                if part.name in values:
                    return web.json_response({"error": "invalid_form"}, status=400)
                values[part.name] = value
            elif part.name == "frames":
                if not part.filename or part.headers.get("Content-Type", "").lower() not in ("image/jpeg", "image/jpg"):
                    return web.json_response({"error": "invalid_frame"}, status=400)
                data = await part.read_chunk(size=64 * 1024)
                chunks = [data]
                size = len(data)
                while not part.at_eof():
                    chunk = await part.read_chunk(size=64 * 1024)
                    size += len(chunk)
                    if size > MAX_FRAME_BYTES:
                        return web.json_response({"error": "frame_too_large"}, status=413)
                    chunks.append(chunk)
                total += size
                if total > MAX_TOTAL_BYTES:
                    return web.json_response({"error": "upload_too_large"}, status=413)
                files.append((part.filename, b"".join(chunks)))
                if len(files) > MAX_FRAMES:
                    return web.json_response({"error": "invalid_frame_count"}, status=400)
            else:
                return web.json_response({"error": "invalid_form"}, status=400)
        try:
            lat, lon = float(values["latitude"]), float(values["longitude"])
            cid = values["challenge_id"]
            action = values["action"]
            if action not in ALLOWED_ACTIONS:
                raise ValueError
            if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
                raise ValueError
        except (KeyError, ValueError):
            return web.json_response({"error": "invalid_location_or_challenge"}, status=400)
        prune()
        entry = challenges.pop(cid, None)  # one-use even when later validation fails
        if not entry or entry["uid"] != uid or entry["action"] != action or entry["expires"] <= clock():
            return web.json_response({"error": "challenge_expired"}, status=400)
        if not MIN_FRAMES <= len(files) <= MAX_FRAMES:
            return web.json_response({"error": "invalid_frame_count"}, status=400)
        try:
            distance = float(await invoke(distance_from_office, lat, lon))
        except Exception:
            return web.json_response({"error": "location_unavailable"}, status=400)
        if not math.isfinite(distance) or distance > office_radius_m:
            return web.json_response({"error": "outside_office_radius"}, status=403)

        created: list[Path] = []
        try:
            directory = Path(temp_dir)
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            try:
                directory.chmod(0o700)
            except OSError:
                pass
            # Private per-request directory; files are never retained.
            with tempfile.TemporaryDirectory(prefix="attendance-api-", dir=directory) as work:
                work_path = Path(work)
                try:
                    work_path.chmod(0o700)
                except OSError:
                    pass
                paths = []
                for index, (_, blob) in enumerate(files):
                    path = work_path / f"frame-{index}.jpg"
                    path.write_bytes(blob)
                    path.chmod(0o600)
                    paths.append(path)
                    created.append(path)
                if asyncio.iscoroutinefunction(liveness_check):
                    live_result = await liveness_check(paths)
                else:
                    live_result = await asyncio.to_thread(liveness_check, paths)
                live_ok = bool(live_result)
                if not live_ok:
                    return web.json_response({"error": "face_check_failed"}, status=422)
                reference = user["face_path"] if hasattr(user, "__getitem__") else getattr(user, "face_path")
                face_ok = False
                for index in sorted(set((0, len(paths) // 2, len(paths) - 1))):
                    if await invoke(compare_face, reference, paths[index]):
                        face_ok = True
                        break
                if not face_ok:
                    return web.json_response({"error": "face_check_failed"}, status=422)
                try:
                    await invoke(add_event, uid, action, lat, lon, distance, 1, 1)
                except sqlite3.IntegrityError:
                    return web.json_response({"error": "duplicate_action"}, status=409)
                event = await invoke(get_event, uid, action)
                happened = event["happened_at"] if hasattr(event, "__getitem__") else event.happened_at
                delay = int(await invoke(late_minutes, happened))
                time_text = await invoke(hhmm, happened)
                name = await invoke(format_name, user)
                labels = {"checkin": "ishga keldi", "lunch_out": "tushlikka chiqdi", "lunch_in": "tushlikdan qaytdi", "checkout": "ishdan chiqdi"}
                text = f"✅ {name} {time_text} da {labels[action]} (ofisdan {round(distance)} m)." + (f" Kechikish: {delay} daqiqa." if action == "checkin" else "")
                await invoke(notify_admins, text)
                message = f"{labels[action].capitalize()} {time_text} da qayd etildi." + (f" Kechikish: {delay} daqiqa." if action == "checkin" and delay else "")
                return web.json_response({"ok": True, "time": time_text, "delay_minutes": delay, "distance_m": round(distance), "message": message})
        except Exception:
            return web.json_response({"error": "checkin_failed"}, status=500)
        finally:
            # TemporaryDirectory removes all files on all exit paths.
            created.clear()

    async def index_page(request: web.Request):
        return web.FileResponse(static_root / "index.html")

    async def static_asset(request: web.Request):
        asset = request.match_info["asset"]
        if asset not in {"styles.css", "app.js", "config.js"}:
            raise web.HTTPNotFound()
        return web.FileResponse(static_root / asset)

    async def options(request: web.Request):
        return web.Response(status=204)

    app.router.add_route("GET", "/", index_page)
    app.router.add_route("GET", "/{asset}", static_asset)
    app.router.add_route("GET", "/api/challenge", challenge)
    app.router.add_route("POST", "/api/attendance", attendance)
    app.router.add_route("OPTIONS", "/api/{tail:.*}", options)
    return app


async def start_api(app: web.Application, *, host: str = "127.0.0.1", port: int = 8765, **kwargs: Any) -> web.AppRunner:
    """Start the API on loopback by default; caller owns runner cleanup lifecycle."""
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port, **kwargs)
    await site.start()
    return runner
