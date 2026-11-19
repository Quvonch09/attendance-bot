const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const APP_JS = fs.readFileSync(path.join(__dirname, '..', 'webapp', 'app.js'), 'utf8');
const nativeSetTimeout = setTimeout;
const nativeClearTimeout = clearTimeout;

function makeElement(id) {
  const listeners = {};
  const classes = new Set();
  const element = {
    id,
    hidden: ['capture-button', 'retry-button', 'stop-button'].includes(id),
    disabled: false,
    value: 'checkin',
    textContent: '',
    style: {},
    className: '',
    listeners,
    classList: {
      add(...names) { names.forEach((name) => classes.add(name)); },
      remove(...names) { names.forEach((name) => classes.delete(name)); },
      contains(name) { return classes.has(name); },
    },
    addEventListener(name, callback) { listeners[name] = callback; },
    removeEventListener(name) { delete listeners[name]; },
    async click(name = 'click') {
      assert.equal(typeof listeners[name], 'function', `missing ${name} handler for ${id}`);
      return listeners[name]({target: element});
    },
  };
  if (id === 'video') {
    element.readyState = 2;
    element.videoWidth = 1280;
    element.videoHeight = 720;
    element.muted = false;
    element.playsInline = false;
    element.srcObject = null;
    element.playCalls = 0;
    element.pauseCalls = 0;
    element.play = async () => { element.playCalls += 1; };
    element.pause = () => { element.pauseCalls += 1; };
  }
  return element;
}

function response(body, status = 200) {
  return {ok: status >= 200 && status < 300, status, json: async () => body};
}

class MockFormData {
  constructor() { this.fields = []; }
  append(name, value, filename) { this.fields.push({name, value, filename}); }
}

function createWebView({platform, useTelegramLocation, enableBrowserFallback = false, outsideFirst = false, browserOutside = false, attendanceError = '', faceMode = 'moving', browserAccuracies, telegramAccuracies, action = 'checkin', challengeError = ''} = {}) {
  const elements = new Map();
  const getElement = (id) => {
    if (!elements.has(id)) elements.set(id, makeElement(id));
    return elements.get(id);
  };
  getElement('action-select').value = action;
  let getUserMediaCalls = 0;
  let locationReads = 0;
  let browserLocationReads = 0;
  let locationCheckCalls = 0;
  let attendanceCalls = 0;
  let greenBeforeAttendanceResponse = false;
  let canvasDraws = 0;
  let detectorFrames = 0;
  const locationPayloads = [];
  const challengeActions = [];
  const canvasSizes = [];
  const attendanceForms = [];
  const scheduledDelays = [];
  const track = {
    readyState: 'live',
    stopCalls: 0,
    listeners: {},
    stop() { this.stopCalls += 1; this.readyState = 'ended'; },
    addEventListener(name, callback) { this.listeners[name] = callback; },
  };
  const cameraStream = {
    active: true,
    getTracks() { return [track]; },
    getVideoTracks() { return [track]; },
  };
  const locationManager = useTelegramLocation ? {
    isInited: true,
    isLocationAvailable: true,
    init(callback) { callback(); },
    getLocation(callback) {
      locationReads += 1;
      const list = telegramAccuracies || (outsideFirst ? [25] : [350, 20]);
      callback({latitude: 38.8557873, longitude: 65.8002788, horizontal_accuracy: list[Math.min(locationReads - 1, list.length - 1)]});
    },
  } : undefined;
  const webApp = {
    initData: 'signed-test-init-data',
    initDataUnsafe: {user: {first_name: 'Test'}},
    platform,
    ready() {},
    expand() {},
  };
  if (locationManager) webApp.LocationManager = locationManager;

  class MockFaceDetection {
    constructor() { this.callback = null; this.index = 0; }
    setOptions() {}
    onResults(callback) { this.callback = callback; }
    async initialize() {}
    async send() {
      detectorFrames += 1;
      const i = this.index++;
      if (faceMode === 'none') { this.callback({detections: []}); return; }
      const centers = [0.50, 0.50, 0.50, 0.50, 0.58, 0.64, 0.68, 0.70];
      const x = centers[Math.min(i, centers.length - 1)];
      this.callback({detections: [{
        boundingBox: {xCenter: x, yCenter: 0.50, width: 0.35, height: 0.45},
        landmarks: [{x: x - 0.10, y: 0.40}, {x: x + 0.10, y: 0.40}, {x, y: 0.52}],
      }]});
    }
    async close() {}
  }
  const window = {
    Telegram: {WebApp: webApp},
    FaceDetection: MockFaceDetection,
    ATTENDANCE_API_BASE: 'https://attendance.example',
    location: {search: '', origin: 'https://attendance.example'},
  };
  const navigator = {
    userAgent: platform === 'ios' ? 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)' : 'Mozilla/5.0 (Linux; Android 15)',
    platform: platform === 'ios' ? 'iPhone' : 'Linux armv8l',
    maxTouchPoints: 1,
    mediaDevices: {
      async getUserMedia() { getUserMediaCalls += 1; return cameraStream; },
    },
  };
  if (!useTelegramLocation || enableBrowserFallback) {
    navigator.geolocation = {
      getCurrentPosition(success) {
        browserLocationReads += 1;
        const list = browserAccuracies || (useTelegramLocation ? [20] : [350, 20]);
        success({coords: {latitude: 38.8557873, longitude: 65.8002788, accuracy: list[Math.min(browserLocationReads - 1, list.length - 1)]}});
      },
    };
  }
  const document = {
    getElementById: getElement,
    querySelector() { return null; },
    createElement() {
      const canvas = {
        width: 0,
        height: 0,
        getContext() {
          return {
            save() {}, translate() {}, scale() {},
            drawImage() { canvasDraws += 1; }, restore() {},
          };
        },
        toBlob(callback, type, quality) {
          canvasSizes.push({width: canvas.width, height: canvas.height, type, quality});
          callback({size: 100, type});
        },
      };
      return canvas;
    },
  };
  const fetch = async (url, options = {}) => {
    if (String(url).includes('/api/location-check')) {
      locationCheckCalls += 1;
      const payload = JSON.parse(options.body);
      locationPayloads.push(payload);
      assert.equal(payload.latitude, 38.8557873);
      assert.equal(payload.longitude, 65.8002788);
      if (outsideFirst && locationCheckCalls === 1) {
        return response({error: 'outside_office_radius', distance_m: 1077, accuracy_m: payload.accuracy_m}, 403);
      }
      if (browserOutside && locationCheckCalls === 2) {
        return response({error: 'outside_office_radius', distance_m: 1077, accuracy_m: payload.accuracy_m}, 403);
      }
      if (payload.accuracy_m > 50) {
        return response({error: 'location_accuracy_insufficient', distance_m: 95, accuracy_m: payload.accuracy_m}, 422);
      }
      return response({ok: true, distance_m: 70, accuracy_m: payload.accuracy_m});
    }
    if (String(url).includes('/api/challenge')) {
      challengeActions.push(new URL(String(url)).searchParams.get('action'));
      if (challengeError) return response({error: challengeError}, 409);
      return response({challenge_id: 'one-use-test-challenge', instruction: 'Chapga qarang'});
    }
    if (String(url).includes('/api/attendance')) {
      attendanceCalls += 1;
      const fields = options.body.fields;
      attendanceForms.push(fields);
      greenBeforeAttendanceResponse = getElement('video-wrap').classList.contains('confirmed');
      assert.equal(fields.filter((field) => field.name === 'frames').length, 5);
      assert.equal(fields.find((field) => field.name === 'challenge_id').value, 'one-use-test-challenge');
      assert.equal(fields.find((field) => field.name === 'action').value, 'checkin');
      assert.equal(fields.find((field) => field.name === 'latitude').value, '38.8557873');
      assert.equal(fields.find((field) => field.name === 'longitude').value, '65.8002788');
      assert.equal(fields.find((field) => field.name === 'accuracy_m').value, '20');
      if (attendanceError) return response({error: attendanceError}, 422);
      return response({ok: true, message: 'Davomat serverda qayd etildi.'});
    }
    throw new Error(`unexpected request: ${url}`);
  };
  const fastSetTimeout = (callback, ms, ...args) => {
    scheduledDelays.push(ms);
    return nativeSetTimeout(callback, ms === 1800 ? 100 : 0, ...args);
  };
  vm.runInNewContext(APP_JS, {
    window, navigator, document, URLSearchParams, FormData: MockFormData,
    fetch, setTimeout: fastSetTimeout, clearTimeout: nativeClearTimeout,
    Promise, Error, console,
  }, {filename: 'webapp/app.js'});
  return {
    elements,
    element: getElement,
    getUserMediaCalls: () => getUserMediaCalls,
    locationReads: () => locationReads,
    browserLocationReads: () => browserLocationReads,
    locationCheckCalls: () => locationCheckCalls,
    challengeActions,
    attendanceCalls: () => attendanceCalls,
    detectorFrames: () => detectorFrames,
    greenBeforeAttendanceResponse: () => greenBeforeAttendanceResponse,
    canvasDraws: () => canvasDraws,
    canvasSizes,
    attendanceForms,
    scheduledDelays,
    locationPayloads,
    track,
    cameraStream,
  };
}

async function waitUntil(predicate, description, timeoutMs = 1000) {
  const deadline = Date.now() + timeoutMs;
  while (!predicate()) {
    if (Date.now() >= deadline) throw new Error(`timed out waiting for ${description}`);
    await new Promise((resolve) => nativeSetTimeout(resolve, 5));
  }
}

async function exerciseAccuracyRetry(environment) {
  const start = environment.elements.get('start-button');
  await start.click();
  const video = environment.elements.get('video');
  assert.equal(environment.locationCheckCalls(), 1);
  assert.equal(environment.track.stopCalls, 0, 'location uncertainty must not stop the camera');
  assert.equal(video.srcObject, environment.cameraStream, 'preview remains attached after the GPS retry message');
  assert.equal(start.disabled, false);
  assert.match(environment.elements.get('notice').textContent, /aniqlik/i);
  assert.match(environment.elements.get('notice').textContent, /100 m/i);

  const flow = start.click();
  await waitUntil(() => environment.locationCheckCalls() === 2, 'second server GPS check');
  assert.equal(environment.locationCheckCalls(), 2);
  assert.equal(environment.getUserMediaCalls(), 1, 'retry reuses the still-live camera stream');
  assert.equal(environment.track.stopCalls, 0);
  assert.equal(environment.elements.get('capture-button').hidden, true, 'manual capture stays hidden');
  assert.equal(environment.elements.get('start-button').hidden, true);
  assert.match(environment.elements.get('location-status').textContent, /GPS ±20 m/);
  await waitUntil(() => environment.attendanceCalls() === 1, 'automatic attendance upload');
  await flow;
  assert.equal(environment.greenBeforeAttendanceResponse(), false, 'green state is not shown before server success');
  assert.equal(environment.elements.get('video-wrap').classList.contains('confirmed'), true, 'green state appears only after successful server response');
  assert.match(environment.elements.get('capture-status').textContent, /Server tasdiqladi/);
  assert.equal(environment.track.stopCalls, 0, 'camera remains visible briefly so the user can see confirmation');
  assert.equal(environment.canvasDraws(), 5, 'five frames are captured after framing and movement');
  assert.equal(environment.detectorFrames() >= 7, true, 'the local face detector waits for a centered face and visible movement');
  assert.deepEqual(environment.canvasSizes[0], {width: 720, height: 405, type: 'image/jpeg', quality: 0.72}, 'frames preserve camera aspect ratio');
  assert.equal(environment.scheduledDelays.includes(3000), false, 'there is no fixed three-second countdown');
  assert.equal(environment.attendanceForms[0].filter((field) => field.name === 'frames').length, 5);
  await waitUntil(() => environment.track.stopCalls === 1, 'camera cleanup after visible confirmation');
}

test('iOS Telegram native location retry automatically captures a short sequence and confirms only after server success', async () => {
  const environment = createWebView({platform: 'ios', useTelegramLocation: true});
  await exerciseAccuracyRetry(environment);
  assert.equal(environment.locationReads(), 2);
  assert.equal(environment.browserLocationReads(), 0);
  assert.equal(environment.locationPayloads[0].accuracy_m, 350);
  assert.equal(environment.locationPayloads[1].accuracy_m, 20);
});

test('Android browser geolocation retry automatically captures while preserving frame proportions', async () => {
  const environment = createWebView({platform: 'android', useTelegramLocation: false});
  await exerciseAccuracyRetry(environment);
  assert.equal(environment.locationReads(), 0);
  assert.equal(environment.browserLocationReads(), 2);
  assert.equal(environment.locationPayloads[0].accuracy_m, 350);
  assert.equal(environment.locationPayloads[1].accuracy_m, 20);
});

test('iOS Telegram GPS outside result tries permitted WebView GPS and accepts only its server-verified result', async () => {
  const environment = createWebView({platform: 'ios', useTelegramLocation: true, enableBrowserFallback: true, outsideFirst: true});
  await environment.elements.get('start-button').click();
  await waitUntil(() => environment.attendanceCalls() === 1, 'fallback attendance upload');
  assert.equal(environment.locationReads(), 1);
  assert.equal(environment.browserLocationReads(), 1);
  assert.equal(environment.locationCheckCalls(), 2, 'both location sources were checked by the server');
  assert.match(environment.elements.get('location-status').textContent, /WebView GPS tasdiqlandi/);
  assert.equal(environment.elements.get('video-wrap').classList.contains('confirmed'), true);
  assert.equal(environment.locationPayloads.length, 2);
});

test('Android Telegram outside result reports server distance and source when permitted browser GPS is also outside', async () => {
  const environment = createWebView({platform: 'android', useTelegramLocation: true, enableBrowserFallback: true, outsideFirst: true, browserOutside: true, browserAccuracies: [25]});
  await environment.elements.get('start-button').click();
  const message = environment.elements.get('notice').textContent;
  assert.match(message, /WebView GPS ham alohida tekshirildi/);
  assert.match(message, /1077 m/);
  assert.match(message, /100 m radius/);
  assert.match(message, /Telegram LocationManager|Telegram WebView GPS/);
  assert.match(message, /Davomat qayd etilmadi/);
  assert.doesNotMatch(message, /38\.8557873|65\.8002788/);
  assert.equal(environment.track.stopCalls, 0);
  assert.equal(environment.elements.get('video').srcObject, environment.cameraStream);
});

test('face-match failure is explained separately and never turns the guide green', async () => {
  const environment = createWebView({platform: 'ios', useTelegramLocation: true, attendanceError: 'face_match_failed'});
  await environment.elements.get('start-button').click();
  await environment.elements.get('start-button').click();
  await waitUntil(() => environment.attendanceCalls() === 1, 'face-match response');
  await waitUntil(() => environment.elements.get('retry-button').hidden === false, 'retry control');
  assert.match(environment.elements.get('notice').textContent, /profil suratiga mos kelmadi/i);
  assert.equal(environment.elements.get('video-wrap').classList.contains('confirmed'), false);
  assert.equal(environment.track.stopCalls, 0, 'live preview remains available for retry');
});

test('movement-check failure is explained separately and never turns the guide green', async () => {
  const environment = createWebView({platform: 'android', useTelegramLocation: true, attendanceError: 'motion_check_failed'});
  await environment.elements.get('start-button').click();
  await environment.elements.get('start-button').click();
  await waitUntil(() => environment.attendanceCalls() === 1, 'movement-check response');
  await waitUntil(() => environment.elements.get('retry-button').hidden === false, 'retry control');
  assert.match(environment.elements.get('notice').textContent, /topshiriqdagi harakat sezilmadi/i);
  assert.equal(environment.elements.get('video-wrap').classList.contains('confirmed'), false);
});

test('Telegram geofence rejection without browser fallback does not stop camera or expose coordinates', async () => {
  const environment = createWebView({platform: 'android', useTelegramLocation: true, outsideFirst: true});
  const start = environment.elements.get('start-button');
  await start.click();
  const message = environment.elements.get('notice').textContent;
  assert.match(message, /1077 m/);
  assert.match(message, /±25 m/);
  assert.match(message, /100 m radius/);
  assert.match(message, /Telegram LocationManager/);
  assert.match(message, /Ruxsat yoqilgan bo‘lsa ham/);
  assert.match(message, /Davomat qayd etilmadi/);
  assert.doesNotMatch(message, /38\.8557873|65\.8002788/);
  assert.equal(environment.track.stopCalls, 0);
  assert.equal(environment.elements.get('video').srcObject, environment.cameraStream);
  assert.equal(start.textContent, 'GPSni qayta tekshirish →');
});

test('Stop during automatic face framing cancels upload and camera use', async () => {
  const environment = createWebView({platform: 'ios', useTelegramLocation: true, faceMode: 'none', telegramAccuracies: [20]});
  const captureStatus = environment.element('capture-status');
  const pendingStart = environment.elements.get('start-button').click();
  await waitUntil(() => /Yuz topilmadi/.test(captureStatus.textContent), 'automatic face framing');
  await environment.elements.get('stop-button').click();
  await pendingStart;
  assert.equal(environment.attendanceCalls(), 0, 'stopping before a valid face/movement sequence must not submit attendance');
  assert.equal(environment.track.stopCalls, 1);
  assert.equal(environment.elements.get('video-wrap').classList.contains('confirmed'), false);
  assert.match(environment.elements.get('notice').textContent, /to‘xtatildi/i);
});

test('lunch-out sequence 409 is shown in the movement step and sends no face frames', async () => {
  const environment = createWebView({platform: 'android', useTelegramLocation: true, telegramAccuracies: [20], action: 'lunch_out', challengeError: 'sequence_invalid'});
  await environment.elements.get('start-button').click();
  assert.deepEqual(environment.challengeActions, ['lunch_out']);
  assert.equal(environment.locationCheckCalls(), 1, 'the office location was accepted by the server');
  assert.equal(environment.detectorFrames(), 0, 'face capture must not start without an issued challenge');
  assert.equal(environment.attendanceCalls(), 0, 'no attendance upload occurs on a rejected challenge');
  assert.match(environment.elements.get('motion-status').textContent, /oldingi davomat amali qayd etilmagan/i);
  assert.match(environment.elements.get('instruction').textContent, /Ishga kelish/);
  assert.match(environment.elements.get('capture-status').textContent, /kadrlar yuborilmadi/i);
  assert.equal(environment.elements.get('capture-count').textContent, '0 / 5');
  assert.equal(environment.elements.get('start-button').textContent, 'Qayta tekshirish →');
  assert.equal(environment.elements.get('video').srcObject, environment.cameraStream, 'preview stays live for the user to read the error');
});

test('duplicate attendance action 409 is surfaced clearly and does not start capture', async () => {
  const environment = createWebView({platform: 'ios', useTelegramLocation: true, telegramAccuracies: [20], action: 'lunch_out', challengeError: 'duplicate_action'});
  await environment.elements.get('start-button').click();
  assert.equal(environment.detectorFrames(), 0);
  assert.equal(environment.attendanceCalls(), 0);
  assert.match(environment.elements.get('instruction').textContent, /allaqachon qayd etilgan/i);
  assert.match(environment.elements.get('capture-status').textContent, /kadrlar yuborilmadi/i);
});
