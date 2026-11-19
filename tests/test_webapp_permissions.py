import unittest
from pathlib import Path


APP_JS = (Path(__file__).resolve().parents[1] / "webapp" / "app.js").read_text(encoding="utf-8")
CSS = (Path(__file__).resolve().parents[1] / "webapp" / "styles.css").read_text(encoding="utf-8")


class WebAppPermissionFlowTests(unittest.TestCase):
    def test_camera_permission_is_resolved_before_location_is_requested(self):
        start = APP_JS.split("async function start(){", 1)[1].split("async function capture()", 1)[0]
        self.assertLess(start.index("navigator.mediaDevices.getUserMedia"), start.index("await waitForCamera(cameraPromise)"))
        self.assertLess(start.index("await waitForCamera(cameraPromise)"), start.index("await requestLocation()"))
        self.assertNotIn("Promise.all", start)

    def test_telegram_native_location_manager_and_browser_fallback_are_available(self):
        self.assertIn("tg&&tg.LocationManager", APP_JS)
        self.assertIn("manager.getLocation", APP_JS)
        self.assertIn("navigator.geolocation.getCurrentPosition", APP_JS)

    def test_permission_waits_have_timeouts_and_actionable_error_paths(self):
        self.assertIn("CameraPermissionTimeoutError", APP_JS)
        self.assertIn("30000", APP_JS)
        self.assertIn("LocationPermissionTimeoutError", APP_JS)
        self.assertIn("locationFailure", APP_JS)

    def test_location_accuracy_from_telegram_and_browser_is_sent_to_server(self):
        self.assertIn("location.horizontal_accuracy", APP_JS)
        self.assertIn("raw.accuracy", APP_JS)
        self.assertIn("/api/location-check", APP_JS)
        self.assertIn("accuracy_m:position.coords.accuracy", APP_JS)
        self.assertIn("form.append('accuracy_m'", APP_JS)
        self.assertNotIn("dist>300", APP_JS)

    def test_camera_is_preserved_for_location_errors_and_permission_help_covers_both_platforms(self):
        self.assertIn("if(error.cameraFailure||!hasLiveCamera())stopCamera()", APP_JS)
        self.assertIn("iPhone’da Sozlamalar", APP_JS)
        self.assertIn("Android’da Sozlamalar", APP_JS)

    def test_green_face_confirmation_requires_server_success(self):
        capture = APP_JS.split("async function capture()", 1)[1].split("$('action-select')", 1)[0]
        self.assertLess(capture.index("if(!res.ok)"), capture.index("classList.add('confirmed')"))
        self.assertIn(".video-wrap.confirmed .face-guide", CSS)


if __name__ == "__main__":
    unittest.main()
