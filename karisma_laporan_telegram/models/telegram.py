"""Bot Telegram: penerima (chat pribadi dan grup), pengiriman lewat Telegram Bot API resmi, cron ringkasan pagi,
dan penanganan perintah."""
import logging
import secrets

import requests

from odoo import api, fields, models
from odoo.exceptions import UserError

from .laporan import WIB, e

_logger = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{metode}"
JALUR_WEBHOOK = "/karisma/telegram/webhook"
P_TOKEN = "karisma_telegram.token"
P_RAHASIA = "karisma_telegram.webhook_secret"
P_NAMA_BOT = "karisma_telegram.bot_username"
# "1" = siapa pun yang mengirim /start langsung disetujui (hanya untuk demo dengan data fiktif).
# Bawaan kosong: penerima baru harus disetujui admin di Pengaturan > Bot Telegram > Penerima (versi 1.3).
P_BEBAS = "karisma_telegram.daftar_bebas"
PERINTAH = [
    ("laporan", "Ringkasan harian (sama dengan pesan pagi)"),
    ("hariini", "Rencana produksi, pesanan, dan barang datang hari ini"),
    ("produksi", "Produksi 7 hari terakhir dan bulan ini"),
    ("stok", "Stok produk jadi dan bahan baku"),
    ("lembur", "Lembur bulan ini per departemen"),
    ("piutang", "Piutang dan faktur yang telat"),
    ("berhenti", "Berhenti menerima ringkasan pagi"),
    ("bantuan", "Daftar perintah"),
]
LAPORAN = ("laporan", "hariini", "produksi", "stok", "lembur", "piutang")
KEYBOARD = {"keyboard": [["/laporan", "/hariini"], ["/produksi", "/stok"], ["/lembur", "/piutang"]],
            "resize_keyboard": True, "is_persistent": True}


class TelegramChat(models.Model):
    _name = "karisma.telegram.chat"
    _description = "Penerima laporan Telegram"
    _order = "tanggal_daftar desc"

    name = fields.Char("Nama", required=True)
    chat_id = fields.Char("Chat ID", required=True, index=True)
    jenis = fields.Selection([("private", "Pribadi"), ("group", "Grup"), ("supergroup", "Grup"), ("channel", "Kanal")],
                             string="Jenis", default="private")
    username = fields.Char("Username Telegram")
    aktif_pagi = fields.Boolean("Terima ringkasan pagi", default=True)
    disetujui = fields.Boolean("Disetujui", default=False,
                               help="Hanya chat yang disetujui admin yang menerima laporan dan jawaban perintah.")
    tanggal_daftar = fields.Datetime("Terdaftar", default=fields.Datetime.now)
    terakhir = fields.Datetime("Pesan terakhir")
    jumlah_perintah = fields.Integer("Jumlah perintah")
    galat_terakhir = fields.Char("Galat kirim terakhir")

    _chat_unik = models.Constraint("UNIQUE(chat_id)", "Chat ini sudah terdaftar.")

    # ------------------------------------------------------------------ Bot API
    @api.model
    def _token(self):
        return self.env["ir.config_parameter"].sudo().get_str(P_TOKEN) or ""

    @api.model
    def _nama_bot(self):
        return (self.env["ir.config_parameter"].sudo().get_str(P_NAMA_BOT) or "").lower()

    @api.model
    def _daftar_bebas(self):
        return (self.env["ir.config_parameter"].sudo().get_str(P_BEBAS) or "") == "1"

    @api.model
    def _belum_disetujui(self, nama, chat_id):
        return (f"Chat ini belum disetujui untuk menerima laporan <b>{e(self.env.company.name)}</b>.\n\n"
                f"Minta admin Odoo membuka Pengaturan &gt; Bot Telegram &gt; Penerima, lalu menyetujui "
                f"<b>{e(nama)}</b> (Chat ID {e(chat_id)}).")

    def action_setujui(self):
        for rec in self:
            if rec.disetujui:
                continue
            rec.write({"disetujui": True, "aktif_pagi": True})
            try:
                rec._kirim(rec.chat_id, rec._salam(rec.name, grup=rec.jenis != "private"), keyboard=rec.jenis == "private")
            except Exception as ex:
                _logger.warning("Salam setelah disetujui gagal ke %s: %s", rec.chat_id, ex)
                rec.galat_terakhir = str(ex)[:200]

    @api.model
    def _api(self, metode, token=None, **data):
        token = token or self._token()
        if not token:
            raise UserError("Token bot Telegram belum diisi.")
        r = requests.post(API.format(token=token, metode=metode), json=data, timeout=20)
        try:
            hasil = r.json()
        except ValueError:
            hasil = {"ok": False, "description": f"HTTP {r.status_code}"}
        if not hasil.get("ok"):
            raise UserError(f"Telegram menolak {metode}: {hasil.get('description')}")
        return hasil.get("result")

    @api.model
    def _kirim(self, chat_id, teks, keyboard=True, balas=None):
        """keyboard: tombol perintah (hanya chat pribadi). balas: message_id yang dibalas (dipakai di grup)."""
        data = {"chat_id": chat_id, "text": teks, "parse_mode": "HTML", "disable_web_page_preview": True}
        if keyboard:
            data["reply_markup"] = KEYBOARD
        if balas:
            data["reply_parameters"] = {"message_id": balas, "allow_sending_without_reply": True}
        return self._api("sendMessage", **data)

    # ------------------------------------------------------------------ cron
    @api.model
    def _cron_ringkasan_pagi(self, paksa=False):
        hari_ini = (fields.Datetime.now() + WIB).date()
        if (hari_ini.weekday() == 6 and not paksa) or not self._token():
            return
        teks = self.env["karisma.laporan.pabrik"].susun("laporan")
        for c in self.sudo().search([("aktif_pagi", "=", True), ("disetujui", "=", True)]):
            try:
                self._kirim(c.chat_id, teks, keyboard=c.jenis == "private")
                c.galat_terakhir = False
            except Exception as ex:  # satu penerima gagal (memblokir bot, bot dikeluarkan) tidak menghentikan yang lain
                _logger.warning("Ringkasan pagi gagal ke %s: %s", c.chat_id, ex)
                c.galat_terakhir = str(ex)[:200]
                if any(k in str(ex) for k in ("blocked", "chat not found", "kicked", "not a member")):
                    c.aktif_pagi = False

    # ------------------------------------------------------------------ perintah
    @api.model
    def _bantuan(self, grup=False):
        teks = "<b>Perintah yang tersedia</b>\n" + "\n".join(f"/{k}  {v}" for k, v in PERINTAH) + \
            "\n\nRingkasan pagi dikirim Senin sampai Sabtu pukul 06.30 WIB."
        nama_bot = self.env["ir.config_parameter"].sudo().get_str(P_NAMA_BOT)
        if grup and nama_bot:
            teks += ("\n\nDi grup, bot hanya menjawab perintah, bukan obrolan. Kalau ada bot lain di grup ini, "
                     f"tambahkan nama bot, misalnya /laporan@{e(nama_bot)}.")
        return teks

    @api.model
    def _salam(self, nama, grup):
        tujuan = "grup ini" if grup else "chat ini"
        return (f"Halo {e(nama)}. Ini bot laporan <b>{e(self.env.company.name)}</b>.\n\n"
                "Angka di sini diambil langsung dari Odoo, tanpa diketik ulang dan tanpa AI. "
                f"Ringkasan pagi akan dikirim ke {tujuan} setiap Senin sampai Sabtu pukul 06.30 WIB.\n\n"
                + self._bantuan(grup))

    @api.model
    def _daftar(self, chat_id, nama, jenis, username=None):
        rec = self.sudo().search([("chat_id", "=", chat_id)], limit=1)
        if not rec:
            rec = self.sudo().create({"name": nama, "chat_id": chat_id, "jenis": jenis, "username": username,
                                      "disetujui": self._daftar_bebas()})
        elif rec.name != nama or rec.jenis != jenis:
            rec.write({"name": nama, "jenis": jenis})
        return rec

    @api.model
    def _tangani_update(self, update):
        pesan = update.get("message") or update.get("edited_message")
        chat = (pesan or {}).get("chat") or {}
        teks_masuk = (pesan or {}).get("text") or ""
        jenis = chat.get("type")
        _logger.info("Telegram masuk: update %s, chat %s, teks %s, perintah %s", update.get("update_id"), jenis,
                     bool(teks_masuk), teks_masuk.split()[0][:40] if teks_masuk.startswith("/") else "-")
        if not pesan or jenis not in ("private", "group", "supergroup"):
            return
        grup = jenis != "private"
        chat_id = str(chat["id"])
        dari = pesan.get("from") or {}
        Chat = self.sudo()
        nama_bot = self._nama_bot()

        # Telegram mengubah grup biasa menjadi supergrup: ID chat berganti
        if pesan.get("migrate_to_chat_id"):
            lama = Chat.search([("chat_id", "=", chat_id)], limit=1)
            baru_id = str(pesan["migrate_to_chat_id"])
            if lama and not Chat.search_count([("chat_id", "=", baru_id)]):
                lama.write({"chat_id": baru_id, "jenis": "supergroup"})
            return

        # bot dikeluarkan dari grup: ringkasan pagi ke grup itu berhenti
        keluar = pesan.get("left_chat_member") or {}
        if keluar.get("is_bot") and (keluar.get("username") or "").lower() == nama_bot:
            Chat.search([("chat_id", "=", chat_id)]).write({"aktif_pagi": False})
            return

        # bot dimasukkan ke grup: daftarkan grup dan menyapa sekali
        masuk = [m for m in pesan.get("new_chat_members") or []
                 if m.get("is_bot") and (m.get("username") or "").lower() == nama_bot]
        if grup and (masuk or pesan.get("group_chat_created") or pesan.get("supergroup_chat_created")):
            judul = chat.get("title") or chat_id
            rec = self._daftar(chat_id, judul, jenis)
            rec.aktif_pagi = True
            teks = self._salam(judul, grup=True) if rec.disetujui else self._belum_disetujui(judul, chat_id)
            self._kirim(chat_id, teks, keyboard=False)
            return

        kata = teks_masuk.strip().split()
        perintah, untuk = "", ""
        if kata and kata[0].startswith("/"):
            perintah, _sep, untuk = kata[0][1:].lower().partition("@")
        if grup:
            # di grup hanya perintah yang dijawab, dan bukan perintah untuk bot lain
            if not perintah or (untuk and untuk != nama_bot):
                return
            nama = chat.get("title") or chat_id
            rec = self._daftar(chat_id, nama, jenis)
        else:
            nama = " ".join(x for x in (dari.get("first_name"), dari.get("last_name")) if x) or chat_id
            rec = self._daftar(chat_id, nama, jenis, dari.get("username"))
        rec.write({"terakhir": fields.Datetime.now(), "jumlah_perintah": rec.jumlah_perintah + 1})

        if not rec.disetujui:
            # Belum disetujui admin: tidak ada angka yang dikirim. /start dan /berhenti tetap dicatat.
            if perintah == "berhenti":
                rec.aktif_pagi = False
            elif perintah == "start":
                rec.aktif_pagi = True
            self._kirim(chat_id, self._belum_disetujui(nama, chat_id), keyboard=False,
                        balas=pesan.get("message_id") if grup else None)
            return

        lap = self.env["karisma.laporan.pabrik"].sudo()
        try:
            if perintah == "start":
                teks = self._salam(nama if grup else (dari.get("first_name") or nama), grup)
                rec.aktif_pagi = True
            elif perintah == "berhenti":
                rec.aktif_pagi = False
                tujuan = "grup ini" if grup else "chat ini"
                teks = (f"Ringkasan pagi dihentikan untuk {tujuan}. Perintah lain tetap bisa dipakai. "
                        "Ketik /start untuk berlangganan lagi.")
            elif perintah in LAPORAN:
                teks = lap.susun(perintah)
            else:
                teks = self._bantuan(grup)  # pesan bukan perintah di chat pribadi (teks bebas, stiker, foto)
        except Exception:
            _logger.exception("Gagal menyusun laporan Telegram untuk perintah %s", perintah)
            teks = "Maaf, laporan gagal disusun. Kesalahan sudah dicatat di server."
        self._kirim(chat_id, teks, keyboard=not grup, balas=pesan.get("message_id") if grup else None)


class TelegramAtur(models.TransientModel):
    _name = "karisma.telegram.atur"
    _description = "Sambungkan bot Telegram"

    name = fields.Char(default="Bot Telegram")
    token = fields.Char("Token bot", help="Dari @BotFather. Kosongkan kalau bot sudah tersambung dan token tidak diganti.")
    nama_bot = fields.Char("Bot tersambung", readonly=True)
    alamat_webhook = fields.Char("Alamat webhook", readonly=True)
    jumlah_penerima = fields.Integer("Penerima ringkasan pagi", readonly=True)
    status = fields.Char("Status webhook", readonly=True)

    @api.model
    def default_get(self, daftar):
        nilai = super().default_get(daftar)
        icp = self.env["ir.config_parameter"].sudo()
        nama = icp.get_str(P_NAMA_BOT)
        nilai.update(
            nama_bot=f"@{nama}" if nama else "Belum ada",
            alamat_webhook=icp.get_str("web.base.url") + JALUR_WEBHOOK,
            jumlah_penerima=self.env["karisma.telegram.chat"].search_count([("aktif_pagi", "=", True),
                                                                            ("disetujui", "=", True)]),
        )
        if icp.get_str(P_TOKEN):
            try:
                info = self.env["karisma.telegram.chat"]._api("getWebhookInfo")
                galat = info.get("last_error_message")
                nilai["status"] = (f"Aktif, {info.get('pending_update_count', 0)} pesan antre"
                                   + (f". Galat terakhir: {galat}" if galat else ""))
            except Exception as ex:
                nilai["status"] = f"Tidak bisa dicek: {ex}"
        else:
            nilai["status"] = "Belum tersambung"
        return nilai

    def action_sambungkan(self):
        self.ensure_one()
        icp = self.env["ir.config_parameter"].sudo()
        Chat = self.env["karisma.telegram.chat"]
        token = (self.token or "").strip() or icp.get_str(P_TOKEN)
        if not token:
            raise UserError("Isi token bot dari @BotFather dulu.")
        saya = Chat._api("getMe", token=token)
        rahasia = secrets.token_urlsafe(32)
        url = icp.get_str("web.base.url").rstrip("/") + JALUR_WEBHOOK
        if not url.startswith("https://"):
            raise UserError(f"Webhook Telegram wajib HTTPS. web.base.url sekarang: {url}")
        Chat._api("setWebhook", token=token, url=url, secret_token=rahasia, allowed_updates=["message"],
                  drop_pending_updates=True)
        Chat._api("setMyCommands", token=token, commands=[{"command": k, "description": v} for k, v in PERINTAH])
        icp.set_str(P_TOKEN, token)
        icp.set_str(P_RAHASIA, rahasia)
        icp.set_str(P_NAMA_BOT, saya.get("username") or "")
        return self._buka_lagi(f"Bot @{saya.get('username')} tersambung. Kirim /start ke bot dari Telegram, lalu setujui "
                               "chat itu di menu Penerima.")

    def action_kirim_sekarang(self):
        penerima = self.env["karisma.telegram.chat"].search([("aktif_pagi", "=", True), ("disetujui", "=", True)])
        if not penerima:
            raise UserError("Belum ada penerima yang disetujui. Kirim /start ke bot dari Telegram atau masukkan bot ke "
                            "grup, lalu setujui di menu Penerima.")
        self.env["karisma.telegram.chat"]._cron_ringkasan_pagi(paksa=True)
        return self._buka_lagi(f"Ringkasan dikirim ke {len(penerima)} penerima.")

    def action_putuskan(self):
        icp = self.env["ir.config_parameter"].sudo()
        if icp.get_str(P_TOKEN):
            try:
                self.env["karisma.telegram.chat"]._api("deleteWebhook")
            except Exception as ex:
                _logger.warning("deleteWebhook gagal: %s", ex)
        for p in (P_TOKEN, P_RAHASIA, P_NAMA_BOT):
            icp.set_str(p, "")
        return self._buka_lagi("Bot diputuskan. Token dihapus dari Odoo.")

    def _buka_lagi(self, pesan):
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {"title": "Bot Telegram", "message": pesan, "type": "success", "sticky": False,
                       "next": {"type": "ir.actions.act_window", "res_model": self._name, "view_mode": "form",
                                "target": "current", "views": [[False, "form"]]}},
        }
