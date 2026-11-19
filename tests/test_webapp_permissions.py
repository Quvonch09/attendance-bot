import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "webapp" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "webapp" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "webapp" / "styles.css").read_text(encoding="utf-8")


class WebAppPermissionFlowTests(unittest.TestCase):
    def test_camera_permission_is_resolved_before_location_is_requested(self):
        start = APP_JS.split("async function start(){", 1)[1].split("async function capture(frames, sessionId)", 1)[0]
        self.assertLess(start.index("navigator.mediaDevices.getUserMedia"), start.index("await waitForCamera(cameraPromise)"))
        self.assertLess(start.index("await waitForCamera(cameraPromise)"), start.index("await requestAndVerifyLocation()"))
        self.assertNotIn("Promise.all", start)

    def test_telegram_location_has_an_explicit_os_permission_browser_fallback(self):
        self.assertIn("tg && tg.LocationManager", APP_JS)
        self.assertIn("manager.getLocation", APP_JS)
        self.assertIn("navigator.geolocation.getCurrentPosition", APP_JS)
        self.assertIn("canFallbackToBrowser(error)", APP_JS)
        self.assertIn("alternativeSourceAttempted", APP_JS)
        self.assertIn("await checkLocationOnServer(position)", APP_JS)
        self.assertNotIn("distance_m <= 300", APP_JS)
        self.assertIn("300 m radius o‘zgarmagan", APP_JS)

    def test_permission_waits_have_timeouts_and_actionable_platform_help(self):
        self.assertIn("CameraPermissionTimeoutError", APP_JS)
        self.assertIn("30000", APP_JS)
        self.assertIn("LocationPermissionTimeoutError", APP_JS)
        self.assertIn("locationFailure", APP_JS)
        self.assertIn("iPhone’da Sozlamalar", APP_JS)
        self.assertIn("Android’da Sozlamalar", APP_JS)

    def test_telegram_and_browser_accuracy_reach_authoritative_server_checks(self):
        self.assertIn("location.horizontal_accuracy", APP_JS)
        self.assertIn("raw.accuracy", APP_JS)
        self.assertIn("/api/location-check", APP_JS)
        self.assertIn("accuracy_m: position.coords.accuracy", APP_JS)
        self.assertIn("form.append('accuracy_m'", APP_JS)
        self.assertIn("error.locationSource = position.source", APP_JS)
        self.assertNotIn("dist>300", APP_JS)

    def test_face_detector_uses_only_versioned_same_origin_assets(self):
        self.assertIn("window.FaceDetection", APP_JS)
        self.assertIn("/face-detection/${FACE_DETECTION_VERSION}/${filename}", APP_JS)
        self.assertIn('/face-detection/v0.4.1646425229/face_detection.js', HTML)
        self.assertIn("Yuzni kadrga joylashtirish shu Web App ichida bajariladi", HTML)
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
