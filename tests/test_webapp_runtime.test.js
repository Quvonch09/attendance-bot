const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const APP_JS = fs.readFileSync(path.join(__dirname, '..', 'webapp', 'app.js'), 'utf8');

function makeElement(id) {
  const listeners = {};
  const classes = new Set();
  const element = {
    id,
    hidden: ['capture-button', 'retry-button', 'stop-button'].includes(id),
    disabled: false,
    value: 'checkin',
    textContent: '',
    className: '',
    listeners,
    classList: {
      add(name) { classes.add(name); },
      remove(name) { classes.delete(name); },
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
    element.videoWidth = 640;
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

function createWebView({platform, useTelegramLocation}) {
  const elements = new Map();
  const getElement = (id) => {
    if (!elements.has(id)) elements.set(id, makeElement(id));
    return elements.get(id);
  };
  let getUserMediaCalls = 0;
  let locationReads = 0;
  let browserLocationReads = 0;
  let locationCheckCalls = 0;
  const locationPayloads = [];
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
      callback({latitude: 38.8557873, longitude: 65.8002788, horizontal_accuracy: locationReads === 1 ? 350 : 20});
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
    createElement() { throw new Error('canvas is not needed for the start/location regression'); },
  };
  const fetch = async (url, options = {}) => {
    if (String(url).includes('/api/location-check')) {
      locationCheckCalls += 1;
      const payload = JSON.parse(options.body);
      locationPayloads.push(payload);
      assert.equal(payload.latitude, 38.8557873);
      assert.equal(payload.longitude, 65.8002788);
      if (locationCheckCalls === 1) {
        return response({error: 'location_accuracy_insufficient', distance_m: 280, accuracy_m: payload.accuracy_m}, 422);
      }
      return response({ok: true, distance_m: 230, accuracy_m: payload.accuracy_m});
    }
    if (String(url).includes('/api/challenge')) {
      return response({challenge_id: 'one-use-test-challenge', instruction: 'Chapga qarang'});
    }
    throw new Error(`unexpected request: ${url}`);
  };
  vm.runInNewContext(APP_JS, {
    window,
    navigator,
    document,
    URLSearchParams,
    fetch,
    setTimeout,
    clearTimeout,
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
    locationPayloads,
    track,
    cameraStream,
  };
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
  assert.equal(environment.elements.get('capture-button').hidden, false, 'a fresh accurate fix reaches the motion challenge');
  assert.equal(environment.elements.get('start-button').hidden, true);
  assert.match(environment.elements.get('location-status').textContent, /GPS ±20 m/);
}

test('iOS Telegram native LocationManager accuracy retry keeps the camera live', async () => {
  const environment = createWebView({platform: 'ios', useTelegramLocation: true});
  await exerciseAccuracyRetry(environment);
  assert.equal(environment.locationReads(), 2);
  assert.equal(environment.browserLocationReads(), 0);
  assert.equal(environment.locationPayloads[0].accuracy_m, 350);
  assert.equal(environment.locationPayloads[1].accuracy_m, 20);
});

test('Android browser geolocation accuracy retry keeps the camera live', async () => {
  const environment = createWebView({platform: 'android', useTelegramLocation: false});
  await exerciseAccuracyRetry(environment);
  assert.equal(environment.locationReads(), 0);
  assert.equal(environment.browserLocationReads(), 2);
  assert.equal(environment.locationPayloads[0].accuracy_m, 350);
  assert.equal(environment.locationPayloads[1].accuracy_m, 20);
});
