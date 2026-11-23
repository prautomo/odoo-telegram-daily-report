"""Versi 1.3 menambah persetujuan penerima. Chat yang terdaftar sebelum versi ini sudah menerima laporan,
jadi disetujui otomatis supaya tidak terputus setelah upgrade."""


def migrate(cr, version):
    if version:
        cr.execute("UPDATE karisma_telegram_chat SET disetujui = TRUE WHERE disetujui IS NOT TRUE")
