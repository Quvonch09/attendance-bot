(() => {
  const $ = (id) => document.getElementById(id);
  const tg = window.Telegram && window.Telegram.WebApp;
  if (tg) { tg.ready(); tg.expand(); }
  const initData = tg && typeof tg.initData === 'string' ? tg.initData : '';
  const user = tg && tg.initDataUnsafe && tg.initDataUnsafe.user;
  if (user && user.first_name) $('greeting').textContent = `Xush kelibsiz, ${user.first_name}`;
  const api = String(window.ATTENDANCE_API_BASE || '').trim().replace(/\/$/, '');
  let stream = null, coords = null, challenge = null, busy = false, stopped = false;
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
  const apiError = (code, status) => ({
    unauthorized:'Telegram tekshiruvi o‘tmadi. Web App-ni bot ichidan qayta oching.',
    not_approved:'Ro‘yxatdagi yuzingiz hali admin yoki nazoratchi tomonidan tasdiqlanmagan.',
    already_checked_in:'Bugungi kelishingiz avval qayd etilgan.', duplicate_action:'Bu amal bugun allaqachon qayd etilgan.', sequence_invalid:'Bu amal davomat ketma-ketligiga mos kelmaydi.', invalid_action:'Noto‘g‘ri davomat amali.',
    late_reason_required:'09:30 dan keyin kelayotgan bo‘lsangiz, bot chatiga kechikish sababini matn yoki ovozda yuboring, keyin qayta urinib ko‘ring.',
    outside_office_radius:'Siz ofisdan 300 metr radiusdan tashqaridasiz.',
    face_check_failed:'Yuz yoki harakat tekshiruvi o‘tmadi. Kameraga aniq qarab, topshiriqni bajaring va qayta urinib ko‘ring.',
    challenge_expired:'Harakat topshirig‘i muddati tugadi. Tekshiruvni qayta boshlang.',
    service_unavailable:'Davomat API hali sozlanmagan. Birozdan so‘ng qayta urinib ko‘ring.',
    origin_denied:'Web App manzili API tomonidan ruxsat etilmagan.',
    location_unavailable:'Joylashuvni tekshirib bo‘lmadi. GPS-ni yoqib qayta urinib ko‘ring.'
  })[code] || `Tekshiruv serverda bajarilmadi (${status}). Qayta urinib ko‘ring.`;
  function distanceMeters(a,b,c,d){const r=x=>x*Math.PI/180,dl=r(c-a),dn=r(d-b),q=Math.sin(dl/2)**2+Math.cos(r(a))*Math.cos(r(c))*Math.sin(dn/2)**2;return 6371000*2*Math.atan2(Math.sqrt(q),Math.sqrt(1-q));}
  function stopCamera(){if(stream){stream.getTracks().forEach(t=>t.stop());stream=null;} $('video').srcObject=null;$('video-wrap')?.classList.remove('ready');$('camera-placeholder').hidden=false;$('stop-button').hidden=true;}
  function showRetry(message){busy=false; $('start-button').hidden=true;$('capture-button').hidden=true;$('retry-button').hidden=false;$('stop-button').hidden=true;setNotice(message,'error');}
  async function start(){
    if(busy)return;
    if(!initData){setNotice('Telegram Mini App ichida oching. Brauzer preview rejimi autentifikatsiya qilmaydi va check-in yubora olmaydi.','error');return;}
    if(!api){setNotice('API ulanishi sozlanmagan: webapp/config.js faylida ATTENDANCE_API_BASE qiymatini kiriting.','error');return;}
    const officeLat=Number(window.ATTENDANCE_OFFICE_LAT),officeLon=Number(window.ATTENDANCE_OFFICE_LON);
    if(!Number.isFinite(officeLat)||!Number.isFinite(officeLon)||window.ATTENDANCE_OFFICE_LAT===null||window.ATTENDANCE_OFFICE_LON===null){setNotice('Ofis koordinatalari sozlanmagan. config.js ichida ATTENDANCE_OFFICE_LAT va ATTENDANCE_OFFICE_LON ni kiriting.','error');return;}
    busy=true; stopped=false;$('start-button').disabled=true;setNotice('Joylashuv so‘ralmoqda…');
    try {
      if(!navigator.geolocation)throw Error('Bu qurilmada joylashuv aniqlash mavjud emas.');
      const pos=await new Promise((resolve,reject)=>navigator.geolocation.getCurrentPosition(resolve,e=>reject(Error(e.code===1?'Joylashuvga ruxsat berilmadi. Brauzer sozlamalaridan ruxsat bering.':e.code===2?'Joylashuv aniqlanmadi.':'Joylashuv so‘rovi vaqti tugadi. Qayta urinib ko‘ring.')),{enableHighAccuracy:true,timeout:15000,maximumAge:0}));
      coords={latitude:pos.coords.latitude,longitude:pos.coords.longitude};const dist=distanceMeters(coords.latitude,coords.longitude,officeLat,officeLon);
      if(dist>300)throw Error(`Siz ofisdan taxminan ${Math.round(dist)} m uzoqdasiz. Check-in uchun 300 m ichida bo‘ling.`);
      $('location-status').textContent=`Ofis hududidasiz · taxminan ${Math.round(dist)} m`;markDone('step-location');setNotice('Joylashuv tasdiqlandi. Kameraga ruxsat bering.');
      if(!navigator.mediaDevices||!navigator.mediaDevices.getUserMedia)throw Error('Kamera ishlashi uchun HTTPS va mos brauzer kerak.');
      stream=await navigator.mediaDevices.getUserMedia({audio:false,video:{facingMode:{ideal:'user'},width:{ideal:640},height:{ideal:480}}});
      $('video').srcObject=stream;$('camera-placeholder').hidden=true;$('video-wrap').classList.add('ready');$('camera-status').textContent='Old kamera tayyor';markDone('step-camera');$('stop-button').hidden=false;
      const res=await fetch(`${api}/api/challenge?action=${encodeURIComponent(action())}`,{method:'GET',headers:headers()});let challengeData={};try{challengeData=await res.json();}catch(_){ }
      if(!res.ok)throw Error(apiError(challengeData.error,res.status));
      challenge=challengeData;if(!challenge.challenge_id||!challenge.instruction)throw Error('Server topshiriq javobida kerakli ma’lumotlar yo‘q.');
      $('instruction').textContent=challenge.instruction;$('motion-status').textContent='Topshiriq tayyor · yuzni avtomatik solishtirish va oddiy harakat tekshiruvi';
      $('start-button').hidden=true;$('capture-button').hidden=false;$('capture-button').disabled=false;$('retry-button').hidden=true;busy=false;setNotice('Topshiriqni bajaring, so‘ng kadrlarni oling.');
    } catch(e){stopCamera();busy=false;$('start-button').disabled=false;setNotice(e.message||'Tekshiruvni boshlashda xatolik.','error');}
  }
  async function capture(){if(busy||!stream||!challenge||!coords)return;busy=true;stopped=false;$('capture-button').disabled=true;$('stop-button').hidden=false;const video=$('video');
    try{
      await new Promise((resolve,reject)=>{if(video.readyState>=2)return resolve();video.onloadeddata=resolve;setTimeout(()=>reject(Error('Kamera tasviri tayyor bo‘lmadi.')),5000);});
      const canvas=document.createElement('canvas');canvas.width=640;canvas.height=480;const ctx=canvas.getContext('2d');if(!ctx)throw Error('Kadr olish ishga tushmadi.');
      const frames=[];
      for(let i=0;i<5;i++){
        if(stopped)throw Error('Tekshiruv to‘xtatildi.');
        ctx.save();ctx.translate(canvas.width,0);ctx.scale(-1,1);ctx.drawImage(video,0,0,canvas.width,canvas.height);ctx.restore();
        const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg',.72));if(!blob)throw Error('JPEG kadr tayyorlanmadi.');frames.push(blob);
        $('capture-count').textContent=`${i+1} / 5`;$('capture-status').textContent='Kadrlar olinmoqda…';$('progress').style.width=`${(i+1)*20}%`;
        if(i<4)await new Promise(resolve=>setTimeout(resolve,600));
      }
      if(stopped)throw Error('Tekshiruv to‘xtatildi.');$('capture-status').textContent='Serverga yuborilmoqda…';setNotice('Kadrlar serverga yuborilmoqda…');
      const form=new FormData();form.append('challenge_id',challenge.challenge_id);form.append('action',action());form.append('latitude',String(coords.latitude));form.append('longitude',String(coords.longitude));frames.forEach((blob,i)=>form.append('frames',blob,`frame-${i+1}.jpg`));
      const res=await fetch(`${api}/api/attendance`,{method:'POST',headers:headers(),body:form});let data={};try{data=await res.json();}catch(_){ }
      if(!res.ok)throw Error(apiError(data.error||data.detail||data.message,res.status));
      stopCamera();busy=false;markDone('step-motion');$('capture-status').textContent='Tekshiruv yakunlandi';$('progress').style.width='100%';$('capture-button').hidden=true;$('stop-button').hidden=true;$('retry-button').hidden=true;setNotice(data.message||`${labels[action()].charAt(0).toUpperCase()+labels[action()].slice(1)} muvaffaqiyatli qayd etildi.`,'success');
    }catch(e){if(stopped){$('capture-count').textContent='0 / 5';$('capture-status').textContent='5 ta kadr olinadi';$('progress').style.width='0';}busy=false;$('capture-button').hidden=true;$('retry-button').hidden=false;$('stop-button').hidden=true;setNotice(e.message||'Kadrlarni olishda xatolik.','error');}
  }
  $('action-select').addEventListener('change',()=>{challenge=null;coords=null;stopCamera();$('start-button').hidden=false;$('start-button').disabled=false;$('capture-button').hidden=true;$('retry-button').hidden=true;setNotice(`${labels[action()]} uchun GPS, yuz mosligi va bir martalik harakat tekshiruvi bajariladi.`);});
  $('start-button').addEventListener('click',start);$('capture-button').addEventListener('click',capture);
  $('stop-button').addEventListener('click',()=>{stopped=true;stopCamera();busy=false;$('capture-button').hidden=true;$('start-button').hidden=false;$('start-button').disabled=false;$('retry-button').hidden=true;setNotice('Tekshiruv to‘xtatildi. Qayta boshlashingiz mumkin.');});
  $('retry-button').addEventListener('click',()=>{challenge=null;coords=null;$('capture-count').textContent='0 / 5';$('capture-status').textContent='5 ta kadr olinadi';$('progress').style.width='0';$('retry-button').hidden=true;$('start-button').hidden=false;$('start-button').disabled=false;setNotice('Qayta urinishga tayyor.');});
  if(!initData)setNotice('Telegram Mini App ichida oching. Brauzer preview rejimi autentifikatsiya qilmaydi va check-in yubora olmaydi.','error');
  else if(!api)setNotice('API ulanishi sozlanmagan: webapp/config.js faylida ATTENDANCE_API_BASE qiymatini kiriting.','error');
})();
