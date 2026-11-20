import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "webapp" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "webapp" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "webapp" / "styles.css").read_text(encoding="utf-8")


class WebAppPermissionFlowTests(unittest.TestCase):
    def test_native_location_challenge_is_verified_before_camera_request(self):
        start = APP_JS.split("async function start(){", 1)[1].split("async function capture(frames, sessionId)", 1)[0]
        self.assertLess(start.index("/api/challenge"), start.index("cameraPromise = navigator.mediaDevices.getUserMedia"))
        self.assertIn("native_location_token", start)
        self.assertIn("location_verified !== true", start)
        self.assertNotIn("Promise.all", start)
        self.assertNotIn("navigator.geolocation", APP_JS)
        self.assertNotIn("LocationManager", APP_JS)
        self.assertNotIn("/api/location-check", APP_JS)

    def test_launch_token_is_read_from_fragment_and_camera_auto_start_has_manual_fallback(self):
        self.assertIn("window.location.hash", APP_JS)
        self.assertIn("native_location_token", APP_JS)
        self.assertIn("else if (autoStart)", APP_JS)
        self.assertIn("Kamera avtomatik ochilmoqda", APP_JS)
        self.assertIn("Kamerani qayta ochish", APP_JS)
        self.assertIn("NotAllowedError", APP_JS)
        self.assertIn("iPhone’da Sozlamalar", APP_JS)

    def test_attendance_upload_has_no_client_supplied_location(self):
        capture = APP_JS.split("async function capture(frames, sessionId)", 1)[1].split("$('action-select')", 1)[0]
        self.assertIn("challenge_id", capture)
        self.assertIn("form.append('action'", capture)
        self.assertNotIn("latitude", capture)
        self.assertNotIn("longitude", capture)
        self.assertNotIn("accuracy_m", capture)
        self.assertIn("challengeConsumed = true", capture)

    def test_face_detector_uses_only_versioned_same_origin_assets(self):
        self.assertIn("window.FaceDetection", APP_JS)
        self.assertIn("/face-detection/${FACE_DETECTION_VERSION}/${filename}", APP_JS)
        self.assertIn('/face-detection/v0.4.1646425229/face_detection.js', HTML)
        self.assertIn("Telegram chatdagi lokatsiya", HTML)
        self.assertNotIn("tfhub.dev", APP_JS)
        self.assertNotIn("cdn.jsdelivr.net", APP_JS)

    def test_capture_is_automatic_after_framing_and_motion_without_a_countdown(self):
        self.assertIn("frontalFrames >= 3", APP_JS)
        self.assertIn("motionRangeExceeded", APP_JS)
        self.assertIn("return [baselineBlob, ...recent.map(item => item.blob)]", APP_JS)
        self.assertIn("await capture(frames, thisSession)", APP_JS)
        self.assertNotRegex(APP_JS, re.compile(r"setTimeout\([^\n]*,\s*3000\b"))
        self.assertIn("Kadrlar kechiktiruvchi taymersiz avtomatik yuboriladi", APP_JS)

    def test_green_face_confirmation_requires_server_success(self):
        capture = APP_JS.split("async function capture(frames, sessionId)", 1)[1].split("$('action-select')", 1)[0]
        self.assertLess(capture.index("if (!response.ok)"), capture.index("classList.add('confirmed')"))
        self.assertIn(".video-wrap.confirmed .face-guide", CSS)

    def test_hidden_camera_placeholder_never_covers_live_video(self):
        self.assertRegex(CSS, r"\.camera-placeholder\[hidden\]\s*\{\s*display:\s*none\s*!important")


if __name__ == "__main__":
    unittest.main()
