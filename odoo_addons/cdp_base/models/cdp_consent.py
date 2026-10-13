from odoo import _, api, fields, models
from odoo.exceptions import UserError

PURPOSES = [
    ("care", "Chăm sóc / thông báo dịch vụ"),
    ("marketing", "Tiếp thị (email, ZNS, SMS)"),
    ("ads", "Quảng cáo (Facebook/TikTok/Google)"),
    ("analytics", "Phân tích hành vi"),
]
CHANNELS = [
    ("form", "Phiếu đăng ký"),
    ("website", "Website / cổng khách hàng"),
    ("email", "Email (hủy đăng ký)"),
    ("zalo", "Zalo"),
    ("phone", "Điện thoại"),
    ("in_person", "Trực tiếp"),
    ("import", "Nhập dữ liệu"),
    ("other", "Khác"),
]


class CdpConsent(models.Model):
    """Consent per purpose (Luật BVDLCN 91/2025/QH15). Every change is sent to CDP Core."""

    _name = "cdp.consent"
    _description = "CDP Consent"
    _order = "partner_id, purpose"

    partner_id = fields.Many2one("res.partner", required=True, ondelete="cascade", index=True)
    purpose = fields.Selection(PURPOSES, required=True)
    granted = fields.Boolean(default=False)
    channel = fields.Selection(CHANNELS, default="form", required=True)
    evidence = fields.Char(help="Mã phiếu, IP/thiết bị, đường dẫn ghi âm...")
    date = fields.Datetime(default=fields.Datetime.now, required=True)
    user_id = fields.Many2one("res.users", default=lambda self: self.env.user, readonly=True)

    _sql_constraints = [("partner_purpose_uniq", "unique(partner_id, purpose)", "Một quyết định cho mỗi mục đích.")]

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._cdp_emit()
        return records

    def write(self, vals):
        if "granted" in vals and "date" not in vals:
            vals = dict(vals, date=fields.Datetime.now())
        res = super().write(vals)
        if {"granted", "channel", "evidence"} & set(vals):
            self._cdp_emit()
        return res

    def _cdp_emit(self):
        outbox = self.env["cdp.outbox"]
        for rec in self:
            outbox._emit("consent.updated", rec.partner_id, rec, {
                "purpose": rec.purpose, "granted": rec.granted, "channel": rec.channel,
                "evidence_ref": rec.evidence, "decided_at": rec.date,
            })

    @api.model
    def _set(self, partner, purpose, granted, channel="form", evidence=None):
        """Create or update the decision of `partner` for `purpose`."""
        for p in partner:
            rec = self.sudo().search([("partner_id", "=", p.id), ("purpose", "=", purpose)], limit=1)
            if rec:
                if rec.granted != granted or rec.channel != channel:
                    rec.write({"granted": granted, "channel": channel, "evidence": evidence})
            else:
                self.sudo().create({"partner_id": p.id, "purpose": purpose, "granted": granted,
                                    "channel": channel, "evidence": evidence})


class MailBlacklist(models.Model):
    """Unsubscribing from emails withdraws marketing consent."""

    _inherit = "mail.blacklist"

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._cdp_withdraw_marketing()
        return records

    def _add(self, email, message=None):
        record = super()._add(email, message=message)
        record._cdp_withdraw_marketing()
        return record

    def _cdp_withdraw_marketing(self):
        if not self.env["cdp.outbox"]._cdp_enabled():
            return
        for rec in self.filtered("active"):
            partners = self.env["res.partner"].sudo().search([("email_normalized", "=", rec.email)])
            self.env["cdp.consent"]._set(partners, "marketing", False, channel="email",
                                         evidence=f"mail.blacklist,{rec.id}")


class ResPartner(models.Model):
    _inherit = "res.partner"

    cdp_consent_ids = fields.One2many("cdp.consent", "partner_id", string="Đồng ý xử lý dữ liệu")
    cdp_excluded = fields.Boolean(
        "Loại khỏi CDP", copy=False, readonly=True,
        help="Đã yêu cầu xóa dữ liệu: không gửi dữ liệu của khách này sang CDP nữa.")

    def action_cdp_request_erasure(self):
        """Right to erasure: CDP Core deletes the person's data and Odoo stops sending it."""
        if not self.env.user.has_group("cdp_base.group_cdp_manager"):
            raise UserError(_("Chỉ CDP Manager được yêu cầu xóa dữ liệu."))
        for partner in self:
            self.env["cdp.outbox"]._emit("privacy.erasure_requested", partner, partner,
                                         {"requested_by": self.env.user.login})
            partner.cdp_profile_ids.sudo().unlink()
            partner.with_context(cdp_no_sync=True).sudo().write({"cdp_excluded": True})
            partner.message_post(body=_("Đã gửi yêu cầu xóa dữ liệu cá nhân sang CDP; khách được loại khỏi CDP."))
        return True
