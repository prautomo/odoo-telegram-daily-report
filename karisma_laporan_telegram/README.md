# Daily Factory Report via Telegram (Odoo 20)

Odoo module `karisma_laporan_telegram` by [Karisma Technologies](https://karismatech.com/odoo/), Bandung, Indonesia.
License: LGPL-3. Branch `20.0` targets Odoo 20.

Every morning (Monday to Saturday, 06.30 WIB) management receives a summary in a Telegram group or private chat:

- production per product and material cost,
- new orders, deliveries, invoices, payments received,
- attendance and overtime per department with estimated cost,
- finished goods stock,
- **Needs attention**: overdue receivables, raw materials running low, unshipped orders, overtime spikes.

Quick commands: `/laporan`, `/hariini`, `/produksi`, `/stok`, `/lembur`, `/piutang`, `/berhenti`, `/bantuan`.
All figures are read directly from Odoo. No AI, no re-typing. Interface and messages are in Indonesian.

## Setup

1. Create a bot with @BotFather and copy the token.
2. Odoo: Settings > Bot Telegram > Sambungkan bot. Paste the token, click **Sambungkan**. Odoo validates the token, sets a
   webhook with a random secret (`X-Telegram-Bot-Api-Secret-Token`), and registers the commands. Odoo must be reachable over
   HTTPS at `web.base.url`.
3. Send `/start` to the bot, or add the bot to a group.
4. Settings > Bot Telegram > Penerima: click **Setujui** (approve). Chats that are not approved never receive numbers.
5. Optional: **Kirim ringkasan sekarang** sends a test summary.

## Notes

- Depends on Manufacturing, Sales, Purchase, Inventory, Invoicing, Attendances. Products and raw materials are read from BoMs.
- Overtime uses Odoo 20 attendance overtime lines (Overtime 1 = 1.5x, Overtime 2 = 2x). Cost = hours x monthly wage / 173 x
  multiplier.
- Thresholds (4 days of raw material, 1.5 days of finished goods, +30% overtime) live in `models/laporan.py`.
- Time zone fixed to WIB (UTC+7). Sundays are skipped.
- System parameters: `karisma_telegram.token`, `karisma_telegram.webhook_secret`, `karisma_telegram.bot_username`,
  `karisma_telegram.catatan_kaki` (footer text), `karisma_telegram.daftar_bebas` (`1` = auto-approve every chat; only for
  demo databases with fictional data).

## Bahasa Indonesia

Modul Odoo 20 untuk mengirim ringkasan harian pabrik ke Telegram setiap pagi pukul 06.30 WIB, plus perintah cepat
(/stok, /lembur, /piutang). Penerima baru harus disetujui admin. Butuh bantuan memasang atau menyesuaikan untuk perusahaan
Anda (laporan lain, WhatsApp, alur persetujuan)? Hubungi Karisma Technologies: WhatsApp 0851-3351-3315,
info@karismatech.com, [karismatech.com/odoo](https://karismatech.com/odoo/).
