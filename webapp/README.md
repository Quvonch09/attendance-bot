# Davomat Telegram Web App

`webapp/` katalogi Telegram Mini App uchun statik frontend bo'lib, Vercel'ga joylanadi. Build yoki serverless function kerak emas; Vercel loyihasining root katalogi sifatida shu katalogni tanlang. Kamera va geolocation uchun HTTPS hamda sahifani Telegram ilovasi ichida ochish kerak.

`config.js` ichidagi `ATTENDANCE_OFFICE_LAT/LON` Innova ofisining WGS84 koordinatalari. `ATTENDANCE_API_BASE` bot API'ning HTTPS manzili, masalan `https://169.58.215.170:8443`. Haqiqiy HTTPS endpoint tayyor bo'lmaguncha API manzilini bo'sh qoldiring; bu vaqtda UI check-in yubormaydi.

Har bir so'rov `X-Telegram-Init-Data` headerida Telegram'dan kelgan raw `Telegram.WebApp.initData` qiymatini yuboradi. Backend HMAC va `auth_date`ni serverda tekshiradi; `initDataUnsafe` login uchun ishlatilmaydi. GET `/api/challenge` bir marta ishlatiladigan, besh daqiqalik harakat topshirig'ini yaratadi. POST `/api/checkin` `challenge_id`, `latitude`, `longitude` va beshta JPEG kadrni `multipart/form-data` orqali yuboradi. Server xodim tasdig'i, 300 m radius, bugungi takroriy qayd va yuz mosligini qayta tekshiradi.

09:30 dan keyin check-in qilishdan avval Telegram chatda kechikish sababi matn yoki ovozda qayd etilgan bo'lishi kerak. Izoh bo'lmasa, chatga qaytib sababni yuboring va Web App'ni qayta oching. Selfie kadrlarining vaqtinchalik fayllari tekshiruvdan keyin o'chiriladi; birinchi ro'yxat surati serverda saqlanadi.

Backend faqat `127.0.0.1:8765` da ishlaydi. Alohida Nginx HTTPS listener 8443 portdagi API so'rovlarini loopback'ga uzatadi va faqat Vercel production manziliga CORS ruxsatini beradi. Frontend URL va API endpoint sozlanmaguncha API'ni yoqmang. Harakat tekshiruvi oddiy prototip, sertifikatlangan liveness emas.
