(() => {
  const $ = (id) => document.getElementById(id);
  const tg = window.Telegram && window.Telegram.WebApp;
  if (tg) { tg.ready(); tg.expand(); }
  const initData = tg && typeof tg.initData === 'string' ? tg.initData : '';
  const user = tg && tg.initDataUnsafe && tg.initDataUnsafe.user;
  if (user && user.first_name) $('greeting').textContent = `Xush kelibsiz, ${user.first_name}`;
  const api = String(window.ATTENDANCE_API_BASE || '').trim().replace(/\/$/, '');
  let stream = null, coords = null, challenge = null, busy = false, stopped = false;
  let autoCaptureTimer = null, successTimer = null;
  const action = () => $('action-select').value;
  const labels = {checkin:'kelish', lunch_out:'tushlikka chiqish', lunch_in:'tushlikdan qaytish', checkout:'ishdan chiqish'};
  const requestedAction = new URLSearchParams(window.location.search).get('action');
  if (Object.prototype.hasOwnProperty.call(labels, requestedAction)) {
    $('action-select').value = requestedAction;
    $('action-select').disabled = true;
    const actionLabel = document.querySelector('label[for="action-select"]');
    if (actionLabel) actionLabel.textContent = 'Tanlangan davomat amali';
  }
  const setNotice = (text, kind = '') => { const n=$('notice'); n.textContent=text; n.className=`notice ${kind}`.trim(); };
  const markDone = (id) => $(id).classList.add('done');
  const headers = () => ({'X-Telegram-Init-Data': initData});
  function isIOS(){
    const platform=String((tg&&tg.platform)||'').toLowerCase();
    const ua=String(navigator.userAgent||'').toLowerCase();
    return platform==='ios'||/iphone|ipad|ipod/.test(ua)||(navigator.platform==='MacIntel'&&navigator.maxTouchPoints>1);
  }
  function locationPermissionHelp(){
    return isIOS()
      ? 'iPhone’da Sozlamalar → Maxfiylik va xavfsizlik → Joylashuv xizmatlari → Telegram ichida “Aniq joylashuv”ni yoqing.'
      : 'Android’da Sozlamalar → Joylashuv → Google Location Accuracy-ni va Telegram uchun aniq joylashuv ruxsatini yoqing.';
  }
  function locationSourceLabel(source){
    return source==='telegram'?'Telegram LocationManager':source==='browser'?'Telegram WebView GPS':'';
  }
  function appendLocationSource(message,source){
    const label=locationSourceLabel(source);
    return label?`${message} GPS manbasi: ${label}.`:message;
  }
  function apiError(code, status, data = {}){
    if(code==='location_accuracy_insufficient'){
      const accuracy=Number(data.accuracy_m);
      const accuracyText=Number.isFinite(accuracy)&&accuracy>0?` Qurilma bildirgan aniqlik: taxminan ±${Math.round(accuracy)} m.`:'';
      return `GPS aniqligi 300 m chegarasiga nisbatan noaniq.${accuracyText} ${locationPermissionHelp()} Ochiq joyda 10–20 soniya kutib, “GPSni qayta tekshirish”ni bosing. Davomat qayd etilmadi; 300 m radius o‘zgarmagan.`;
    }
    if(code==='outside_office_radius'){
      const distance=Number(data.distance_m);
      const distanceText=Number.isFinite(distance)?` Server hisoblagan masofa taxminan ${Math.round(distance)} m.`:'';
      const accuracy=Number(data.accuracy_m);
      const accuracyText=Number.isFinite(accuracy)&&accuracy>0?` Qurilma bildirgan GPS aniqligi: ±${Math.round(accuracy)} m.`:' Qurilma bildirgan GPS aniqligi mavjud emas.';
      return `Qurilma yuborgan GPS nuqtasi ofisning 300 m radiusidan tashqarida.${distanceText}${accuracyText} Ruxsat yoqilgan bo‘lsa ham, ayni GPS o‘lchovi xato bo‘lishi mumkin. Agar ofisda bo‘lsangiz, ochiq joyda 10–20 soniya kutib, “GPSni qayta tekshirish”ni bosing. Davomat qayd etilmadi; radius o‘zgarmagan.`;
    }
    return ({
      unauthorized:'Telegram tekshiruvi o‘tmadi. Web App-ni bot ichidan qayta oching.',
      not_approved:'Ro‘yxatdagi yuzingiz hali admin yoki nazoratchi tomonidan tasdiqlanmagan.',
      already_checked_in:'Bugungi kelishingiz avval qayd etilgan.', duplicate_action:'Bu amal bugun allaqachon qayd etilgan.', sequence_invalid:'Bu amal davomat ketma-ketligiga mos kelmaydi.', invalid_action:'Noto‘g‘ri davomat amali.',
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
    const name=error&&error.name;
    if(name==='CameraPermissionTimeoutError')return 'Kamera ruxsati so‘roviga 30 soniyada javob kelmadi. Ruxsat oynasi chiqsa, ruxsat bering; chiqmasa Telegram-ni to‘liq yopib qayta oching.';
    if(name==='NotAllowedError'||name==='PermissionDeniedError')return isIOS()
      ? 'Kameraga ruxsat berilmadi. iPhone’da Sozlamalar → Maxfiylik va xavfsizlik → Kamera → Telegram ruxsatini yoqing, so‘ng Mini App-ni qayta oching.'
      : 'Kameraga ruxsat berilmadi. Android’da Sozlamalar → Ilovalar → Telegram → Ruxsatlar → Kamera-ni yoqing, so‘ng Mini App-ni qayta oching.';
    if(name==='NotFoundError'||name==='DevicesNotFoundError')return 'Old kamera topilmadi. Qurilmada kamera borligini tekshiring.';
    if(name==='NotReadableError'||name==='TrackStartError')return 'Kamera boshqa ilovada band yoki ochilmadi. Kamera ishlatayotgan ilovalarni yoping va qayta urinib ko‘ring.';
    if(name==='CameraPlaybackError'||name==='CameraFrameTimeoutError'||name==='CameraStreamEndedError')return error.message||'Kamera tasviri to‘xtadi. Tekshiruvni qayta boshlang.';
    return error.message||'Kamera ochilmadi. Qurilma sozlamalarida Telegram uchun kameraga ruxsatni tekshiring.';
  }
  function locationError(message,name='LocationPermissionError'){
    const error=Error(message);error.name=name;error.locationFailure=true;return error;
  }
  function waitWithTimeout(promise,ms,name,message,flag){
    let timer;
    const timeout=new Promise((_,reject)=>{timer=setTimeout(()=>{const error=Error(message);error.name=name;if(flag)error[flag]=true;reject(error);},ms);});
    return Promise.race([promise,timeout]).finally(()=>clearTimeout(timer));
  }
  function waitForCamera(promise){
    let timer,timedOut=false;
    const timeout=new Promise((_,reject)=>{timer=setTimeout(()=>{timedOut=true;const error=Error('Telegram WebView kamera ruxsati haqida javob bermadi.');error.name='CameraPermissionTimeoutError';error.cameraFailure=true;reject(error);},30000);});
    promise.then(cameraStream=>{if(timedOut)cameraStream.getTracks().forEach(track=>track.stop());},()=>{});
    return Promise.race([promise,timeout]).finally(()=>clearTimeout(timer));
  }
  function normalizePosition(position){
    const raw=position&&position.coords;
    if(!raw)return Promise.reject(locationError('Qurilmadan koordinata olinmadi. GPS-ni yoqib qayta urinib ko‘ring.'));
    const latitude=Number(raw.latitude),longitude=Number(raw.longitude),accuracy=Number(raw.accuracy);
    if(!Number.isFinite(latitude)||!Number.isFinite(longitude)||latitude < -90||latitude > 90||longitude < -180||longitude > 180)
      return Promise.reject(locationError('Qurilmadan to‘g‘ri koordinata olinmadi. Qayta urinib ko‘ring.'));
    if(!Number.isFinite(accuracy)||accuracy<=0)
      return Promise.reject(locationError(`Qurilma GPS aniqligini bermadi. ${locationPermissionHelp()} Ochiq joyda qayta urinib ko‘ring.`,'LocationAccuracyUnavailableError'));
    return {coords:{latitude,longitude,accuracy}};
  }
  function requestBrowserLocation(){
    if(!navigator.geolocation)return Promise.reject(locationError('Bu Telegram WebView’da joylashuv API mavjud emas. Telegram-ni yangilang yoki joylashuv ruxsatini tekshiring.'));
    const operation=new Promise((resolve,reject)=>navigator.geolocation.getCurrentPosition(position=>{
      try{const normalized=normalizePosition(position);normalized.source='browser';resolve(normalized);}catch(error){reject(error);}
    },error=>{
      reject(locationError(error.code===1?`Joylashuvga ruxsat berilmadi. ${locationPermissionHelp()}`:error.code===2?'GPS joylashuv aniqlanmadi. Ochiq joyda qayta urinib ko‘ring.':'GPS so‘rovi vaqti tugadi. Ochiq joyda qayta urinib ko‘ring.'));
    },{enableHighAccuracy:true,timeout:20000,maximumAge:0}));
    return waitWithTimeout(operation,25000,'LocationPermissionTimeoutError','Qurilma joylashuv so‘roviga 25 soniyada javob bermadi. GPS va aniq joylashuv ruxsatini tekshirib qayta urinib ko‘ring.','locationFailure');
  }
  function requestLocation(){
    const manager=tg&&tg.LocationManager;
    if(!manager||typeof manager.init!=='function'||typeof manager.getLocation!=='function')return requestBrowserLocation();
    const operation=new Promise((resolve,reject)=>{
      const fail=(message,name='LocationPermissionError')=>reject(locationError(message,name));
      const readLocation=()=>{
        if(manager.isLocationAvailable===false){fail('Telefonning joylashuv xizmati o‘chiq. GPS/joylashuvni yoqing va qayta urinib ko‘ring.');return;}
        try{
          manager.getLocation(location=>{
            if(!location){fail(`Telegram orqali joylashuvga ruxsat berilmadi. ${locationPermissionHelp()}`);return;}
            const latitude=Number(location.latitude),longitude=Number(location.longitude),accuracy=Number(location.horizontal_accuracy);
            if(!Number.isFinite(latitude)||!Number.isFinite(longitude)||latitude < -90||latitude > 90||longitude < -180||longitude > 180){fail('Telegram joylashuv xizmatidan to‘g‘ri koordinata olinmadi. Qayta urinib ko‘ring.');return;}
            if(Number.isFinite(accuracy)&&accuracy>0){resolve({coords:{latitude,longitude,accuracy},source:'telegram'});return;}
            if(navigator.geolocation){requestBrowserLocation().then(resolve,reject);return;}
            fail(`Telegram bu safar joylashuv aniqligini bermadi. ${locationPermissionHelp()} Ochiq joyda qayta urinib ko‘ring.`,'LocationAccuracyUnavailableError');
          });
        }catch(error){fail(error.message||'Telegram joylashuv so‘rovini boshlay olmadi.');}
      };
      try{if(manager.isInited)readLocation();else manager.init(readLocation);}
      catch(error){fail(error.message||'Telegram joylashuv xizmatini ishga tushira olmadi.');}
    });
    return waitWithTimeout(operation,25000,'LocationPermissionTimeoutError','Telegram joylashuv so‘roviga 25 soniyada javob bermadi. Mini App-ni qayta ochib, joylashuv ruxsatini tekshiring.','locationFailure');
  }
  function hasLiveCamera(){
    if(!stream||typeof stream.getVideoTracks!=='function')return false;
    return stream.getVideoTracks().some(track=>track.readyState==='live');
  }
  function stopCamera(){
    if(autoCaptureTimer!==null){clearTimeout(autoCaptureTimer);autoCaptureTimer=null;}
    if(successTimer!==null){clearTimeout(successTimer);successTimer=null;}
    const current=stream;stream=null;
    if(current)current.getTracks().forEach(track=>track.stop());
    const video=$('video');if(typeof video.pause==='function')video.pause();video.srcObject=null;
    $('video-wrap')?.classList.remove('ready','confirmed');$('camera-placeholder').hidden=false;$('stop-button').hidden=true;
  }
  function watchCameraStream(cameraStream){
    cameraStream.getVideoTracks().forEach(track=>{
      if(typeof track.addEventListener==='function')track.addEventListener('ended',()=>{
        if(stream!==cameraStream)return;
        stopCamera();$('camera-status').textContent='Kamera oqimi to‘xtadi. Qayta boshlang.';
        if(!busy){$('start-button').hidden=false;$('start-button').disabled=false;$('start-button').textContent='Kamerani qayta boshlash →';setNotice('Qurilma kamera oqimini to‘xtatdi. Kamerani qayta boshlang.','error');}
      },{once:true});
    });
  }
  function waitForVideoFrame(video){
    return new Promise((resolve,reject)=>{
      if(video.readyState>=2&&video.videoWidth>0)return resolve();
      let timer;const finish=(fn,value)=>{clearTimeout(timer);video.removeEventListener('loadeddata',onFrame);video.removeEventListener('canplay',onFrame);fn(value);};
      const onFrame=()=>{if(video.readyState>=2&&video.videoWidth>0)finish(resolve);};
      timer=setTimeout(()=>{const error=Error('Kamera ulandi, lekin video kadr kelmadi.');error.name='CameraFrameTimeoutError';error.cameraFailure=true;finish(reject,error);},8000);
      video.addEventListener('loadeddata',onFrame);video.addEventListener('canplay',onFrame);onFrame();
    });
  }
  async function checkLocationOnServer(position){
    let response;
    try{
      response=await fetch(`${api}/api/location-check`,{method:'POST',headers:{...headers(),'Content-Type':'application/json'},body:JSON.stringify({latitude:position.coords.latitude,longitude:position.coords.longitude,accuracy_m:position.coords.accuracy})});
    }catch(_){throw locationError('GPS tekshiruv serveriga ulanib bo‘lmadi. Internetni tekshirib, qayta urinib ko‘ring.','LocationCheckNetworkError');}
    let data={};try{data=await response.json();}catch(_){ }
    if(!response.ok){const error=Error(apiError(data.error,response.status,data));error.name=data.error||'LocationCheckError';error.locationFailure=true;error.locationSource=position.source;throw error;}
    return data;
  }
  async function start(){
    if(busy)return;
    if(!initData){setNotice('Telegram Mini App ichida oching. Brauzer preview rejimi autentifikatsiya qilmaydi va check-in yubora olmaydi.','error');return;}
    if(!api){setNotice('API ulanishi sozlanmagan: webapp/config.js faylida ATTENDANCE_API_BASE qiymatini kiriting.','error');return;}
    if(!navigator.mediaDevices||!navigator.mediaDevices.getUserMedia){const error=Error('Bu Telegram WebView’da kamera API mavjud emas. Telegram-ni yangilang va Mini App-ni qayta oching.');error.cameraFailure=true;setNotice(cameraErrorMessage(error),'error');return;}
    const manager=tg&&tg.LocationManager;
    if((!manager||typeof manager.getLocation!=='function')&&!navigator.geolocation){setNotice('Bu Telegram WebView’da joylashuv API mavjud emas. Telegram-ni yangilang va joylashuv ruxsatini tekshiring.','error');return;}
    busy=true;stopped=false;challenge=null;coords=null;$('start-button').disabled=true;setNotice('Avval kameraga ruxsat bering; kamera tayyor bo‘lgach aniq joylashuv so‘raladi.');
    let cameraPromise=null;
    try{
      if(!hasLiveCamera()){
        if(stream)stopCamera();
        $('camera-status').textContent='Kameraga ulanish so‘ralmoqda…';
        cameraPromise=navigator.mediaDevices.getUserMedia({audio:false,video:{facingMode:{ideal:'user'},width:{ideal:640},height:{ideal:480}}}).catch(error=>{const wrapped=Error(error&&error.message||'Kamera ochilmadi.');wrapped.name=error&&error.name||'Error';wrapped.cameraFailure=true;throw wrapped;});
        cameraPromise.catch(()=>{});
        const cameraStream=await waitForCamera(cameraPromise);
        if(stopped){cameraStream.getTracks().forEach(track=>track.stop());return;}
        stream=cameraStream;watchCameraStream(cameraStream);
      }
      const video=$('video');video.muted=true;video.playsInline=true;if(video.srcObject!==stream)video.srcObject=stream;
      try{await video.play();}catch(_){const error=Error('Kamera ruxsati olindi, lekin video tasviri ishga tushmadi. Kamerani qayta boshlang.');error.name='CameraPlaybackError';error.cameraFailure=true;throw error;}
      await waitForVideoFrame(video);
      if(stopped)return;
      if(!hasLiveCamera()){const error=Error('Kamera oqimi qurilma tomonidan to‘xtatildi.');error.name='CameraStreamEndedError';error.cameraFailure=true;throw error;}
      $('camera-placeholder').hidden=true;$('video-wrap').classList.add('ready');$('camera-status').textContent='Old kamera tayyor';markDone('step-camera');$('stop-button').hidden=false;
      $('location-status').textContent=manager&&typeof manager.getLocation==='function'?'Telegram orqali aniq joylashuv so‘ralmoqda…':'Qurilma GPS aniqligi so‘ralmoqda…';setNotice('Kamera tayyor. Endi joylashuv aniqligi va ofis hududi tekshiriladi.');
      const position=await requestLocation();
      if(stopped)return;
      if(!hasLiveCamera()){const error=Error('Kamera oqimi joylashuv so‘rovi paytida to‘xtadi. Kamerani qayta boshlang.');error.name='CameraStreamEndedError';error.cameraFailure=true;throw error;}
      coords={latitude:position.coords.latitude,longitude:position.coords.longitude,accuracy_m:position.coords.accuracy,source:position.source};
      $('location-status').textContent=`GPS aniqligi ±${Math.round(coords.accuracy_m)} m · ofis hududi tekshirilmoqda…`;
      const location=await checkLocationOnServer(position);
      if(stopped)return;
      if(!hasLiveCamera()){const error=Error('Kamera oqimi qurilma tomonidan to‘xtatildi. Kamerani qayta boshlang.');error.name='CameraStreamEndedError';error.cameraFailure=true;throw error;}
      $('location-status').textContent=`Ofis hududidasiz · taxminan ${location.distance_m} m · GPS ±${location.accuracy_m} m`;markDone('step-location');setNotice('Joylashuv tasdiqlandi. Harakat topshirig‘i tayyorlanmoqda.');
      const res=await fetch(`${api}/api/challenge?action=${encodeURIComponent(action())}`,{method:'GET',headers:headers()});let challengeData={};try{challengeData=await res.json();}catch(_){ }
      if(!res.ok)throw Error(apiError(challengeData.error,res.status,challengeData));
      challenge=challengeData;if(!challenge.challenge_id||!challenge.instruction)throw Error('Server topshirig‘i javobida kerakli ma’lumotlar yo‘q.');
      $('instruction').textContent=challenge.instruction;$('motion-status').textContent='Topshiriq tayyor · yuzni avtomatik solishtirish va oddiy harakat tekshiruvi';
      $('start-button').hidden=true;$('capture-button').hidden=true;$('capture-button').disabled=true;$('retry-button').hidden=true;
      $('capture-count').textContent='0 / 5';$('capture-status').textContent='3 soniyadan so‘ng kadrlar avtomatik olinadi';$('progress').style.width='0';
      busy=false;setNotice('Yuzingizni kadrga joylang va ko‘rsatilgan tomonga sekin buring. 3 soniyadan so‘ng 5 kadr avtomatik olinib serverga yuboriladi.');
      autoCaptureTimer=setTimeout(()=>{autoCaptureTimer=null;capture();},3000);
    }catch(error){
      if(stopped){busy=false;return;}
      if(cameraPromise)cameraPromise.then(pending=>{if(pending!==stream&&pending.getTracks().some(track=>track.readyState==='live'))pending.getTracks().forEach(track=>track.stop());}).catch(()=>{});
      let message=error.cameraFailure?cameraErrorMessage(error):(error.message||'Tekshiruvni boshlashda xatolik.');
      if(error.locationFailure&&error.locationSource)message=appendLocationSource(message,error.locationSource);
      if(error.cameraFailure||!hasLiveCamera())stopCamera();
      else{$('camera-placeholder').hidden=true;$('video-wrap').classList.add('ready');$('camera-status').textContent='Old kamera tayyor';$('stop-button').hidden=false;}
      if(error.cameraFailure){$('camera-status').textContent=message;$('start-button').textContent='Kamerani qayta boshlash →';}
      else if(error.locationFailure){$('location-status').textContent=message;$('start-button').textContent='GPSni qayta tekshirish →';}
      else $('start-button').textContent='Qayta urinib ko‘rish →';
      busy=false;$('start-button').disabled=false;setNotice(message,'error');
    }
  }
  async function capture(){
    if(busy||stopped||!hasLiveCamera()||!challenge||!coords)return;
    busy=true;stopped=false;$('capture-button').disabled=true;$('stop-button').hidden=false;const video=$('video');
    try{
      video.muted=true;video.playsInline=true;if(video.srcObject!==stream)video.srcObject=stream;
      try{await video.play();}catch(_){const error=Error('Kamera tasviri qayta ishga tushmadi. Tekshiruvni qayta boshlang.');error.name='CameraPlaybackError';error.cameraFailure=true;throw error;}
      await new Promise((resolve,reject)=>{if(video.readyState>=2)return resolve();video.onloadeddata=resolve;setTimeout(()=>reject(Error('Kamera tasviri tayyor bo‘lmadi.')),5000);});
      const canvas=document.createElement('canvas');
      const sourceWidth=Number(video.videoWidth)||640,sourceHeight=Number(video.videoHeight)||480;
      const scale=Math.min(1,720/Math.max(sourceWidth,sourceHeight));
      canvas.width=Math.max(1,Math.round(sourceWidth*scale));canvas.height=Math.max(1,Math.round(sourceHeight*scale));
      const ctx=canvas.getContext('2d');if(!ctx)throw Error('Kadr olish ishga tushmadi.');
      const frames=[];
      for(let i=0;i<5;i++){
        if(stopped)throw Error('Tekshiruv to‘xtatildi.');
        ctx.save();ctx.translate(canvas.width,0);ctx.scale(-1,1);ctx.drawImage(video,0,0,canvas.width,canvas.height);ctx.restore();
        const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg',.72));if(!blob)throw Error('JPEG kadr tayyorlanmadi.');frames.push(blob);
        $('capture-count').textContent=`${i+1} / 5`;$('capture-status').textContent='Kadrlar olinmoqda…';$('progress').style.width=`${(i+1)*20}%`;
        if(i<4)await new Promise(resolve=>setTimeout(resolve,600));
      }
      if(stopped)throw Error('Tekshiruv to‘xtatildi.');$('capture-status').textContent='Serverga yuborilmoqda…';setNotice('Kadrlar serverga yuborilmoqda…');
      const form=new FormData();form.append('challenge_id',challenge.challenge_id);form.append('action',action());form.append('latitude',String(coords.latitude));form.append('longitude',String(coords.longitude));form.append('accuracy_m',String(coords.accuracy_m));frames.forEach((blob,i)=>form.append('frames',blob,`frame-${i+1}.jpg`));
      const res=await fetch(`${api}/api/attendance`,{method:'POST',headers:headers(),body:form});let data={};try{data=await res.json();}catch(_){ }
      if(!res.ok){const code=data.error||data.detail||data.message;const error=Error(apiError(code,res.status,data));if(code==='outside_office_radius'||code==='location_accuracy_insufficient'){error.locationFailure=true;error.locationSource=coords.source;}throw error;}
      busy=false;markDone('step-motion');$('capture-status').textContent='Server tasdiqladi';$('progress').style.width='100%';$('capture-button').hidden=true;$('stop-button').hidden=true;$('retry-button').hidden=true;
      $('video-wrap').classList.add('confirmed');$('camera-status').textContent='Yuz tasdiqlandi';$('motion-status').textContent='Davomat qayd etildi';
      setNotice(data.message||`${labels[action()].charAt(0).toUpperCase()+labels[action()].slice(1)} muvaffaqiyatli qayd etildi.`,'success');
      successTimer=setTimeout(()=>{successTimer=null;stopCamera();},1800);
    }catch(error){
      if(stopped){$('capture-count').textContent='0 / 5';$('capture-status').textContent='5 ta kadr olinadi';$('progress').style.width='0';}
      busy=false;$('capture-button').hidden=true;$('retry-button').hidden=false;$('stop-button').hidden=!hasLiveCamera();$('start-button').hidden=true;
      let message=error.cameraFailure?cameraErrorMessage(error):(error.message||'Kadrlarni olishda xatolik.');
      if(error.cameraFailure||!hasLiveCamera())stopCamera();
      if(error.locationFailure&&error.locationSource)message=appendLocationSource(message,error.locationSource);setNotice(message,'error');
    }
  }
  $('action-select').addEventListener('change',()=>{$('start-button').textContent='Tekshiruvni boshlash →';challenge=null;coords=null;stopCamera();$('start-button').hidden=false;$('start-button').disabled=false;$('capture-button').hidden=true;$('retry-button').hidden=true;setNotice(`${labels[action()]} uchun GPS aniqligi, yuz mosligi va bir martalik harakat tekshiruvi bajariladi.`);});
  $('start-button').addEventListener('click',start);$('capture-button').addEventListener('click',capture);
  $('stop-button').addEventListener('click',()=>{stopped=true;stopCamera();busy=false;$('capture-button').hidden=true;$('start-button').hidden=false;$('start-button').disabled=false;$('start-button').textContent='Tekshiruvni boshlash →';$('retry-button').hidden=true;setNotice('Tekshiruv to‘xtatildi. Qayta boshlashingiz mumkin.');});
  $('retry-button').addEventListener('click',()=>{challenge=null;coords=null;$('capture-count').textContent='0 / 5';$('capture-status').textContent='5 ta kadr olinadi';$('progress').style.width='0';$('retry-button').hidden=true;$('start-button').hidden=false;$('start-button').disabled=false;$('start-button').textContent='Tekshiruvni boshlash →';setNotice('Qayta urinishga tayyor.');});
  if(!initData)setNotice('Telegram Mini App ichida oching. Brauzer preview rejimi autentifikatsiya qilmaydi va check-in yubora olmaydi.','error');
  else if(!api)setNotice('API ulanishi sozlanmagan: webapp/config.js faylida ATTENDANCE_API_BASE qiymatini kiriting.','error');
})();
