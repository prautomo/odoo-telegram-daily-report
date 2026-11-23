"""Penyusun teks laporan pabrik untuk Telegram (format HTML Telegram). Semua angka dibaca dari data Odoo."""
import datetime as dt
import html
from collections import defaultdict

from odoo import fields, models

WIB = dt.timedelta(hours=7)  # Asia/Jakarta tanpa musim panas
HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
HARI3 = ["Sen", "Sel", "Rab", "Kam", "Jum", "Sab", "Min"]
BULAN = ["", "Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September",
         "Oktober", "November", "Desember"]
BULAN3 = ["", "Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
JAM_SEBULAN = 173  # pembagi upah per jam (PP 35/2021)


def ribuan(n):
    return f"{round(n):,}".replace(",", ".")


def koma(x, d=1):
    return f"{x:,.{d}f}".replace(",", "_").replace(".", ",").replace("_", ".")


def rp(n):
    return "Rp " + ribuan(n)


def rp_singkat(n):
    a = abs(n)
    if a >= 1e9:
        return f"Rp {koma(n / 1e9, 2)} miliar"
    if a >= 1e6:
        return f"Rp {koma(n / 1e6, 1)} juta"
    return rp(n)


def e(s):
    return html.escape(str(s or ""))


def tgl(d, hari=True):
    s = f"{d.day} {BULAN[d.month]} {d.year}"
    return f"{HARI[d.weekday()]}, {s}" if hari else s


def _satuan(move):
    """Satuan stock.move: uom_id di Odoo 20, product_uom di versi sebelumnya."""
    return move.uom_id if "uom_id" in move._fields else move.product_uom


def tgl_pendek(d):
    return f"{d.day} {BULAN3[d.month]}"


class LaporanPabrik(models.AbstractModel):
    _name = "karisma.laporan.pabrik"
    _description = "Penyusun laporan pabrik untuk Telegram"

    # ------------------------------------------------------------------ pembantu
    def _hari_ini(self):
        return (fields.Datetime.now() + WIB).date()

    def _rentang(self, a, b=None):
        """Tanggal WIB a..b (inklusif) -> (awal, akhir) UTC tanpa zona, format simpan Odoo."""
        b = b or a
        return (dt.datetime.combine(a, dt.time()) - WIB, dt.datetime.combine(b, dt.time()) - WIB + dt.timedelta(days=1))

    def _wib(self, waktu):
        return (waktu + WIB).date()

    def _kg(self):
        return self.env.ref("uom.product_uom_kgm", raise_if_not_found=False)

    def _jumlah(self, q, uom):
        if uom == self._kg():
            return f"{koma(q / 1000, 1)} ton"
        return f"{ribuan(q)} {e(uom.name)}"

    def _nama(self, produk):
        return e(produk.name)

    def _catatan_kaki(self):
        teks = self.env["ir.config_parameter"].sudo().get_str("karisma_telegram.catatan_kaki") or ""
        return f"\n\n<i>{e(teks)}</i>" if teks else ""

    def _tanggal_laporan(self):
        """Hari operasi terakhir sebelum hari ini (dalam 14 hari) yang punya produksi atau absensi."""
        hari_ini = self._hari_ini()
        a, b = self._rentang(hari_ini - dt.timedelta(days=14), hari_ini - dt.timedelta(days=1))
        mo = self.env["mrp.production"].search([("state", "=", "done"), ("date_finished", ">=", a),
                                                ("date_finished", "<", b)], order="date_finished desc", limit=1)
        ab = self.env["hr.attendance"].search([("check_in", ">=", a), ("check_in", "<", b)], order="check_in desc", limit=1)
        calon = [self._wib(w) for w in (mo.date_finished, ab.check_in) if w]
        return max(calon) if calon else hari_ini - dt.timedelta(days=1)

    # ------------------------------------------------------------------ pengambil data
    def _produksi(self, a, b=None):
        x, y = self._rentang(a, b)
        mo = self.env["mrp.production"].search([("state", "=", "done"), ("date_finished", ">=", x), ("date_finished", "<", y)])
        per = defaultdict(float)
        for m in mo:
            per[m.product_id] += m.uom_id._compute_quantity(m.qty_produced, m.product_id.uom_id)
        biaya = sum(mv.product_id.standard_price * mv.quantity for mv in mo.move_raw_ids if mv.state == "done")
        return mo, per, biaya

    def _absen(self, a, b=None):
        x, y = self._rentang(a, b)
        return self.env["hr.attendance"].search([("check_in", ">=", x), ("check_in", "<", y)])

    @staticmethod
    def _tarif(baris):
        t = baris.work_entry_type_id
        if not t:
            return 1.0
        rate = getattr(t, "amount_rate", 0) or 1.0
        if rate == 1.0 and "OVERTIME" in (getattr(t, "code", "") or "").upper():
            rate = 1.5
        return rate

    def _lembur(self, baris):
        """{departemen: [jam, biaya, {karyawan: jam}]} untuk baris absensi berjenis lembur."""
        hasil = defaultdict(lambda: [0.0, 0.0, defaultdict(float)])
        for r in baris:
            tarif = self._tarif(r)
            if tarif <= 1.0:
                continue
            emp = r.employee_id
            dept = emp.department_id.name or "Tanpa departemen"
            upah = getattr(emp, "wage", 0) or 0
            hasil[dept][0] += r.worked_hours
            hasil[dept][1] += r.worked_hours * upah / JAM_SEBULAN * tarif
            hasil[dept][2][emp] += r.worked_hours
        return hasil

    def _piutang(self):
        hari_ini = self._hari_ini()
        inv = self.env["account.move"].search([("move_type", "=", "out_invoice"), ("state", "=", "posted"),
                                               ("payment_state", "in", ("not_paid", "partial"))])
        telat = inv.filtered(lambda m: m.invoice_date_due and m.invoice_date_due < hari_ini)
        return inv, telat, hari_ini

    def _produk_bom(self):
        bom = self.env["mrp.bom"].search([])
        jadi = bom.mapped("product_tmpl_id.product_variant_ids")
        bahan = bom.mapped("bom_line_ids.product_id") - jadi
        return jadi, bahan

    def _rata_harian(self, sampai, jenis):
        """Rata-rata pemakaian bahan (jenis='bahan') atau pengiriman (jenis='kirim') per hari kerja, 7 hari s.d. tanggal."""
        x, y = self._rentang(sampai - dt.timedelta(days=6), sampai)
        if jenis == "bahan":
            dom = [("raw_material_production_id", "!=", False)]
        else:
            dom = [("picking_type_id.code", "=", "outgoing")]
        mv = self.env["stock.move"].search(dom + [("state", "=", "done"), ("date", ">=", x), ("date", "<", y)])
        hari = {self._wib(m.date) for m in mv} or {sampai}
        total = defaultdict(float)
        for m in mv:
            total[m.product_id] += _satuan(m)._compute_quantity(m.quantity, m.product_id.uom_id)
        return {p: q / len(hari) for p, q in total.items()}

    def _po_datang(self, produk):
        """PO terkonfirmasi yang belum diterima penuh untuk produk ini: tanggal paling awal dan nomornya."""
        baris = self.env["purchase.order.line"].search([("product_id", "=", produk.id), ("order_id.state", "=", "purchase")])
        baris = baris.filtered(lambda l: l.qty_received < l.product_qty).sorted("date_planned")
        return baris[:1]

    def _hari_ini_data(self):
        hari_ini = self._hari_ini()
        x, y = self._rentang(hari_ini)
        mo = self.env["mrp.production"].search([("state", "in", ("confirmed", "progress", "to_close")),
                                                ("date_start", "<", y)])
        so = self.env["sale.order"].search([("state", "=", "sale"), ("delivery_status", "in", ("pending", "partial"))])
        po = self.env["purchase.order"].search([("state", "=", "purchase"), ("receipt_status", "in", ("pending", "partial")),
                                                ("date_planned", "<", y)])
        return hari_ini, mo, so, po

    # ------------------------------------------------------------------ pesan
    def susun(self, perintah, tanggal=None):
        fungsi = {
            "laporan": self._pesan_ringkasan, "hariini": self._pesan_hari_ini, "produksi": self._pesan_produksi,
            "stok": self._pesan_stok, "lembur": self._pesan_lembur, "piutang": self._pesan_piutang,
        }[perintah]
        if perintah in ("laporan", "produksi", "lembur"):
            return fungsi(tanggal or self._tanggal_laporan()) + self._catatan_kaki()
        return fungsi() + self._catatan_kaki()

    def _bagian_perhatian(self, d):
        poin = []
        _inv, telat, hari_ini = self._piutang()
        if telat:
            per = defaultdict(float)
            for m in telat:
                per[m.partner_id] += m.amount_residual
            terbesar = max(per, key=per.get)
            lama = max((hari_ini - m.invoice_date_due).days for m in telat if m.partner_id == terbesar)
            poin.append(f"Piutang lewat jatuh tempo {rp_singkat(sum(per.values()))} ({len(telat)} faktur). "
                        f"Terbesar: {e(terbesar.name)}, telat sampai {lama} hari.")
        jadi, bahan = self._produk_bom()
        pakai = self._rata_harian(d, "bahan")
        for p in bahan.sorted("default_code"):
            rata = pakai.get(p, 0)
            if rata <= 0:
                continue
            cukup = p.qty_available / rata
            if cukup < 4:
                po = self._po_datang(p)
                info = (f" {e(po.order_id.name)} dijadwalkan datang {tgl_pendek(self._wib(po.date_planned))}."
                        if po else " Belum ada pesanan pembelian.")
                poin.append(f"{self._nama(p)}: sisa {self._jumlah(p.qty_available, p.uom_id)}, cukup {koma(cukup)} hari.{info}")
        kirim = self._rata_harian(d, "kirim")
        for p in jadi.sorted("default_code"):
            rata = kirim.get(p, 0)
            if rata > 0 and p.qty_available / rata < 1.5:
                poin.append(f"Stok {self._nama(p)} tinggal {self._jumlah(p.qty_available, p.uom_id)}, "
                            f"cukup {koma(p.qty_available / rata)} hari pengiriman.")
        x, _y = self._rentang(d)
        lama_so = self.env["sale.order"].search([("state", "=", "sale"), ("delivery_status", "in", ("pending", "partial")),
                                                 ("date_order", "<", x)])
        if lama_so:
            poin.append(f"{len(lama_so)} pesanan belum dikirim lebih dari sehari ({rp_singkat(sum(lama_so.mapped('amount_total')))}).")
        jam_hari = sum(v[0] for v in self._lembur(self._absen(d)).values())
        sebelum = self._lembur(self._absen(d - dt.timedelta(days=7), d - dt.timedelta(days=1)))
        hari_sebelum = {self._wib(a.check_in) for a in self._absen(d - dt.timedelta(days=7), d - dt.timedelta(days=1))}
        if hari_sebelum and jam_hari:
            rata = sum(v[0] for v in sebelum.values()) / len(hari_sebelum)
            if rata and jam_hari > rata * 1.3:
                poin.append(f"Lembur {koma(jam_hari)} jam, naik {koma((jam_hari / rata - 1) * 100, 0)}% dari rata-rata 7 hari "
                            f"({koma(rata)} jam).")
        if not poin:
            return "Tidak ada yang perlu perhatian."
        return "\n".join(f"• {p}" for p in poin)

    def _pesan_ringkasan(self, d):
        perusahaan = self.env.company
        mo, per, biaya = self._produksi(d)
        bag = [f"<b>📊 Ringkasan harian</b>\n<b>{e(perusahaan.name)}</b>\n{tgl(d)}"]

        total = sum(per.values())
        if mo:
            baris = "\n".join(f"• {self._nama(p)}: {self._jumlah(q, p.uom_id)}" for p, q in sorted(per.items(), key=lambda x: x[0].default_code or ""))
            semua_kg = all(p.uom_id == self._kg() for p in per)
            ringkas = f"Total <b>{koma(total / 1000, 1)} ton</b> dari {len(mo)} perintah produksi" if semua_kg else f"{len(mo)} perintah produksi selesai"
            ongkos = f"\nBiaya bahan {rp_singkat(biaya)}" + (f" ({rp(biaya / total)} per kg)" if semua_kg and total else "")
            bag.append(f"<b>🏭 Produksi</b>\n{baris}\n{ringkas}{ongkos}")
        else:
            bag.append("<b>🏭 Produksi</b>\nTidak ada produksi selesai.")

        x, y = self._rentang(d)
        so = self.env["sale.order"].search([("state", "=", "sale"), ("date_order", ">=", x), ("date_order", "<", y)])
        kirim = self.env["stock.picking"].search([("picking_type_code", "=", "outgoing"), ("state", "=", "done"),
                                                  ("date_done", ">=", x), ("date_done", "<", y)])
        kg_kirim = sum(_satuan(m)._compute_quantity(m.quantity, m.product_id.uom_id) for m in kirim.move_ids
                       if m.product_id.uom_id == self._kg())
        faktur = self.env["account.move"].search([("move_type", "=", "out_invoice"), ("state", "=", "posted"),
                                                  ("invoice_date", "=", d)])
        bayar = self.env["account.payment"].search([("payment_type", "=", "inbound"), ("partner_type", "=", "customer"),
                                                    ("date", "=", d), ("state", "not in", ("draft", "canceled", "rejected"))])
        jual = [f"Pesanan baru: {len(so)}, {rp_singkat(sum(so.mapped('amount_total')))}" if so else "Pesanan baru: tidak ada"]
        jual.append(f"Terkirim: {len(kirim)} pengiriman" + (f", {koma(kg_kirim / 1000, 1)} ton" if kg_kirim else "") if kirim else "Terkirim: tidak ada")
        if faktur:
            jual.append(f"Faktur terbit: {len(faktur)}, {rp_singkat(sum(faktur.mapped('amount_total')))}")
        jual.append(f"Uang masuk: {rp_singkat(sum(bayar.mapped('amount')))} ({len(bayar)} pembayaran)" if bayar else "Uang masuk: tidak ada")
        bag.append("<b>🛒 Penjualan</b>\n" + "\n".join(jual))

        absen = self._absen(d)
        jumlah_kary = self.env["hr.employee"].search_count([])
        hadir = len(absen.mapped("employee_id"))
        lembur = self._lembur(absen)
        jam = sum(v[0] for v in lembur.values())
        orang = len({k for v in lembur.values() for k in v[2]})
        tk = [f"Hadir: {hadir} dari {jumlah_kary} orang"]
        if jam:
            tk.append(f"Lembur: {orang} orang, {koma(jam)} jam, sekitar {rp_singkat(sum(v[1] for v in lembur.values()))}")
            tk += [f"• {e(k)} {koma(v[0])} jam" for k, v in sorted(lembur.items(), key=lambda x: -x[1][0])]
        else:
            tk.append("Lembur: tidak ada")
        bag.append("<b>👷 Tenaga kerja</b>\n" + "\n".join(tk))

        jadi, _bahan = self._produk_bom()
        rata_kirim = self._rata_harian(d, "kirim")
        stok = []
        for p in jadi.sorted("default_code"):
            r = rata_kirim.get(p, 0)
            stok.append(f"• {self._nama(p)}: {self._jumlah(p.qty_available, p.uom_id)}" + (f" (cukup {koma(p.qty_available / r)} hari)" if r else ""))
        if stok:
            bag.append("<b>📦 Stok produk jadi saat ini</b>\n" + "\n".join(stok))

        bag.append("<b>⚠️ Perlu perhatian</b>\n" + self._bagian_perhatian(d))
        bag.append(self._teks_hari_ini(judul="📅 Hari ini"))
        return "\n\n".join(bag)

    def _teks_hari_ini(self, judul):
        hari_ini, mo, so, po = self._hari_ini_data()
        baris = [f"<b>{judul}</b>, {tgl(hari_ini)}"]
        if mo:
            per = defaultdict(float)
            for m in mo:
                per[m.product_id] += m.uom_id._compute_quantity(m.product_qty, m.product_id.uom_id)
            baris.append("Produksi direncanakan: " + ", ".join(f"{self._nama(p)} {self._jumlah(q, p.uom_id)}" for p, q in per.items()))
        else:
            baris.append("Produksi direncanakan: belum ada perintah produksi")
        if so:
            baris.append(f"Pesanan menunggu kirim: {len(so)} ({rp_singkat(sum(so.mapped('amount_total')))})")
            baris += [f"• {e(s.name)} {e(s.partner_id.name)}, {rp_singkat(s.amount_total)}" for s in so.sorted("date_order")[:5]]
        else:
            baris.append("Pesanan menunggu kirim: tidak ada")
        if po:
            baris.append(f"Barang datang dari pemasok: {len(po)} PO (" + ", ".join(e(p.partner_id.name) for p in po[:3])
                         + (", dan lainnya" if len(po) > 3 else "") + ")")
        return "\n".join(baris)

    def _pesan_hari_ini(self):
        return self._teks_hari_ini(judul="📅 Rencana hari ini")

    def _pesan_produksi(self, d):
        awal = d - dt.timedelta(days=6)
        baris = []
        for i in range(7):
            h = awal + dt.timedelta(days=i)
            mo, per, _b = self._produksi(h)
            if not mo:
                continue
            rinci = " · ".join(f"{e(p.default_code or p.name)} {koma(q / 1000, 1)}" for p, q in sorted(per.items(), key=lambda x: x[0].default_code or ""))
            baris.append(f"{HARI3[h.weekday()]} {tgl_pendek(h)}: <b>{koma(sum(per.values()) / 1000, 1)} t</b> ({rinci})")
        mo_b, per_b, biaya_b = self._produksi(d.replace(day=1), d)
        hari_b = {self._wib(m.date_finished) for m in mo_b}
        total_b = sum(per_b.values())
        teks = [f"<b>🏭 Produksi 7 hari terakhir</b> (ton)", "\n".join(baris) or "Tidak ada produksi.",
                f"<b>Bulan {BULAN[d.month]} sampai {tgl_pendek(d)}</b>\n{koma(total_b / 1000, 1)} ton dalam {len(hari_b)} hari produksi"
                + (f", rata-rata {koma(total_b / 1000 / len(hari_b), 1)} ton per hari" if hari_b else "")
                + (f"\nBiaya bahan {rp_singkat(biaya_b)} ({rp(biaya_b / total_b)} per kg)" if total_b else "")]
        teks.append("\n".join(f"• {self._nama(p)}: {self._jumlah(q, p.uom_id)}" for p, q in sorted(per_b.items(), key=lambda x: x[0].default_code or "")))
        return "\n\n".join(t for t in teks if t)

    def _pesan_stok(self):
        hari_ini = self._hari_ini()
        d = hari_ini - dt.timedelta(days=1)
        jadi, bahan = self._produk_bom()
        kirim = self._rata_harian(d, "kirim")
        pakai = self._rata_harian(d, "bahan")

        def baris(p, rata):
            cukup = p.qty_available / rata if rata else None
            tanda = "🔴" if cukup is not None and cukup < 2 else ("🟡" if cukup is not None and cukup < 4 else "🟢")
            s = f"{tanda} {self._nama(p)}: {self._jumlah(p.qty_available, p.uom_id)}"
            if cukup is not None:
                s += f", cukup {koma(cukup)} hari"
            po = self._po_datang(p) if p in bahan else None
            if po:
                s += f" (+{self._jumlah(po.product_qty - po.qty_received, p.uom_id)} tgl {tgl_pendek(self._wib(po.date_planned))})"
            return s

        return "\n\n".join([
            f"<b>📦 Stok per {tgl(hari_ini)}</b>",
            "<b>Produk jadi</b> (dibanding rata-rata kirim 7 hari)\n" + "\n".join(baris(p, kirim.get(p)) for p in jadi.sorted("default_code")),
            "<b>Bahan baku dan kemasan</b> (dibanding pemakaian 7 hari)\n" + "\n".join(baris(p, pakai.get(p)) for p in bahan.sorted("default_code")),
            "🟢 aman 4 hari atau lebih · 🟡 kurang dari 4 hari · 🔴 kurang dari 2 hari",
        ])

    def _pesan_lembur(self, d):
        awal = d.replace(day=1)
        absen = self._absen(awal, d)
        lembur = self._lembur(absen)
        hari = len({self._wib(a.check_in) for a in absen})
        if not lembur:
            return f"<b>⏱️ Lembur 1 sampai {tgl_pendek(d)}</b>\nTidak ada lembur."
        total_jam = sum(v[0] for v in lembur.values())
        total_rp = sum(v[1] for v in lembur.values())
        dept = "\n".join(f"• {e(k)}: {koma(v[0])} jam, {rp_singkat(v[1])}" for k, v in sorted(lembur.items(), key=lambda x: -x[1][0]))
        orang = defaultdict(float)
        for v in lembur.values():
            for k, j in v[2].items():
                orang[k] += j
        top = "\n".join(f"{i}. {e(k.name)} ({e(k.department_id.name)}): {koma(j)} jam"
                        for i, (k, j) in enumerate(sorted(orang.items(), key=lambda x: -x[1])[:5], 1))
        return "\n\n".join([
            f"<b>⏱️ Lembur 1 sampai {tgl_pendek(d)} {d.year}</b>\n{hari} hari kerja",
            f"<b>Per departemen</b>\n{dept}\nTotal <b>{koma(total_jam)} jam</b>, sekitar <b>{rp_singkat(total_rp)}</b>",
            f"<b>Lembur terbanyak</b>\n{top}",
            "Jam pertama di atas 8 jam dihitung 1,5x, jam berikutnya 2x upah per jam (upah sebulan dibagi 173). "
            "Rincian per orang ada di Odoo, menu Kehadiran.",
        ])

    def _pesan_piutang(self):
        inv, telat, hari_ini = self._piutang()
        if not inv:
            return "<b>💰 Piutang</b>\nTidak ada faktur yang belum dibayar."
        total = sum(inv.mapped("amount_residual"))
        belum = total - sum(telat.mapped("amount_residual"))
        umur = defaultdict(float)
        for m in telat:
            h = (hari_ini - m.invoice_date_due).days
            umur["1-30 hari" if h <= 30 else ("31-60 hari" if h <= 60 else "lebih dari 60 hari")] += m.amount_residual
        teks = [f"<b>💰 Piutang per {tgl(hari_ini)}</b>",
                f"Belum dibayar: <b>{rp_singkat(total)}</b> ({len(inv)} faktur)\n• Belum jatuh tempo: {rp_singkat(belum)}\n"
                + "\n".join(f"• Telat {k}: {rp_singkat(umur[k])}" for k in ("1-30 hari", "31-60 hari", "lebih dari 60 hari") if umur.get(k))]
        if telat:
            per = defaultdict(list)
            for m in telat:
                per[m.partner_id].append(m)
            daftar = []
            for p, ms in sorted(per.items(), key=lambda x: -sum(m.amount_residual for m in x[1])):
                lama = max((hari_ini - m.invoice_date_due).days for m in ms)
                daftar.append(f"• {e(p.name)}: {rp_singkat(sum(m.amount_residual for m in ms))}, {len(ms)} faktur, telat sampai {lama} hari")
            teks.append("<b>⚠️ Lewat jatuh tempo</b>\n" + "\n".join(daftar[:8]))
        else:
            teks.append("Tidak ada faktur yang lewat jatuh tempo.")
        return "\n\n".join(teks)
