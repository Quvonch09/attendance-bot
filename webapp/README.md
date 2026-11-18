# Davomat Telegram Web App

`webapp/` katalogi Telegram Mini App uchun statik frontend. Uni faqat ish maqsadidagi foydalanishga mos, HTTPS’li hostingda joylashtiring. Vercel Hobby reja shaxsiy yoki notijoriy foydalanish bilan cheklangani uchun bu ish botiga tanlanmagan; rasmiy shartlar: https://vercel.com/legal/terms . Hozircha pullik hosting tasdiqlanmagan.

`config.js` ichida Innova ofisining WGS84 koordinatalari va API asosiy manzili sozlanadi. `ATTENDANCE_API_BASE` bot API’ning HTTPS manzili bo‘lishi kerak (hozirgi alohida tinglovchi: `https://169.58.215.170:8443`). Frontend hosting tanlanmaguncha API asosiy manzilini bo‘sh qoldiring.

Frontend kelish, tushlikka chiqish, tushlikdan qaytish va ishdan chiqish amallaridan birini tanlaydi. Har bir amal Telegram’dan kelgan xom `Telegram.WebApp.initData` bilan tasdiqlanadi, serverdagi bir martalik topshiriqni oladi, GPS koordinatasini va beshta JPEG kadrni `/api/attendance` manziliga `multipart/form-data` orqali yuboradi. Backend 300 metr radius, xodim tasdig‘i, takroriy qayd, amal ketma-ketligi, yuz mosligi va harakat belgilarini qayta tekshiradi. So‘rovlar `X-Telegram-Init-Data` sarlavhasida initData yuboradi; `initDataUnsafe` ishonchli emas.

Mini App’ni reply-keyboard `KeyboardButton(web_app)` orqali ochmang: Telegram bunday ochilishda `initData`ni bo‘sh berishi mumkin. Bot API rejimida inline `web_app` tugmasi ishlatiladi. 09:30 dan keyingi kelishdan oldin izohni Telegram chatda matn yoki ovozda yuborish kerak.

Backend faqat `127.0.0.1:8765` loopback manzilida ishlaydi. Nginx’ning 8443 tinglovchisi faqat bot API upstream’iga yo‘naltirilgan, boshqa yo‘llar `404` qaytaradi. Hozir `WEBAPP_API_ENABLED=0`, `WEBAPP_URL` va aniq origin sozlanmagan, shuning uchun API o‘chiq. Mos HTTPS frontend hosting tanlangach `WEBAPP_URL`, `WEBAPP_ORIGIN` va `ATTENDANCE_API_BASE` qiymatlarini sozlab, birgalikda sinovdan o‘tkazing. Harakat tekshiruvi prototip bo‘lib, sertifikatlangan tiriklikni aniqlash tizimi emas.
