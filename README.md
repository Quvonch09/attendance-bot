# Telegram davomat boti

Bot xodimlarning ishga kelish-ketishini Telegram orqali qayd etadi. Oddiy xodim, admin va nazoratchi huquqlari bitta akkauntda birga bo'lishi mumkin. Nazoratchi ham xodim sifatida davomat belgilay oladi.

## Ish tartibi

Ish joyi Innova maktabining xaritadagi nuqtasi (`38.8557873, 65.8002788`), ruxsat etilgan radius 300 metr, vaqt zonasi `Asia/Tashkent`. Adminlar Telegram ID `8048583227` va `1922170479`; `8048583227` nazoratchi huquqiga ham ega. Xodim `/start` orqali ro'yxatdan o'tadi, yuz ma'lumotidan foydalanishga rozilik beradi, ism-familiyasini va birinchi selfisini yuboradi. Admin yoki nazoratchilardan biri ro'yxatdagi suratni bir marta tasdiqlaydi. Xodim o'z suratini o'zi tasdiqlay olmaydi; boshqa nazoratchi tasdiqlashi kerak.

09:00 da ishga kelish eslatmasi yuboriladi. 09:30 dan keyin xodim Telegram chatga kechikish sababini matn yoki ovozda yuborishi shart. 12:00 da tushlikka chiqish, 14:30 da ishga qaytish, 18:00 da ishdan chiqish tugmalari ishlatiladi. 12:00 va 14:30 tasdiqlari 10 daqiqada kelmasa, nazoratchiga ogohlantirish yuboriladi; 18:15 da kunlik hisobot keladi. Har bir qaydda nazoratchilarga xodimning ismi va vaqti yuboriladi. Pastki doimiy menyuda xodim va nazoratchi tugmalari ko'rinadi.

## Telegram Web App va API

`webapp/` katalogidagi sahifa Vercel'ga joylanadigan statik frontend. Kamera va joylashuv faqat HTTPS orqali Telegram ilovasi ichida ochilganda ishlaydi. Frontend Telegram'dan olingan raw `Telegram.WebApp.initData` qiymatini serverga yuboradi. Server bot tokeni bilan HMAC imzosini va ma'lumot vaqtini tekshiradi; `initDataUnsafe` login uchun ishlatilmaydi.

`WEBAPP_API_ENABLED=1` bo'lsa, bot API faqat `127.0.0.1:8765` manzilida ishlaydi. Alohida HTTPS proxy 8443 portdagi `/api/` so'rovlarini shu API'ga uzatadi va faqat Web App'ning aniq HTTPS manzilini qabul qiladi. API'ni boshqa manzilga bevosita ochmang. Vercel va API manzillari tayyor bo'lgach `webapp/config.js` hamda server `.env` sozlanadi. URL'lar yo'q bo'lsa API'ni yoqmang.

09:30 dan keyingi Web App check-in uchun Telegram chatga kechikish izohi oldin yuborilgan bo'lishi shart. Selfie kadrlari vaqtinchalik faylda tekshiriladi va keyin o'chiriladi. Birinchi yuz surati va davomat SQLite bazasi `DATA_DIR` ichida saqlanadi.

## Lokal ishga tushirish

Python 3.11 yoki undan yangi versiya kerak.

```bash
python -m pip install -r requirements.txt
cp .env.example .env
# .env ichiga BOT_TOKEN va kerakli sozlamalarni kiriting
python -m py_compile bot.py webapp_api.py
python bot.py
```

`WEBAPP_API_ENABLED=0` xavfsiz boshlang'ich sozlama. API'ni faqat frontend HTTPS manzili va Nginx proxy tayyor bo'lgach `1` qiling. `.env`, bot tokeni, `data/` ichidagi yuz rasmlari va SQLite faylini GitHub'ga yubormang.

## GitHub orqali yangilash

Private repository'dagi GitHub Actions faqat alohida `cue-attendance-bot` foydalanuvchisi, `/opt/cue-attendance-bot/releases/` katalogi va `cue-attendance-bot.service` ni yangilaydi. Boshqa loyiha va xizmatlarga tegmaydi. Actions faqat `DEPLOY_SSH_KEY` secretidan foydalanadi; bot tokeni GitHub'ga yuborilmaydi va serverdagi himoyalangan `.env` faylida qoladi. Kod serverga joylanganining o'zi Vercel yoki HTTPS proxy tayyor degani emas; ularni alohida sozlab tekshirish kerak.

## Yuz va joylashuv bo'yicha cheklovlar

DeepFace har kungi yuzni birinchi ro'yxat surati bilan avtomatik solishtiradi. Haar yuz topish va bir nechta kadrdagi harakat signali oddiy prototip tekshiruvidir, sertifikatlangan liveness yoki anti-spoof tizimi emas. Soxta GPS yoki ekrandagi video bilan aldash xavfi qoladi; natijani maosh yoki intizomiy qarorning yagona dalili qilmang. Ish boshlashdan oldin rozilik, saqlash muddati, ma'lumotni kim ko'rishi, o'chirish va zaxira nusxa qoidalarini belgilang.

Fayllar: `bot.py` — Telegram bot, menyu, eslatma va hisobot; `webapp_api.py` — Telegram initData tekshiruvi va davomat API; `webapp/` — Vercel frontend; `deploy/` — alohida xizmat va deploy sozlamalari.
