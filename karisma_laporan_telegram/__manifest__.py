{
    "name": "Daily Factory Report via Telegram (Laporan Pabrik lewat Telegram)",
    "version": "1.3",
    "category": "Productivity",
    "summary": "Daily morning summary of production, sales, attendance, overtime, stock, and receivables sent to Telegram, "
               "plus quick commands (/stok, /lembur, /piutang). Indonesian interface, admin approval for recipients.",
    "description": """
Ringkasan pagi (produksi, penjualan, kehadiran, lembur, stok, hal yang perlu perhatian) dikirim ke Telegram
setiap Senin sampai Sabtu pukul 06.30 WIB. Perintah dibalas lewat webhook. Semua angka diambil langsung dari
data Odoo, tanpa AI. Penerima baru harus disetujui admin. Dibuat Karisma Technologies.
""",
    "author": "Karisma Technologies",
    "website": "https://karismatech.com/odoo/",
    "maintainer": "Karisma Technologies",
    "depends": ["base_setup", "mrp", "sale_stock", "purchase_stock", "account", "hr_attendance"],
    "external_dependencies": {"python": ["requests"]},
    "data": [
        "security/ir.access.csv",
        "views/telegram_views.xml",
        "views/res_config_settings_views.xml",
        "data/cron.xml",
    ],
    "images": ["static/description/banner.png"],
    "installable": True,
    "application": False,
    "license": "LGPL-3",
}
