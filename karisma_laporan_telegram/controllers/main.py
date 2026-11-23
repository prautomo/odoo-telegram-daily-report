"""Webhook Telegram. Telegram mengirim header X-Telegram-Bot-Api-Secret-Token yang harus sama dengan rahasia
yang dibuat saat bot disambungkan. Permintaan lain ditolak 403."""
import hmac
import json
import logging

from odoo import http
from odoo.http import request

from ..models.telegram import JALUR_WEBHOOK, P_RAHASIA

_logger = logging.getLogger(__name__)


class TelegramWebhook(http.Controller):
    @http.route(JALUR_WEBHOOK, type="http", methods=["POST"], auth="public", csrf=False, save_session=False)
    def webhook(self, **_kw):
        rahasia = request.env["ir.config_parameter"].sudo().get_str(P_RAHASIA) or ""
        kiriman = request.httprequest.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not rahasia or not hmac.compare_digest(rahasia, kiriman):
            return request.make_response("forbidden", status=403)
        try:
            update = json.loads(request.httprequest.get_data() or b"{}")
            request.env["karisma.telegram.chat"].sudo()._tangani_update(update)
        except Exception:
            # Tetap 200 supaya Telegram tidak mengulang kiriman yang sama terus-menerus.
            _logger.exception("Webhook Telegram gagal diproses")
        return request.make_response("ok")
