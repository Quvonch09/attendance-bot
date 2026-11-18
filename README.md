# Telegram davomat bot — dastlabki versiya

Bot xodimlarning davomatini Telegram orqali qayd etadi va adminlarga real vaqtda xabar hamda kun yakuni hisobotini yuboradi.

## Tasdiqlangan sozlamalar

- Ish joyi: Innova maktabi xaritadagi nuqta — `38.8557873, 65.8002788`.
- Ruxsat etilgan radius: **300 metr**.
- Vaqt zonasi: `Asia/Tashkent`.
- Adminlar: Telegram ID `8048583227` va `1922170479`.
- Eslatmalar: 09:00 ishga kelish, 12:00 tushlikka chiqish, 14:30 ishga qaytish, 18:00 ishdan chiqish.
- 12:00 va 14:30 dagi tasdiqlar bo‘lmasa, 10 daqiqadan keyin adminlarga ogohlantirish yuboriladi.
- 18:15 da kunlik hisobot yuboriladi.
- 09:30 dan keyin kelayotgan xodim matn yoki ovozda kechikish sababini yuboradi; izoh adminlarga boradi.

## Ishga tushirish

1. BotFather orqali Telegram bot yarating va bot tokenini oling.
2. Python 3.11 yoki undan yangiroq muhitda shu katalogga o‘ting.
3. `.env.example` faylidan `.env` nusxa oling va `BOT_TOKEN=` qatoriga tokenni kiriting. Tokenni ommaga yubormang yoki Git’ga qo‘shmang.
4. Kutubxonalarni o‘rnating: `python -m pip install -r requirements.txt`.
5. Botni ishga tushiring: `python bot.py`.
6. Har ikki admin bot sahifasini ochib `/start` yuborsin. Telegram bot foydalanuvchi avval suhbatni boshlamaguncha unga o‘zi xabar yubora olmaydi.
7. Xodimlar botga `/start` yuborib, rozilik va ro‘yxatdan o‘tish bosqichlarini bajarsin. Yuz surati ikkala adminga bir martalik tasdiq uchun boradi; ulardan biri tasdiqlagach xodim davomatni ishlata oladi.

Bot serverda doimiy ishlashi kerak; jarayon to‘xtasa, eslatmalar vaqtida yuborilmaydi. Dastlabki ishga tushirishdan oldin adminlar va bir nechta xodim bilan sinov o‘tkazing.

## GitHub CI/CD deploy

Repo xususiy bo‘lishi kerak. Bir martalik server tayyorlash `deploy/bootstrap-server.sh` orqali alohida `cue-attendance-bot` akkaunti, faqat bot uchun katalog/service va cheklangan restart ruxsatini o‘rnatadi; mavjud loyiha va servicelarga tegmaydi. GitHub Actions faqat shu akkaunt bilan `/opt/cue-attendance-bot/releases/` ichiga kod yuboradi va bot servisinigina restart qiladi.

Repo Actions secret’lari: `DEPLOY_SSH_KEY` va `BOT_TOKEN`. Server manzili, deploy akkaunti va host key repo ichida pin qilingan; runner server key’ni avtomatik qabul qilmaydi. `BOT_TOKEN` bo‘lmasa, CI kodni alohida release papkasiga joylaydi, lekin service’ni ishga tushirmaydi. Eski oshkor bo‘lgan token emas, yangi Telegram tokenini `BOT_TOKEN` secret’iga xavfsiz qo‘shgandan keyingi deploy service’ni ishga tushiradi.

## Xodim uchun oqim

Birinchi `/start` da bot yuz surati nima uchun saqlanishini va u ikki adminga bir martalik ko‘rib chiqish uchun yuborilishini tushuntiradi, so‘ng rozilik so‘raydi. Rozilikdan keyin ism-familiya va yuz aniq ko‘rinadigan ro‘yxat surati olinadi. Adminlardan biri yuzni tasdiqlagach xodimga davomat tugmalari ochiladi; rad etsa, xodim qayta ro‘yxatdan o‘tishi mumkin. Rozilik bermagan odam biometrik davomatga yozilmaydi.

09:00 dagi kelish tasdig‘ida bot Telegram lokatsiyasini oladi va koordinatani ofisdan 300 metr ichida ekanini tekshiradi. Keyin 3–10 soniyalik yangi video-selfi so‘raydi; undagi yuz ro‘yxat surati bilan solishtiriladi va boshni tasodifiy tanlangan tomonga burish orqali harakat tekshiriladi. 09:30 dan keyin kelayotgan xodim avvalo kechikish sababini matn yoki ovozda yuboradi.

Boshlang‘ich surat bir marta admin tomonidan tasdiqlangach, keyingi kelishlar yuz va lokatsiya bo‘yicha avtomatik tekshiriladi; kundalik kelishni admin alohida tasdiqlamaydi. Boshqa tasdiqlar tugma bilan qayd etiladi. Har bir muhim voqeadan so‘ng adminlarga xodim nomi va vaqti yuboriladi. `/today` kunlik hisobotni, `/live` bugungi holatni, `/pending` esa admin tasdig‘ini kutayotgan boshlang‘ich suratlarni ko‘rsatadi va tasdiq tugmalarini qayta yuboradi; bu buyruqlar faqat ko‘rsatilgan Telegram ID’larga ochiq.

## Hisobot

Hisobotda kelgan, tushlikka chiqqan, qaytgan va ishdan chiqqan vaqtlar; 09:00 dan keyingi kechikish daqiqalari; tasdiqlanmagan vaqtlar; hamda ish vaqti kelib-chiqish qaydlari bo‘lsa hisoblab chiqariladi. Ish vaqtidan tushlik oralig‘i faqat tushlikka chiqish va qaytish ikkalasi ham qayd etilgandagina ayiriladi. Kelish yoki chiqish qaydi yetishmasa, bot to‘liq ish soatini taxmin qilmaydi.

## Muhim cheklov va xavfsizlik

- Bot tokeni ulanmagan holda servis ishga tushmaydi; yangi token kelmaguncha CI kodni yuklasa ham servisni o‘chiq qoldiradi.
- Yuzni solishtirish `DeepFace` orqali shu serverda bajariladi; model og‘ir bo‘lishi mumkin va ilk ishga tushishda model fayllari internetdan olinadi.
- Videodagi harakat tekshiruvi oddiy prototip: u bir nechta kadrda yuz borligini va yuz ramkasi harakatini tekshiradi, lekin sertifikatlangan anti-spoof/liveness tizimi emas. Boshlang‘ich yuz suratini admin bir marta tasdiqlaydi; keyingi kundalik selfilarni inson ko‘rib chiqmaydi. Ekrandagi soxta video bilan aldash ehtimoli bor. Bu usulni maosh yoki intizomiy chora uchun yagona dalil deb qabul qilmang; ishlab chiqarishda maxsus tekshirilgan liveness xizmati kerak.
- Telegram lokatsiyasi va selfi to‘liq soxtalashtirishdan himoyalanmagan. Soxta GPS, kamera sifati va yorug‘lik yuz tekshiruviga ta’sir qiladi.
- Yuz surati, lokatsiya va davomat tarixi nozik shaxsiy ma’lumot hisoblanadi. Ishga tushirishdan oldin xodimlarga ma’lumot bering va zarur roziliklarni oling; kim ko‘rishi, qancha saqlanishi, zaxira nusxalari va o‘chirish tartibini belgilang. Loyiha suratlar va SQLite bazasini serverdagi `data/` papkasida saqlaydi; papka ruxsati cheklangan, ammo diskdagi ma’lumotlar shifrlanmaydi.
- Sinovdan keyin yuz ma’lumotlarini saqlash muddati, server joylashuvi va disk shifrlashni belgilab, shundan keyingina ishga tushiring.

## Fayllar

- `bot.py` — Telegram bot, eslatmalar, ro‘yxatdan o‘tish, davomat qaydlari va hisobot.
- `.env.example` — boshlang‘ich sozlama namunasi.
- `requirements.txt` — Python kutubxonalari.
- `data/` — bot ishga tushganda yaratiladigan SQLite va yuz rasmlari papkasi; bu ma’lumotlarni maxfiy saqlang.
