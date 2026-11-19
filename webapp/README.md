# Davomat Telegram Web App

`webapp/` katalogi Telegram Mini App uchun statik frontend bo‘lib, bot serveridagi `https://api.orivasmarket.uz:8443/` manzilida ishlaydi. Bu ish botini Vercel Hobby rejaga joylashtirmaymiz: https://vercel.com/legal/terms .

`config.js` Innova ofisining WGS84 koordinatalarini belgilaydi; API manzili `window.location.origin` orqali joriy origin’dan olinadi. Web App `https://api.orivasmarket.uz:8443/` sahifasidan ochilganda so‘rovlar o‘sha origin’dagi `/api/challenge` va `/api/attendance` manzillariga yuboriladi.

Frontend kelish, tushlikka chiqish, tushlikdan qaytish va ishdan chiqish amallaridan birini tanlaydi. Har bir amal Telegram’dan kelgan xom `Telegram.WebApp.initData` bilan tasdiqlanadi, serverdagi bir martalik topshiriqni oladi, GPS koordinatasini va beshta JPEG kadrni `multipart/form-data` orqali yuboradi. Backend 100 metr radius, xodim tasdig‘i, takroriy qayd, amal ketma-ketligi, yuz mosligi va harakat belgilarini qayta tekshiradi. `initDataUnsafe` ishonchli emas.

Mini App’ni reply-keyboard `KeyboardButton(web_app)` orqali ochmang: Telegram bunday ochilishda `initData`ni bo‘sh berishi mumkin. Bot API rejimida inline `web_app` tugmasi ishlatiladi. 09:30 dan keyingi kelishdan oldin izohni Telegram chatda matn yoki ovozda yuborish kerak.

Bot API faqat `127.0.0.1:8765` loopback manzilida ishlaydi. Nginx’ning faqat davomat uchun ajratilgan 8443 bloki `api.orivasmarket.uz` sertifikatidan foydalanib, `/` va `/api/` so‘rovlarini bot API’ga uzatadi. Proxy belgilangan `X-Cue-Origin` sarlavhasini o‘zi qo‘yadi; shu orqali same-origin so‘rovlari va origin tekshiruvi himoyalangan bo‘ladi. API yoqilishidan oldin TLS sertifikati, HTTPS frontend, inline Web App URL, CORS/origin va API aloqasi sinovdan o‘tkaziladi. Harakat tekshiruvi prototip bo‘lib, tasdiqlangan tiriklikni aniqlash tizimi emas.
