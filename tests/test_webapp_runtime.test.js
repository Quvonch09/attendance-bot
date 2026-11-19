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

function createWebView({platform, useTelegramLocation, outsideFirst = false, attendanceError = ''}) {
  const elements = new Map();
  const getElement = (id) => {
    if (!elements.has(id)) elements.set(id, makeElement(id));
    return elements.get(id);
  };
  let getUserMediaCalls = 0;
  let locationReads = 0;
  let browserLocationReads = 0;
  let locationCheckCalls = 0;
  let attendanceCalls = 0;
  let greenBeforeAttendanceResponse = false;
  let canvasDraws = 0;
  const locationPayloads = [];
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
      callback({latitude: 38.8557873, longitude: 65.8002788, horizontal_accuracy: outsideFirst ? 25 : locationReads === 1 ? 350 : 20});
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
  const window = {
    Telegram: {WebApp: webApp},
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
  if (!useTelegramLocation) {
    navigator.geolocation = {
      getCurrentPosition(success) {
        browserLocationReads += 1;
        success({coords: {latitude: 38.8557873, longitude: 65.8002788, accuracy: browserLocationReads === 1 ? 350 : 20}});
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
            save() {},
            translate() {},
            scale() {},
            drawImage() { canvasDraws += 1; },
            restore() {},
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
      if (locationCheckCalls === 1) {
        return response({error: 'location_accuracy_insufficient', distance_m: 280, accuracy_m: payload.accuracy_m}, 422);
      }
      return response({ok: true, distance_m: 230, accuracy_m: payload.accuracy_m});
    }
    if (String(url).includes('/api/challenge')) {
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
    return nativeSetTimeout(callback, ms === 1800 ? 100 : ms === 3000 ? 30 : 0, ...args);
  };
  vm.runInNewContext(APP_JS, {
    window,
    navigator,
    document,
    URLSearchParams,
    FormData: MockFormData,
    fetch,
    setTimeout: fastSetTimeout,
    clearTimeout: nativeClearTimeout,
    Promise,
    Error,
    console,
  }, {filename: 'webapp/app.js'});
  return {
    elements,
    getUserMediaCalls: () => getUserMediaCalls,
    locationReads: () => locationReads,
    browserLocationReads: () => browserLocationReads,
    locationCheckCalls: () => locationCheckCalls,
    attendanceCalls: () => attendanceCalls,
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

  await start.click();
  assert.equal(environment.locationCheckCalls(), 2);
  assert.equal(environment.getUserMediaCalls(), 1, 'retry reuses the still-live camera stream');
  assert.equal(environment.track.stopCalls, 0);
  assert.equal(environment.elements.get('capture-button').hidden, true, 'the manual capture button stays hidden');
  assert.equal(environment.elements.get('start-button').hidden, true);
  assert.match(environment.elements.get('location-status').textContent, /GPS ±20 m/);
  assert.match(environment.elements.get('notice').textContent, /avtomatik/i);

  await waitUntil(() => environment.attendanceCalls() === 1, 'automatic attendance upload');
  assert.equal(environment.greenBeforeAttendanceResponse(), false, 'the green state is not shown before server success');
  assert.equal(environment.elements.get('video-wrap').classList.contains('confirmed'), true, 'green state appears only after a successful server response');
  assert.equal(environment.track.stopCalls, 0, 'camera remains visible briefly so the user can see confirmation');
  assert.equal(environment.canvasDraws(), 5, 'five frames are captured automatically');
  assert.deepEqual(environment.canvasSizes[0], {width: 720, height: 405, type: 'image/jpeg', quality: 0.72}, 'frame dimensions preserve the camera aspect ratio');
  assert.ok(environment.scheduledDelays.includes(3000), 'the direction instruction has a short response countdown');
  assert.equal(environment.attendanceForms[0].filter((field) => field.name === 'frames').length, 5);
  await waitUntil(() => environment.track.stopCalls === 1, 'camera cleanup after visible confirmation');
}

test('iOS Telegram native LocationManager retry auto-captures and confirms only after the server response', async () => {
  const environment = createWebView({platform: 'ios', useTelegramLocation: true});
  await exerciseAccuracyRetry(environment);
  assert.equal(environment.locationReads(), 2);
  assert.equal(environment.browserLocationReads(), 0);
  assert.equal(environment.locationPayloads[0].accuracy_m, 350);
  assert.equal(environment.locationPayloads[1].accuracy_m, 20);
});

test('Android browser geolocation retry auto-captures while preserving frame proportions', async () => {
  const environment = createWebView({platform: 'android', useTelegramLocation: false});
  await exerciseAccuracyRetry(environment);
  assert.equal(environment.locationReads(), 0);
  assert.equal(environment.browserLocationReads(), 2);
  assert.equal(environment.locationPayloads[0].accuracy_m, 350);
  assert.equal(environment.locationPayloads[1].accuracy_m, 20);
});

test('face-match failure is explained separately and never turns the guide green', async () => {
  const environment = createWebView({platform: 'ios', useTelegramLocation: true, attendanceError: 'face_match_failed'});
  const start = environment.elements.get('start-button');
  await start.click();
  await start.click();
  await waitUntil(() => environment.attendanceCalls() === 1, 'face-match response');
  await waitUntil(() => environment.elements.get('retry-button').hidden === false, 'retry control');
  assert.match(environment.elements.get('notice').textContent, /profil suratiga mos kelmadi/i);
  assert.equal(environment.elements.get('video-wrap').classList.contains('confirmed'), false);
  assert.equal(environment.track.stopCalls, 0, 'the live preview remains available for a retry');
});

test('movement-check failure is explained separately and never turns the guide green', async () => {
  const environment = createWebView({platform: 'android', useTelegramLocation: true, attendanceError: 'motion_check_failed'});
  const start = environment.elements.get('start-button');
  await start.click();
  await start.click();
  await waitUntil(() => environment.attendanceCalls() === 1, 'movement-check response');
  await waitUntil(() => environment.elements.get('retry-button').hidden === false, 'retry control');
  assert.match(environment.elements.get('notice').textContent, /topshiriqdagi harakat sezilmadi/i);
  assert.equal(environment.elements.get('video-wrap').classList.contains('confirmed'), false);
});

test('Android Telegram outside result shows distance, accuracy and source without stopping camera', async () => {
  const environment = createWebView({platform: 'android', useTelegramLocation: true, outsideFirst: true});
  const start = environment.elements.get('start-button');
  await start.click();
  const message = environment.elements.get('notice').textContent;
  assert.match(message, /1077 m/);
  assert.match(message, /±25 m/);
  assert.match(message, /Telegram LocationManager/);
  assert.match(message, /Ruxsat yoqilgan bo‘lsa ham/);
  assert.match(message, /Davomat qayd etilmadi/);
  assert.doesNotMatch(message, /38\.8557873|65\.8002788/);
  assert.equal(environment.track.stopCalls, 0);
  assert.equal(environment.elements.get('video').srcObject, environment.cameraStream);
  assert.equal(start.textContent, 'GPSni qayta tekshirish →');
});

test('Stop during the automatic-capture countdown cancels upload and camera use', async () => {
  const environment = createWebView({platform: 'ios', useTelegramLocation: true});
  const start = environment.elements.get('start-button');
  await start.click();
  await start.click();
  assert.equal(environment.elements.get('start-button').hidden, true);
  await environment.elements.get('stop-button').click();
  await new Promise((resolve) => nativeSetTimeout(resolve, 60));
  assert.equal(environment.attendanceCalls(), 0, 'stopping before the countdown ends must not submit attendance');
  assert.equal(environment.track.stopCalls, 1);
  assert.equal(environment.elements.get('video-wrap').classList.contains('confirmed'), false);
  assert.match(environment.elements.get('notice').textContent, /to‘xtatildi/i);
});
