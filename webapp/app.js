(() => {
  const $ = (id) => document.getElementById(id);
  const tg = window.Telegram && window.Telegram.WebApp;
  if (tg) { tg.ready(); tg.expand(); }
  const initData = tg && typeof tg.initData === 'string' ? tg.initData : '';
  const user = tg && tg.initDataUnsafe && tg.initDataUnsafe.user;
  if (user && user.first_name) $('greeting').textContent = `Xush kelibsiz, ${user.first_name}`;
  const api = String(window.ATTENDANCE_API_BASE || '').trim().replace(/\/$/, '');
  const FACE_DETECTION_VERSION = 'v0.4.1646425229';
  const launchParams = new URLSearchParams(String(window.location.hash || '').replace(/^#/, ''));
  let nativeLocationToken = launchParams.get('native_location_token') || '';
  const autoStart = launchParams.get('autostart') === '1';
  let stream = null, challenge = null, busy = false, stopped = false, challengeConsumed = false;
  let session = 0, successTimer = null, faceDetector = null, faceDetectorPromise = null;
  const action = () => $('action-select').value;
  const labels = {checkin:'kelish', lunch_out:'tushlikka chiqish', lunch_in:'tushlikdan qaytish', checkout:'ishdan chiqish'};
  const requestedAction = new URLSearchParams(window.location.search).get('action');
  if (Object.prototype.hasOwnProperty.call(labels, requestedAction)) {
    $('action-select').value = requestedAction;
    $('action-select').disabled = true;
    const actionLabel = document.querySelector('label[for="action-select"]');
    if (actionLabel) actionLabel.textContent = 'Tanlangan davomat amali';
  }
  const setNotice = (text, kind = '') => { const n = $('notice'); n.textContent = text; n.className = `notice ${kind}`.trim(); };
  const markDone = (id) => $(id).classList.add('done');
  const headers = () => ({'X-Telegram-Init-Data': initData});
  const sessionActive = (id) => !stopped && id === session;

  function isIOS(){
    const platform = String((tg && tg.platform) || '').toLowerCase();
    const ua = String(navigator.userAgent || '').toLowerCase();
    return platform === 'ios' || /iphone|ipad|ipod/.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  }
  function apiError(code, status, data = {}){
    if (code === 'location_accuracy_insufficient'){
      const accuracy = Number(data.accuracy_m);
      const accuracyText = Number.isFinite(accuracy) && accuracy > 0 ? ` Telegram bildirgan aniqlik taxminan ±${Math.round(accuracy)} m.` : ' Telegram bu safar aniqlik qiymatini bermadi.';
      return `Telegram lokatsiyasi juda noaniq keldi.${accuracyText} Davomat qayd etilmadi. Joylashuv xizmatini (GPS) yoqing va bot chatidagi «Joriy lokatsiyani yuborish» tugmasini kirish eshigiga yaqin joyda qayta bosing.`;
    }
    if (code === 'outside_office_radius'){
      const distance = Number(data.distance_m);
      const distanceText = Number.isFinite(distance) ? ` Server hisoblagan masofa taxminan ${Math.round(distance)} m.` : '';
      const accuracy = Number(data.accuracy_m);
      const accuracyText = Number.isFinite(accuracy) && accuracy > 0 ? ` Aniqlik ±${Math.round(accuracy)} m.` : '';
      return `Telegram chatdagi lokatsiya ofisning 100 m radiusidan tashqarida.${distanceText}${accuracyText} Ochiq joyda Telegram lokatsiya tugmasidan qayta yuboring. Davomat qayd etilmadi.`;
    }
    return ({
      unauthorized:'Telegram tekshiruvi o‘tmadi. Web App-ni bot ichidan qayta oching.',
      not_approved:'Ro‘yxatdagi yuzingiz hali admin yoki nazoratchi tomonidan tasdiqlanmagan.',
      native_location_required:'Avval bot chatida davomat amalini tanlab, joriy Telegram lokatsiyasini yuboring.',
      native_location_invalid:'Lokatsiya tasdiq kaliti muddati tugagan yoki ishlatilgan. Bot chatga qaytib joriy lokatsiyani qayta yuboring.',
      already_checked_in:'Bugungi kelishingiz avval qayd etilgan.', duplicate_action:'Bu amal bugun allaqachon qayd etilgan; qayta yuborib bo‘lmaydi.', sequence_invalid:'Oldingi davomat amali qayd etilmagan. Tushlikka chiqishdan oldin “Ishga kelish”, tushlikdan qaytishdan oldin “Tushlikka chiqish” qaydi bo‘lishi kerak.', invalid_action:'Noto‘g‘ri davomat amali.',
      late_reason_required:'09:30 dan keyin kelayotgan bo‘lsangiz, bot chatiga kechikish sababini matn yoki ovozda yuboring, keyin qayta urinib ko‘ring.',
      motion_check_failed:'Yuz kadrda aniq ko‘rinmadi yoki topshiriqdagi harakat sezilmadi. Yuzni ramkada ushlab, ko‘rsatilgan tomonga sekin buring va yorug‘ joyda qayta urinib ko‘ring. Bu oddiy harakat tekshiruvi, sertifikatlangan liveness emas.',
      face_match_failed:'Kadrdagi yuz tasdiqlangan profil suratiga mos kelmadi. Yuzni to‘liq va yorug‘ joyda ko‘rsating. Qaytalansa, administrator profil suratini tekshirsin.',
      face_check_failed:'Yuz yoki harakat tekshiruvi o‘tmadi. Yuzni yaxshi yoritilgan joyda ko‘rsating va ko‘rsatilgan harakatni bajaring.',
      challenge_expired:'Harakat topshirig‘i muddati tugadi. Tekshiruvni qayta boshlang.',
      service_unavailable:'Davomat API hali sozlanmagan. Birozdan so‘ng qayta urinib ko‘ring.',
      origin_denied:'Web App manzili API tomonidan ruxsat etilmagan.',
      location_unavailable:'Joylashuvni tekshirib bo‘lmadi. GPS-ni yoqib, ochiq joyda qayta urinib ko‘ring.'
    })[code] || `Tekshiruv serverda bajarilmadi (${status}). Qayta urinib ko‘ring.`;
  }
  function cameraErrorMessage(error){
    const name = error && error.name;
    if (name === 'CameraPermissionTimeoutError') return 'Kamera ruxsati so‘roviga 30 soniyada javob kelmadi. Ruxsat oynasi chiqsa, ruxsat bering; chiqmasa Telegram-ni to‘liq yopib qayta oching.';
    if (name === 'NotAllowedError' || name === 'PermissionDeniedError') return isIOS()
      ? 'Kameraga ruxsat berilmadi. iPhone’da Sozlamalar → Maxfiylik va xavfsizlik → Kamera → Telegram ruxsatini yoqing, so‘ng Mini App-ni qayta oching.'
      : 'Kameraga ruxsat berilmadi. Android’da Sozlamalar → Ilovalar → Telegram → Ruxsatlar → Kamera-ni yoqing, so‘ng Mini App-ni qayta oching.';
    if (name === 'NotFoundError' || name === 'DevicesNotFoundError') return 'Old kamera topilmadi. Qurilmada kamera borligini tekshiring.';
    if (name === 'NotReadableError' || name === 'TrackStartError') return 'Kamera boshqa ilovada band yoki ochilmadi. Kamera ishlatayotgan ilovalarni yoping va qayta urinib ko‘ring.';
    if (name === 'CameraPlaybackError' || name === 'CameraFrameTimeoutError' || name === 'CameraStreamEndedError') return error.message || 'Kamera tasviri to‘xtadi. Tekshiruvni qayta boshlang.';
    return error.message || 'Kamera ochilmadi. Qurilma sozlamalarida Telegram uchun kameraga ruxsatni tekshiring.';
  }
  function faceError(message, name = 'FaceDetectorError'){
    const error = Error(message); error.name = name; error.faceFailure = true; return error;
  }
  function waitWithTimeout(promise, ms, name, message, flag){
    let timer;
    const timeout = new Promise((_, reject) => { timer = setTimeout(() => { const error = Error(message); error.name = name; if (flag) error[flag] = true; reject(error); }, ms); });
    return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
  }
  function waitForCamera(promise){
    let timer, timedOut = false;
    const timeout = new Promise((_, reject) => { timer = setTimeout(() => { timedOut = true; const error = Error('Telegram WebView kamera ruxsati haqida javob bermadi.'); error.name = 'CameraPermissionTimeoutError'; error.cameraFailure = true; reject(error); }, 30000); });
    promise.then(cameraStream => { if (timedOut) cameraStream.getTracks().forEach(track => track.stop()); }, () => {});
    return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
  }
  function hasLiveCamera(){
    if (!stream || typeof stream.getVideoTracks !== 'function') return false;
    return stream.getVideoTracks().some(track => track.readyState === 'live');
  }
  function stopCamera(){
    if (successTimer !== null) { clearTimeout(successTimer); successTimer = null; }
    const current = stream; stream = null;
    if (current) current.getTracks().forEach(track => track.stop());
    const video = $('video'); if (typeof video.pause === 'function') video.pause(); video.srcObject = null;
    $('video-wrap')?.classList.remove('ready', 'aligned', 'confirmed'); $('camera-placeholder').hidden = false; $('stop-button').hidden = true;
  }
  function watchCameraStream(cameraStream){
    cameraStream.getVideoTracks().forEach(track => {
      if (typeof track.addEventListener === 'function') track.addEventListener('ended', () => {
        if (stream !== cameraStream) return;
        stopCamera(); $('camera-status').textContent = 'Kamera oqimi to‘xtadi. Qayta boshlang.';
        if (!busy) { $('start-button').hidden = false; $('start-button').disabled = false; $('start-button').textContent = 'Kamerani qayta boshlash →'; setNotice('Qurilma kamera oqimini to‘xtatdi. Kamerani qayta boshlang.', 'error'); }
      }, {once: true});
    });
  }
  function waitForVideoFrame(video){
    return new Promise((resolve, reject) => {
      if (video.readyState >= 2 && video.videoWidth > 0) return resolve();
      let timer; const finish = (fn, value) => { clearTimeout(timer); video.removeEventListener('loadeddata', onFrame); video.removeEventListener('canplay', onFrame); fn(value); };
      const onFrame = () => { if (video.readyState >= 2 && video.videoWidth > 0) finish(resolve); };
      timer = setTimeout(() => { const error = Error('Kamera ulandi, lekin video kadr kelmadi.'); error.name = 'CameraFrameTimeoutError'; error.cameraFailure = true; finish(reject, error); }, 8000);
      video.addEventListener('loadeddata', onFrame); video.addEventListener('canplay', onFrame); onFrame();
    });
  }
  function createFaceDetector(){
    const exported = window.FaceDetection;
    const Constructor = typeof exported === 'function' ? exported : exported && exported.FaceDetection;
    if (typeof Constructor !== 'function') return Promise.reject(faceError('Qurilmadagi yuz aniqlash moduli yuklanmadi. Internetni tekshirib, Mini App-ni qayta oching.', 'FaceDetectorLoadError'));
    let raw, pendingResult = null;
    try {
      raw = new Constructor({locateFile: file => {
        const filename = String(file).split(/[\\/]/).pop().replace('face_detection_solution_simd_wasm_bin', 'face_detection_solution_wasm_bin');
        return `${window.location.origin}/face-detection/${FACE_DETECTION_VERSION}/${filename}`;
      }});
      raw.setOptions({model: 'short', minDetectionConfidence: 0.65, selfieMode: true});
      raw.onResults(result => { if (pendingResult) { const resolve = pendingResult; pendingResult = null; resolve(result); } });
    } catch (error) { return Promise.reject(faceError(error.message || 'Yuz aniqlash moduli ishga tushmadi.'));
    }
    const detector = {
      async detect(video){
        let resolveResult, rejectResult;
        const resultPromise = new Promise((resolve, reject) => { resolveResult = resolve; rejectResult = reject; });
        pendingResult = resolveResult;
        const sendPromise = Promise.resolve().then(() => raw.send({image: video})).catch(error => { if (pendingResult === resolveResult) pendingResult = null; rejectResult(error); });
        try {
          const [, result] = await waitWithTimeout(Promise.all([sendPromise, resultPromise]), 8000, 'FaceDetectionTimeoutError', 'Yuz aniqlash javobi 8 soniyada kelmadi.', 'faceFailure');
          return Array.isArray(result && result.detections) ? result.detections : [];
        } catch (error) {
          if (!error.faceFailure) { error.faceFailure = true; }
          throw error;
        } finally { if (pendingResult === resolveResult) pendingResult = null; }
      },
      close(){ return raw.close(); }
    };
    return waitWithTimeout(Promise.resolve(raw.initialize()).then(() => detector), 20000, 'FaceDetectorInitTimeoutError', 'Yuz aniqlash modeli 20 soniyada yuklanmadi. Internetni tekshirib, qayta urinib ko‘ring.', 'faceFailure').catch(error => {
      if (raw && typeof raw.close === 'function') Promise.resolve(raw.close()).catch(() => {});
      if (!error.faceFailure) error.faceFailure = true;
      throw error;
    });
  }
  async function ensureFaceDetector(sessionId){
    if (faceDetector) return sessionActive(sessionId) ? faceDetector : null;
    if (!faceDetectorPromise) faceDetectorPromise = createFaceDetector();
    const pending = faceDetectorPromise;
    try {
      const detector = await pending;
      if (faceDetectorPromise === pending) faceDetector = detector;
      return sessionActive(sessionId) ? detector : null;
    } catch (error) {
      if (faceDetectorPromise === pending) faceDetectorPromise = null;
      throw error;
    }
  }
  function faceMetrics(detection, video){
    const box = detection && detection.boundingBox;
    const videoWidth = Number(video.videoWidth), videoHeight = Number(video.videoHeight);
    if (!box || !videoWidth || !videoHeight) return null;
    const x = Number(box.xCenter), y = Number(box.yCenter), width = Number(box.width), height = Number(box.height);
    if (![x, y, width, height].every(Number.isFinite) || width <= 0 || height <= 0) return null;
    const scale = Math.min(1, 720 / Math.max(videoWidth, videoHeight));
    return {centerX: x * videoWidth * scale, centerY: y * videoHeight * scale,
      faceWidth: width * videoWidth * scale, widthRatio: width, heightRatio: height,
      centerXRatio: x, centerYRatio: y, frameWidth: videoWidth * scale};
  }
  function faceIsPositioned(metrics){
    return metrics.widthRatio >= 0.18 && metrics.widthRatio <= 0.75 && metrics.heightRatio >= 0.20 && metrics.heightRatio <= 0.85 &&
      metrics.centerXRatio >= 0.22 && metrics.centerXRatio <= 0.78 && metrics.centerYRatio >= 0.18 && metrics.centerYRatio <= 0.82;
  }
  function faceIsFrontal(detection){
    const points = detection && detection.landmarks;
    if (!Array.isArray(points) || points.length < 3) return false;
    const first = points[0], second = points[1], nose = points[2];
    if (![first.x, first.y, second.x, second.y, nose.x].every(Number.isFinite)) return false;
    const eyeSpan = Math.abs(first.x - second.x);
    if (eyeSpan < 0.035) return false;
    const eyeMid = (first.x + second.x) / 2;
    const noseOffset = Math.abs(nose.x - eyeMid) / eyeSpan;
    const eyeTilt = Math.abs(first.y - second.y) / Math.max(Number(detection.boundingBox && detection.boundingBox.height) || 0, 0.01);
    return noseOffset <= 0.50 && eyeTilt <= 0.28;
  }
  function motionRangeExceeded(metrics){
    if (metrics.length < 2) return false;
    const centers = metrics.map(item => item.centerX), widths = metrics.map(item => item.faceWidth);
    return Math.max(...centers) - Math.min(...centers) >= 24 || Math.max(...widths) - Math.min(...widths) >= 12;
  }
  function captureFrame(video, canvas, ctx){
    if (stopped) return Promise.reject(Error('Tekshiruv to‘xtatildi.'));
    ctx.save(); ctx.translate(canvas.width, 0); ctx.scale(-1, 1); ctx.drawImage(video, 0, 0, canvas.width, canvas.height); ctx.restore();
    return new Promise((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(Error('JPEG kadr tayyorlanmadi.')), 'image/jpeg', 0.72));
  }
  async function waitForFaceSequence(video, detector, sessionId){
    const sourceWidth = Number(video.videoWidth) || 640, sourceHeight = Number(video.videoHeight) || 480;
    const scale = Math.min(1, 720 / Math.max(sourceWidth, sourceHeight));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(sourceWidth * scale)); canvas.height = Math.max(1, Math.round(sourceHeight * scale));
    const ctx = canvas.getContext('2d');
    if (!ctx) throw faceError('Kamera kadrini tayyorlab bo‘lmadi. Qayta urinib ko‘ring.');
    const startedAt = Date.now();
    let baseline = null, baselineBlob = null, recent = [], frontalFrames = 0, missedFrames = 0, iteration = 0;
    const resetAlignment = () => { baseline = null; baselineBlob = null; recent = []; frontalFrames = 0; $('video-wrap').classList.remove('aligned'); $('capture-count').textContent = '0 / 5'; $('progress').style.width = '0'; };
    while (Date.now() - startedAt < 35000){
      if (!sessionActive(sessionId)) return null;
      if (!hasLiveCamera()) { const error = Error('Kamera oqimi qurilma tomonidan to‘xtatildi.'); error.name = 'CameraStreamEndedError'; error.cameraFailure = true; throw error; }
      let detections;
      try { detections = await detector.detect(video); }
      catch (error) { if (!sessionActive(sessionId)) return null; throw error; }
      if (!sessionActive(sessionId)) return null;
      iteration++;
      if (!Array.isArray(detections) || detections.length !== 1){
        missedFrames++;
        if (detections && detections.length > 1){ resetAlignment(); $('capture-status').textContent = 'Kadrda faqat bitta yuz bo‘lishi kerak'; }
        else {
          if (missedFrames >= 2) resetAlignment();
          $('capture-status').textContent = 'Yuz topilmadi · yorug‘roq joyda kameraga qarang';
        }
        await new Promise(resolve => setTimeout(resolve, 100)); continue;
      }
      missedFrames = 0;
      const detection = detections[0], metrics = faceMetrics(detection, video);
      if (!metrics || !faceIsPositioned(metrics)){
        resetAlignment(); $('capture-status').textContent = 'Yuzni oval ramkaning markaziga joylang';
        await new Promise(resolve => setTimeout(resolve, 100)); continue;
      }
      if (!baseline){
        if (!faceIsFrontal(detection)){
          frontalFrames = 0; $('video-wrap').classList.remove('aligned'); $('capture-status').textContent = 'Yuzni kameraga to‘g‘ri qarating';
        } else {
          frontalFrames++;
          if (frontalFrames >= 3){
            baseline = metrics; baselineBlob = await captureFrame(video, canvas, ctx); recent = [];
            $('video-wrap').classList.add('aligned'); $('capture-count').textContent = '1 / 5'; $('progress').style.width = '20%';
            $('capture-status').textContent = 'Yuz joyida · ko‘rsatilgan tomonga sekin buring';
          } else $('capture-status').textContent = 'Yuz aniqlandi · markazda va kameraga qarab turing';
        }
      } else {
        const movedBeforeSample = motionRangeExceeded([baseline, ...recent.map(item => item.metrics), metrics]);
        if (iteration % 2 === 0 || movedBeforeSample){
          const blob = await captureFrame(video, canvas, ctx); recent.push({metrics, blob}); if (recent.length > 4) recent.shift();
        }
        const moved = motionRangeExceeded([baseline, ...recent.map(item => item.metrics), metrics]);
        $('capture-count').textContent = `${Math.min(5, recent.length + 1)} / 5`;
        $('progress').style.width = `${Math.min(100, (recent.length + 1) * 20)}%`;
        if (moved && recent.length >= 4){
          $('capture-status').textContent = 'Harakat sezildi · 5 kadr serverga yuborilmoqda…';
          return [baselineBlob, ...recent.map(item => item.blob)];
        }
        $('capture-status').textContent = moved ? 'Harakat sezildi · kadrlar ketma-ketligi olinmoqda…' : 'Yuz joyida · ko‘rsatilgan tomonga sekin buring';
      }
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    throw faceError('Yuz ramkada barqaror ko‘rinmadi yoki harakat topshirig‘i bajarilmadi. Yuzni markazga joylab, ko‘rsatilgan tomonga sekin buring va qayta urinib ko‘ring.', 'FaceAlignmentTimeoutError');
  }
  function removeNativeLocationTokenFromUrl(){
    if (!window.history || typeof window.history.replaceState !== 'function') return;
    const params = new URLSearchParams(String(window.location.hash || '').replace(/^#/, ''));
    params.delete('native_location_token'); params.delete('autostart');
    const hash = params.toString();
    window.history.replaceState(null, '', `${window.location.pathname || '/'}${window.location.search || ''}${hash ? `#${hash}` : ''}`);
  }
  async function start(){
    if (busy) return;
    if (!initData) { setNotice('Telegram Mini App ichida oching. Brauzer preview rejimi autentifikatsiya qilmaydi va check-in yubora olmaydi.', 'error'); return; }
    if (!api) { setNotice('API ulanishi sozlanmagan: webapp/config.js faylida ATTENDANCE_API_BASE qiymatini kiriting.', 'error'); return; }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) { const error = Error('Bu Telegram WebView’da kamera API mavjud emas. Telegram-ni yangilang va Mini App-ni qayta oching.'); error.cameraFailure = true; setNotice(cameraErrorMessage(error), 'error'); return; }
    if (!challenge && !nativeLocationToken) {
      setNotice('Bot chatiga qayting, davomat amalini tanlang va Telegram joriy lokatsiyasini yuboring.', 'error');
      return;
    }
    const thisSession = ++session;
    busy = true; stopped = false; $('start-button').disabled = true;
    setNotice(challenge ? 'Telegram lokatsiyasi serverda tasdiqlangan. Kamera ishga tushmoqda.' : 'Telegram chatdagi lokatsiya serverda qayta tekshirilmoqda.');
    let cameraPromise = null;
    try {
      if (!challenge){
        let response;
        try {
          response = await fetch(`${api}/api/challenge`, {
            method: 'POST', headers: {...headers(), 'Content-Type': 'application/json'},
            body: JSON.stringify({action: action(), native_location_token: nativeLocationToken}),
          });
        } catch (_) {
          const error = Error('Lokatsiya tasdiq serveriga ulanib bo‘lmadi. Internetni tekshirib, bot chatidan joriy lokatsiyani qayta yuboring.');
          error.challengeFailure = true; error.challengeCode = 'network_error'; throw error;
        }
        let challengeData = {}; try { challengeData = await response.json(); } catch (_) {}
        if (!response.ok){
          const error = Error(apiError(challengeData.error, response.status, challengeData));
          error.challengeFailure = true; error.challengeCode = challengeData.error;
          error.locationFailure = ['outside_office_radius', 'location_accuracy_insufficient'].includes(challengeData.error);
          throw error;
        }
        challenge = challengeData;
        if (!challenge.challenge_id || !challenge.instruction || challenge.location_verified !== true){
          challenge = null;
          const error = Error('Server Telegram lokatsiyasini tasdiqlamadi. Bot chatiga qaytib, lokatsiyani qayta yuboring.');
          error.challengeFailure = true; error.challengeCode = 'native_location_invalid'; throw error;
        }
        challengeConsumed = false; nativeLocationToken = ''; removeNativeLocationTokenFromUrl();
        const verifiedAccuracy = Number(challenge.accuracy_m);
        $('location-status').textContent = `Telegram chatidagi lokatsiya tasdiqlandi · ofisdan taxminan ${challenge.distance_m} m` + (challenge.accuracy_m !== null && Number.isFinite(verifiedAccuracy) && verifiedAccuracy > 0 ? ` · aniqlik ±${Math.round(verifiedAccuracy)} m` : '');
        markDone('step-location'); $('instruction').textContent = challenge.instruction;
        $('motion-status').textContent = 'Yuz mosligi va harakat serverda tekshiriladi';
      }
      if (!hasLiveCamera()){
        if (stream) stopCamera();
        $('camera-status').textContent = 'Kameraga ulanish so‘ralmoqda…';
        cameraPromise = navigator.mediaDevices.getUserMedia({audio: false, video: {facingMode: {ideal: 'user'}, width: {ideal: 640}, height: {ideal: 480}}}).catch(error => { const wrapped = Error(error && error.message || 'Kamera ochilmadi.'); wrapped.name = error && error.name || 'Error'; wrapped.cameraFailure = true; throw wrapped; });
        cameraPromise.catch(() => {});
        const cameraStream = await waitForCamera(cameraPromise);
        if (!sessionActive(thisSession)) { cameraStream.getTracks().forEach(track => track.stop()); return; }
        stream = cameraStream; watchCameraStream(cameraStream);
      }
      const video = $('video'); video.muted = true; video.playsInline = true; if (video.srcObject !== stream) video.srcObject = stream;
      try { await video.play(); } catch (_) { const error = Error('Kamera ruxsati olindi, lekin video tasviri ishga tushmadi. Kamerani qayta boshlang.'); error.name = 'CameraPlaybackError'; error.cameraFailure = true; throw error; }
      await waitForVideoFrame(video);
      if (!sessionActive(thisSession)) return;
      if (!hasLiveCamera()) { const error = Error('Kamera oqimi qurilma tomonidan to‘xtatildi.'); error.name = 'CameraStreamEndedError'; error.cameraFailure = true; throw error; }
      $('camera-placeholder').hidden = true; $('video-wrap').classList.add('ready'); $('camera-status').textContent = 'Old kamera tayyor'; markDone('step-camera'); $('stop-button').hidden = false;
      setNotice('Lokatsiya serverda tasdiqlandi. Yuzingizni ramkaga joylang va ko‘rsatilgan harakatni bajaring.');
      $('start-button').hidden = true; $('capture-button').hidden = true; $('capture-button').disabled = true; $('retry-button').hidden = true;
      $('capture-count').textContent = '0 / 5'; $('capture-status').textContent = 'Yuz aniqlash moduli ishga tushmoqda…'; $('progress').style.width = '0';
      setNotice('Yuzingizni oval ramkaga joylang va ko‘rsatilgan tomonga sekin buring. Kadrlar kechiktiruvchi taymersiz avtomatik yuboriladi.');
      const detector = await ensureFaceDetector(thisSession);
      if (!sessionActive(thisSession) || !detector) return;
      const frames = await waitForFaceSequence(video, detector, thisSession);
      if (!sessionActive(thisSession) || !frames) return;
      await capture(frames, thisSession);
    } catch (error) {
      if (!sessionActive(thisSession)) return;
      if (cameraPromise) cameraPromise.then(pending => { if (pending !== stream && pending.getTracks().some(track => track.readyState === 'live')) pending.getTracks().forEach(track => track.stop()); }).catch(() => {});
      let message = error.cameraFailure ? cameraErrorMessage(error) : error.message || 'Tekshiruvni boshlashda xatolik.';
      if (error.cameraFailure || !hasLiveCamera()) stopCamera();
      else { $('camera-placeholder').hidden = true; $('video-wrap').classList.add('ready'); $('camera-status').textContent = 'Old kamera tayyor'; $('stop-button').hidden = false; }
      if (error.cameraFailure) { $('camera-status').textContent = message; $('start-button').hidden = false; $('start-button').textContent = 'Kamerani qayta ochish →'; }
      else if (error.challengeFailure) { $('motion-status').textContent = message; $('instruction').textContent = message; $('capture-count').textContent = '0 / 5'; $('capture-status').textContent = 'Lokatsiya yoki server topshirig‘i tasdiqlanmadi'; $('progress').style.width = '0'; $('retry-button').hidden = false; $('retry-button').textContent = 'Bot chatga qaytish'; $('start-button').hidden = true; }
      else if (error.faceFailure && challenge) { $('retry-button').hidden = false; $('retry-button').textContent = 'Qayta urinish'; $('start-button').hidden = true; $('motion-status').textContent = 'Yuzni avtomatik aniqlash qayta urinishi kerak'; }
      else { $('start-button').hidden = false; $('start-button').textContent = 'Qayta urinib ko‘rish →'; }
      busy = false; $('start-button').disabled = false; setNotice(message, 'error');
    }
  }
  async function capture(frames, sessionId){
    if (stopped || sessionId !== session || !hasLiveCamera() || !challenge || !Array.isArray(frames) || frames.length < 3) return;
    busy = true; $('capture-button').disabled = true; $('stop-button').hidden = false;
    try {
      $('capture-count').textContent = `${frames.length} / 5`; $('capture-status').textContent = 'Serverga yuborilmoqda…'; $('progress').style.width = '100%';
      setNotice('Kadrlar serverga yuborilmoqda; yuz va harakat serverda tekshiriladi.');
      const form = new FormData();
      form.append('challenge_id', challenge.challenge_id); form.append('action', action());
      frames.forEach((blob, index) => form.append('frames', blob, `frame-${index + 1}.jpg`));
      challengeConsumed = true;
      const response = await fetch(`${api}/api/attendance`, {method: 'POST', headers: headers(), body: form});
      let data = {}; try { data = await response.json(); } catch (_) {}
      if (!response.ok){
        const code = data.error || data.detail || data.message; const error = Error(apiError(code, response.status, data));
        challenge = null;
        throw error;
      }
      busy = false; markDone('step-motion'); $('capture-status').textContent = 'Server tasdiqladi'; $('progress').style.width = '100%';
      $('capture-button').hidden = true; $('stop-button').hidden = true; $('retry-button').hidden = true;
      $('video-wrap').classList.add('confirmed'); $('camera-status').textContent = 'Yuz tasdiqlandi'; $('motion-status').textContent = 'Davomat qayd etildi';
      setNotice(data.message || `${labels[action()].charAt(0).toUpperCase() + labels[action()].slice(1)} muvaffaqiyatli qayd etildi.`, 'success');
      successTimer = setTimeout(() => { successTimer = null; stopCamera(); }, 1800);
    } catch (error) {
      if (!sessionActive(sessionId)) return;
      busy = false; $('capture-button').hidden = true; $('retry-button').hidden = false; $('retry-button').textContent = 'Bot chatga qaytish'; $('stop-button').hidden = !hasLiveCamera(); $('start-button').hidden = true; challenge = null;
      let message = error.cameraFailure ? cameraErrorMessage(error) : error.message || 'Kadrlarni yuborishda xatolik.';
      if (error.cameraFailure || !hasLiveCamera()) stopCamera();
      setNotice(message, 'error');
    }
  }
  $('action-select').addEventListener('change', () => {
    session++; stopped = true; busy = false; $('start-button').textContent = 'Tekshiruvni boshlash →'; challenge = null; stopCamera();
    $('start-button').hidden = false; $('start-button').disabled = false; $('capture-button').hidden = true; $('retry-button').hidden = true;
    $('capture-count').textContent = '0 / 5'; $('capture-status').textContent = 'Yuz ramkaga joylashgach 5 kadr avtomatik olinadi'; $('progress').style.width = '0';
    setNotice(`${labels[action()]} uchun bot chatida joriy lokatsiyani qayta yuboring.`);
  });
  $('start-button').addEventListener('click', start);
  $('stop-button').addEventListener('click', () => {
    session++; stopped = true; stopCamera(); busy = false;
    if (challengeConsumed) challenge = null;
    $('capture-button').hidden = true; $('start-button').hidden = false; $('start-button').disabled = false; $('start-button').textContent = challenge ? 'Davom etish →' : 'Bot chatga qaytish'; $('retry-button').hidden = true;
    $('capture-count').textContent = '0 / 5'; $('capture-status').textContent = 'Yuz ramkaga joylashgach 5 kadr avtomatik olinadi'; $('progress').style.width = '0';
    setNotice(challenge ? 'Tekshiruv to‘xtatildi. Qayta davom ettirishingiz mumkin.' : 'Tekshiruv to‘xtatildi. Bot chatiga qaytib lokatsiyani qayta yuboring.');
  });
  $('retry-button').addEventListener('click', () => {
    if (challengeConsumed || !challenge){
      setNotice('Bot chatiga qaytib, joriy lokatsiyani qayta yuboring.');
      if (tg && typeof tg.close === 'function') tg.close();
      return;
    }
    $('retry-button').hidden = true; $('start-button').hidden = false; $('start-button').disabled = false; $('start-button').textContent = 'Qayta urinish →';
    void start();
  });
  if (!initData) setNotice('Telegram Mini App ichida oching. Brauzer preview rejimi autentifikatsiya qilmaydi va check-in yubora olmaydi.', 'error');
  else if (!api) setNotice('API ulanishi sozlanmagan: webapp/config.js faylida ATTENDANCE_API_BASE qiymatini kiriting.', 'error');
  else if (!nativeLocationToken) setNotice('Bot chatiga qayting, davomat amalini tanlang va Telegram joriy lokatsiyasini yuboring.', 'error');
  else if (autoStart) { $('start-button').textContent = 'Kamera avtomatik ochilmoqda…'; void start(); }
})();
