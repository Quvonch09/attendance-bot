# Davomat Telegram Web App

`webapp/` katalogi Telegram Mini App uchun statik frontend bo‘lib, bot serveridagi `https://api.orivasmarket.uz:8443/` manzilida ishlaydi. Bu ish botini Vercel Hobby rejaga joylashtirmaymiz: https://vercel.com/legal/terms .

`config.js` ofis markazining server bilan mosligini izohlaydi; API manzili `window.location.origin` orqali joriy origin’dan olinadi. Web App `https://api.orivasmarket.uz:8443/` sahifasidan ochilganda so‘rovlar o‘sha origin’dagi `/api/challenge` va `/api/attendance` manzillariga yuboriladi.

Frontend kelish, tushlikka chiqish, tushlikdan qaytish va ishdan chiqish amallarini qo‘llaydi. Bot shaxsiy chatdagi Telegram «joriy lokatsiyani yuborish» tugmasidan kelgan lokatsiya va aniqlikni serverda 100 m chegaraga nisbatan tekshiradi. Mos bo‘lsa, xodim va amal bilan bog‘langan 3 daqiqalik bir martalik token inline Web App tugmasiga beriladi. Mini App tokenni `POST /api/challenge` orqali bir marta qaytaradi; server lokatsiyani yana tekshirgach kamera so‘raladi. Brauzer/GPS koordinatalari davomat uploadiga qo‘shilmaydi. Upload faqat challenge, amal va beshta JPEG kadrni o‘z ichiga oladi; backend xodim tasdig‘i, qat’iy 100 m radius, takroriy qayd, amal ketma-ketligi, yuz mosligi va harakat belgilarini qayta tekshiradi. `initDataUnsafe` ishonchli emas.

Mini App’ni reply-keyboard `KeyboardButton(web_app)` orqali ochmang: Telegram bunday ochilishda `initData`ni bo‘sh berishi mumkin. Bot API rejimida inline `web_app` tugmasi ishlatiladi. 09:30 dan keyingi kelishdan oldin izohni Telegram chatda matn yoki ovozda yuborish kerak.

Bot API faqat `127.0.0.1:8765` loopback manzilida ishlaydi. Nginx’ning faqat davomat uchun ajratilgan 8443 bloki `api.orivasmarket.uz` sertifikatidan foydalanib, `/` va `/api/` so‘rovlarini bot API’ga uzatadi. Proxy belgilangan `X-Cue-Origin` sarlavhasini o‘zi qo‘yadi; shu orqali same-origin so‘rovlari va origin tekshiruvi himoyalangan bo‘ladi. API yoqilishidan oldin TLS sertifikati, HTTPS frontend, inline Web App URL, CORS/origin va API aloqasi sinovdan o‘tkaziladi. Harakat tekshiruvi prototip bo‘lib, tasdiqlangan tiriklikni aniqlash tizimi emas.
