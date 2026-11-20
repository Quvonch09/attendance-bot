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

function createWebView({
  platform = 'ios', autoStart = false, token = 'opaque-one-use-token', action = 'checkin',
  challengeError = '', challengeLocationError = '', attendanceError = '',
  cameraDenials = 0, faceMode = 'moving',
} = {}) {
  const elements = new Map();
  const getElement = (id) => {
    if (!elements.has(id)) elements.set(id, makeElement(id));
    return elements.get(id);
  };
  getElement('action-select').value = action;
  let getUserMediaCalls = 0;
  let challengeCalls = 0;
  let attendanceCalls = 0;
  let greenBeforeAttendanceResponse = false;
  let canvasDraws = 0;
  let detectorFrames = 0;
  let closeCalls = 0;
  const challengeBodies = [];
  const attendanceForms = [];
  const canvasSizes = [];
  const scheduledDelays = [];
  const track = {
    readyState: 'live', stopCalls: 0, listeners: {},
    stop() { this.stopCalls += 1; this.readyState = 'ended'; },
    addEventListener(name, callback) { this.listeners[name] = callback; },
  };
  const cameraStream = {active: true, getTracks() { return [track]; }, getVideoTracks() { return [track]; }};
  const webApp = {
    initData: 'signed-test-init-data', initDataUnsafe: {user: {first_name: 'Test'}}, platform,
    ready() {}, expand() {}, close() { closeCalls += 1; },
  };
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
  const location = {
    search: `?action=${action}`,
    hash: token ? `#native_location_token=${token}${autoStart ? '&autostart=1' : ''}` : '',
    origin: 'https://attendance.example', pathname: '/',
  };
  const window = {
    Telegram: {WebApp: webApp}, FaceDetection: MockFaceDetection,
    ATTENDANCE_API_BASE: 'https://attendance.example', location,
    history: {replaceState(_state, _title, url) { location.hash = String(url).includes('#') ? `#${String(url).split('#')[1]}` : ''; }},
  };
  const navigator = {
    userAgent: platform === 'ios' ? 'Mozilla/5.0 (iPhone; CPU iPhone OS 27_0 like Mac OS X)' : 'Mozilla/5.0 (Linux; Android 15)',
    platform: platform === 'ios' ? 'iPhone' : 'Linux armv8l', maxTouchPoints: 1,
    mediaDevices: {
      async getUserMedia() {
        getUserMediaCalls += 1;
        if (cameraDenials > 0) {
          cameraDenials -= 1;
          const error = Error('Permission denied'); error.name = 'NotAllowedError'; throw error;
        }
        return cameraStream;
      },
    },
  };
  const document = {
    getElementById: getElement,
    querySelector() { return null; },
    createElement() {
      const canvas = {
        width: 0, height: 0,
        getContext() {
          return {save() {}, translate() {}, scale() {}, drawImage() { canvasDraws += 1; }, restore() {}};
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
    if (String(url).includes('/api/challenge')) {
      challengeCalls += 1;
      assert.equal(options.method, 'POST');
      const body = JSON.parse(options.body);
      challengeBodies.push(body);
      assert.equal(body.native_location_token, token);
      if (challengeError) return response({error: challengeError}, 409);
      if (challengeLocationError) return response({error: challengeLocationError, distance_m: 316, accuracy_m: 200}, 422);
      return response({
        challenge_id: 'one-use-test-challenge', instruction: 'Kameraga qarab chapga buring',
        location_verified: true, distance_m: 70, accuracy_m: 20,
      });
    }
    if (String(url).includes('/api/attendance')) {
      attendanceCalls += 1;
      const fields = options.body.fields;
      attendanceForms.push(fields);
      greenBeforeAttendanceResponse = getElement('video-wrap').classList.contains('confirmed');
      assert.equal(fields.filter((field) => field.name === 'frames').length, 5);
      assert.equal(fields.find((field) => field.name === 'challenge_id').value, 'one-use-test-challenge');
      assert.equal(fields.find((field) => field.name === 'action').value, action);
      assert.equal(fields.some((field) => ['latitude', 'longitude', 'accuracy_m'].includes(field.name)), false);
      if (attendanceError) return response({error: attendanceError}, 422);
      return response({ok: true, message: 'Davomat serverda qayd etildi.'});
    }
    throw new Error(`unexpected request: ${url}`);
  };
  const fastSetTimeout = (callback, ms, ...args) => {
    scheduledDelays.push(ms);
    return nativeSetTimeout(callback, ms === 1800 ? 40 : 0, ...args);
  };
  vm.runInNewContext(APP_JS, {
    window, navigator, document, URLSearchParams, FormData: MockFormData,
    fetch, setTimeout: fastSetTimeout, clearTimeout: nativeClearTimeout,
    Promise, Error, console,
  }, {filename: 'webapp/app.js'});
  return {
    elements, element: getElement,
    getUserMediaCalls: () => getUserMediaCalls,
    challengeCalls: () => challengeCalls,
    attendanceCalls: () => attendanceCalls,
    detectorFrames: () => detectorFrames,
    greenBeforeAttendanceResponse: () => greenBeforeAttendanceResponse,
    canvasDraws: () => canvasDraws,
    canvasSizes, attendanceForms, challengeBodies, scheduledDelays,
    track, closeCalls: () => closeCalls, cameraStream,
  };
}

async function waitUntil(predicate, description, timeoutMs = 1500) {
  const deadline = Date.now() + timeoutMs;
  while (!predicate()) {
    if (Date.now() >= deadline) throw new Error(`timed out waiting for ${description}`);
    await new Promise((resolve) => nativeSetTimeout(resolve, 5));
  }
}

async function checkSuccessfulFlow(environment, {automatic = false} = {}) {
  if (!automatic) await environment.elements.get('start-button').click();
  await waitUntil(() => environment.attendanceCalls() === 1, 'automatic attendance upload');
  await waitUntil(() => environment.elements.get('video-wrap').classList.contains('confirmed'), 'server-confirmed face state');
  assert.equal(environment.challengeCalls(), 1);
  assert.equal(environment.getUserMediaCalls(), 1);
  assert.equal(environment.greenBeforeAttendanceResponse(), false, 'green state must wait for server success');
  assert.equal(environment.elements.get('capture-button').hidden, true, 'manual capture remains hidden');
  assert.equal(environment.elements.get('start-button').hidden, true);
  assert.match(environment.elements.get('location-status').textContent, /Telegram chatidagi lokatsiya tasdiqlandi/);
  assert.match(environment.elements.get('capture-status').textContent, /Server tasdiqladi/);
  assert.equal(environment.canvasDraws(), 5, 'five frames are automatically captured after face alignment and movement');
  assert.equal(environment.detectorFrames() >= 6, true, 'the detector checks face position and visible movement');
  assert.deepEqual(environment.canvasSizes[0], {width: 480, height: 270, type: 'image/jpeg', quality: 0.8});
  assert.equal(environment.scheduledDelays.includes(3000), false, 'there is no three-second countdown');
  assert.equal(environment.attendanceForms[0].filter((field) => field.name === 'frames').length, 5);
  assert.equal(environment.challengeBodies[0].native_location_token, 'opaque-one-use-token');
  await waitUntil(() => environment.track.stopCalls === 1, 'camera cleanup after confirmation');
}

test('iOS Mini App automatically verifies native chat location, opens camera, and submits one face sequence', async () => {
  const environment = createWebView({platform: 'ios', autoStart: true});
  await checkSuccessfulFlow(environment, {automatic: true});
});

test('Android manual start fallback reuses the same one-use challenge after camera permission is denied once', async () => {
  const environment = createWebView({platform: 'android', cameraDenials: 1});
  await environment.elements.get('start-button').click();
  assert.equal(environment.challengeCalls(), 1, 'location token is redeemed before camera request');
  assert.equal(environment.getUserMediaCalls(), 1);
  assert.equal(environment.elements.get('start-button').hidden, false);
  assert.match(environment.elements.get('notice').textContent, /Kameraga ruxsat berilmadi/i);
  const retry = environment.elements.get('start-button').click();
  await waitUntil(() => environment.attendanceCalls() === 1, 'attendance after explicit camera permission retry');
  await retry;
  assert.equal(environment.challengeCalls(), 1, 'camera retry must not redeem the location token twice');
  assert.equal(environment.getUserMediaCalls(), 2);
  assert.equal(environment.elements.get('video-wrap').classList.contains('confirmed'), true);
});

test('server-side unreliable location reading blocks camera and face capture', async () => {
  const environment = createWebView({platform: 'ios', challengeLocationError: 'location_accuracy_insufficient'});
  await environment.elements.get('start-button').click();
  assert.equal(environment.challengeCalls(), 1);
  assert.equal(environment.getUserMediaCalls(), 0, 'camera must wait until server revalidates the chat location');
  assert.equal(environment.detectorFrames(), 0);
  assert.equal(environment.attendanceCalls(), 0);
  assert.match(environment.elements.get('notice').textContent, /juda noaniq/i);
  assert.match(environment.elements.get('notice').textContent, /±200 m/);
  assert.doesNotMatch(environment.elements.get('notice').textContent, /ofisning 100 m radiusidan tashqarida/i);
  assert.equal(environment.elements.get('video-wrap').classList.contains('confirmed'), false);
});

test('a missing native location token cannot start the camera or request an API challenge', async () => {
  const environment = createWebView({platform: 'ios', token: ''});
  await environment.elements.get('start-button').click();
  assert.equal(environment.challengeCalls(), 0);
  assert.equal(environment.getUserMediaCalls(), 0);
  assert.match(environment.elements.get('notice').textContent, /bot chatiga qayting/i);
});

test('face-match failure never turns the guide green and sends the user back for a fresh location', async () => {
  const environment = createWebView({platform: 'ios', attendanceError: 'face_match_failed'});
  await environment.elements.get('start-button').click();
  await waitUntil(() => environment.attendanceCalls() === 1, 'face-match response');
  assert.match(environment.elements.get('notice').textContent, /profil suratiga mos kelmadi/i);
  assert.equal(environment.elements.get('video-wrap').classList.contains('confirmed'), false);
  assert.equal(environment.elements.get('retry-button').hidden, false);
  await environment.elements.get('retry-button').click();
  assert.equal(environment.closeCalls(), 1);
  assert.match(environment.elements.get('notice').textContent, /bot chatiga qaytib/i);
});

test('movement-check failure is distinct and does not enable a second submission from the same token', async () => {
  const environment = createWebView({platform: 'android', attendanceError: 'motion_check_failed'});
  await environment.elements.get('start-button').click();
  await waitUntil(() => environment.attendanceCalls() === 1, 'movement-check response');
  assert.match(environment.elements.get('notice').textContent, /harakat sezilmadi/i);
  assert.equal(environment.elements.get('video-wrap').classList.contains('confirmed'), false);
  await environment.elements.get('retry-button').click();
  assert.equal(environment.challengeCalls(), 1);
  assert.equal(environment.attendanceCalls(), 1);
});

test('stopping face framing cancels upload and leaves the unused location challenge available for resume', async () => {
  const environment = createWebView({platform: 'ios', faceMode: 'none'});
  const pendingStart = environment.elements.get('start-button').click();
  await waitUntil(() => /Yuz topilmadi/.test(environment.element('capture-status').textContent), 'automatic face framing');
  await environment.elements.get('stop-button').click();
  await pendingStart;
  assert.equal(environment.attendanceCalls(), 0);
  assert.equal(environment.track.stopCalls, 1);
  assert.equal(environment.elements.get('video-wrap').classList.contains('confirmed'), false);
  assert.match(environment.elements.get('notice').textContent, /qayta davom ettirishingiz mumkin/i);
  assert.equal(environment.elements.get('start-button').textContent, 'Davom etish →');
});

test('sequence rejection occurs before camera startup and no face frames are submitted', async () => {
  const environment = createWebView({platform: 'android', challengeError: 'sequence_invalid', action: 'lunch_out'});
  await environment.elements.get('start-button').click();
  assert.equal(environment.challengeCalls(), 1);
  assert.equal(environment.getUserMediaCalls(), 0);
  assert.equal(environment.detectorFrames(), 0);
  assert.equal(environment.attendanceCalls(), 0);
  assert.match(environment.elements.get('motion-status').textContent, /oldingi davomat amali qayd etilmagan/i);
  assert.equal(environment.elements.get('retry-button').hidden, false);
});
