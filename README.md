# Telegram davomat boti

Bot xodimlarning kelish-ketishini Telegram orqali qayd etadi. Oddiy xodim, admin va nazoratchi huquqlari bitta akkauntda birga bo‘lishi mumkin. Telegram ID `8048583227` admin/nazoratchi va xodim sifatida ishlaydi; `1922170479` mavjud admin huquqini saqlaydi.

## Ish tartibi

Ish joyi Innova maktabi koordinatasi (`38.8557873, 65.8002788`), geofence radiusi 300 metr, vaqt zonasi `Asia/Tashkent`. Xodim `/start` orqali ro‘yxatdan o‘tadi, biometrik suratdan foydalanishga rozilik beradi, ism-familiyasini va selfisini yuboradi. Admin yoki nazoratchilardan biri ro‘yxatdagi yuz suratini bir marta ko‘rib tasdiqlaydi. Xodim o‘z suratini o‘zi tasdiqlay olmaydi.

Eslatmalar: 09:00 kelish, 12:00 tushlikka chiqish, 14:30 ishga qaytish va 18:00 ishdan chiqish. Tushlikka chiqish yoki qaytish belgilanmasa, 10 daqiqada nazoratchiga xabar boradi; 18:15 da kunlik hisobot yuboriladi. Xodim 09:30 dan keyin kelsa, Telegram chatga kechikish sababini matn yoki ovozda yuborishi kerak; matn va ovoz tegishli nazoratchilarga uzatiladi.

Web App API yoqilgach, to‘rtta amalning har biri uchun Telegram imzosi, tasdiqlangan xodim, 300 metr geofence, bir martalik topshiriq, kamera kadrlari va ro‘yxatdagi yuz bilan moslik tekshiriladi. Ketma-ketlik: kelish → tushlikka chiqish → tushlikdan qaytish → ishdan chiqish. Har bir muvaffaqiyatli qayd admin/nazoratchilarga ism-vaqt bilan yuboriladi. Yuzni avtomatik solishtirish uchun DeepFace ishlatiladi.

## Web App va server

`webapp/` katalogida Telegram Mini App uchun statik frontend bor. U `https://api.orivasmarket.uz:8443/` manzilida bot API bilan bir xil origin ostida ishlaydi. Bot Web App’ni inline HTTPS tugma orqali ochadi; server raw `Telegram.WebApp.initData` imzosini bot tokeni bilan tekshiradi. `initDataUnsafe` autentifikatsiyaga ishlatilmaydi.

`api.orivasmarket.uz` manzili `169.58.215.170` server IP manziliga yo‘naltirilgan va unga mos TLS sertifikati serverda mavjud. Sertifikat faqat attendance uchun yangi 8443 Nginx blokida ishlatiladi; mavjud 443 virtual xost, DNS, boshqa sayt va xizmatlarga o‘zgartirish kiritilmaydi. Nginx barcha so‘rovlarni faqat `127.0.0.1:8765` loopback manzilidagi davomat botiga uzatadi.

Production API sozlanganda `WEBAPP_URL=https://api.orivasmarket.uz:8443/` va `WEBAPP_ORIGIN=https://api.orivasmarket.uz:8443` qiymatlari aniq mos bo‘lishi kerak. `WEBAPP_API_ENABLED=0` xavfsiz boshlang‘ich sozlama; TLS, inline URL, frontend va API smoke-test muvaffaqiyatli bo‘lgach uni `1` ga o‘zgartiring. Vercel Hobby rejasini bu ish joyi xizmatida ishlatmaymiz: https://vercel.com/legal/terms .

## O‘rnatish

Python 3.11 yoki yangi versiya kerak.

```bash
python -m pip install -r requirements.txt
cp .env.example .env
# .env ichiga BOT_TOKEN va kerakli sozlamalarni kiriting
python -m py_compile bot.py webapp_api.py
python tests/test_security_core.py -v
python bot.py
```

`.env`, bot tokeni, `data/` ichidagi yuz rasmlari va SQLite faylini GitHub’ga yubormang.

## GitHub Actions deploy

GitHub Actions avval testlarni bajaradi. Testlar muvaffaqiyatli bo‘lgach, alohida `cue-attendance-bot` foydalanuvchisi, `/opt/cue-attendance-bot/releases/` katalogi va `cue-attendance-bot.service` yangilanadi. Boshqa loyiha yoki xizmatlarga tegilmaydi. Avtomatik server deploy uchun repository secret `DEPLOY_SSH_KEY` kerak; secret bo‘lmasa testlar ishlaydi, deploy esa o‘tkazib yuboriladi. Bot tokeni faqat serverdagi himoyalangan `.env` faylida qoladi.

## Biometrik cheklovlar

Haar yuz topish va kadrlar orasidagi harakat belgisi oddiy prototip tekshiruvi, sertifikatlangan tiriklik yoki soxtalashtirishga qarshi tizim emas. Soxta GPS yoki ekrandagi video bilan aldash xavfi qoladi; davomat natijasini maosh yoki intizomiy qarorning yagona dalili qilmang. Ish boshlashdan oldin rozilik, saqlash muddati, ma’lumotni kim ko‘rishi, o‘chirish va zaxira nusxa qoidalarini belgilang.

## Fayllar

- `bot.py` — Telegram bot, rol/menu, eslatma va hisobot.
- `webapp_api.py` — Telegram imzosi, bir martalik topshiriq, yuz/GPS va davomat API.
- `webapp/` — to‘rtta tasdiqlashni qamrab olgan statik frontend.
- `tests/test_security_core.py` — HMAC, challenge takrori, same-origin va amal ketma-ketligi testlari.
- `deploy/` — faqat bot uchun systemd, Nginx va deploy sozlamalari.
