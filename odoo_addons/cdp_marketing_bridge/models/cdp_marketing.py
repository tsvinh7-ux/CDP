import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class CdpSegment(models.Model):
    _inherit = "cdp.segment"

    mailing_list_id = fields.Many2one("mailing.list", string="Danh sách email đồng bộ",
                                      help="Giữ danh sách này bằng đúng các thành viên (đã đồng ý tiếp thị) của phân khúc.")
    trigger_rule_ids = fields.One2many("cdp.trigger.rule", "segment_id", string="Hành động khi khách vào phân khúc")

    def _marketing_domain(self):
        """Partners of the segment who may receive marketing (consent checked by CDP)."""
        self.ensure_one()
        return [("cdp_segment_ids.key", "=", self.key), ("cdp_marketing_allowed", "=", True),
                ("email", "!=", False), ("is_blacklisted", "=", False)]

    def action_create_mailing(self):
        """Email campaign to this segment (Email Marketing on contacts, consent-filtered)."""
        self.ensure_one()
        mailing = self.env["mailing.mailing"].create({
            "subject": self.name,
            "mailing_model_id": self.env["ir.model"]._get_id("res.partner"),
            "mailing_domain": repr(self._marketing_domain()),
        })
        return {"type": "ir.actions.act_window", "res_model": "mailing.mailing", "res_id": mailing.id,
                "view_mode": "form", "target": "current"}

    def _sync_mailing_list(self):
        Contact = self.env["mailing.contact"].sudo()
        for seg in self.filtered("mailing_list_id"):
            mlist = seg.mailing_list_id.sudo()
            partners = self.env["res.partner"].sudo().search(seg._marketing_domain())
            wanted = {p.email_normalized: p for p in partners if p.email_normalized}
            current = {c.email_normalized: c for c in mlist.contact_ids}
            for email, partner in wanted.items():
                if email in current:
                    continue
                contact = Contact.search([("email_normalized", "=", email)], limit=1)
                if contact:
                    contact.write({"list_ids": [(4, mlist.id)]})
                else:
                    Contact.create({"name": partner.name, "email": partner.email, "list_ids": [(4, mlist.id)]})
            for email, contact in current.items():
                if email not in wanted:
                    contact.write({"list_ids": [(3, mlist.id)]})

    @api.model
    def _cron_sync_mailing_lists(self):
        self.search([("mailing_list_id", "!=", False)])._sync_mailing_list()


class CdpProfile(models.Model):
    _inherit = "cdp.profile"

    @api.model
    def _cdp_upsert(self, docs):
        # Segments the partners leave matter as much as the ones they enter.
        before = self.search([("partner_id", "in", [int(d["partner_id"]) for d in docs])])
        keys = set(before.segment_ids.mapped("key"))
        unknown = super()._cdp_upsert(docs)
        keys |= {k for d in docs for k in d.get("segments") or []}
        if keys:
            self.env["cdp.segment"].search([("key", "in", list(keys)), ("mailing_list_id", "!=", False)])._sync_mailing_list()
        return unknown


class CdpTriggerRule(models.Model):
    """What Odoo does when CDP reports that a partner entered a segment."""

    _name = "cdp.trigger.rule"
    _description = "CDP Trigger Rule"
    _order = "segment_id, sequence"

    sequence = fields.Integer(default=10)
    segment_id = fields.Many2one("cdp.segment", required=True, ondelete="cascade")
    active = fields.Boolean(default=True)
    action = fields.Selection([
        ("activity", "Tạo việc cho sales"),
        ("mailing_list", "Thêm vào danh sách email"),
        ("ma_campaign", "Ghi danh chiến dịch Marketing Automation"),
    ], required=True, default="activity")
    user_id = fields.Many2one("res.users", string="Giao cho",
                              help="Để trống: nhân viên phụ trách khách hàng, nếu không có thì quản trị viên.")
    activity_summary = fields.Char("Tiêu đề việc cần làm")
    mailing_list_id = fields.Many2one("mailing.list")
    only_role = fields.Char("Chỉ áp dụng vai trò", help="Ví dụ clinic_owner. Để trống: mọi khách.")
    # Marketing Automation (Bizapp, modelled on Odoo Enterprise): configurable names.
    ma_participant_model = fields.Char(default="marketing.participant")
    ma_campaign_field = fields.Char(default="campaign_id")
    ma_record_field = fields.Char(default="res_id")
    ma_campaign_id = fields.Integer("ID chiến dịch MA")

    @api.constrains("action", "mailing_list_id", "ma_campaign_id")
    def _check_target(self):
        for rule in self:
            if rule.action == "mailing_list" and not rule.mailing_list_id:
                raise UserError(_("Chọn danh sách email."))
            if rule.action == "ma_campaign" and not rule.ma_campaign_id:
                raise UserError(_("Nhập ID chiến dịch Marketing Automation."))

    def _apply(self, log):
        partner = log.partner_id
        traits = partner.cdp_profile_ids[:1].traits or {}
        results = []
        for rule in self:
            if rule.only_role and traits.get("job_role") != rule.only_role:
                results.append(f"{rule.action}: bỏ qua (vai trò)")
                continue
            if rule.action == "activity":
                results.append(rule._do_activity(log))
            elif rule.action == "mailing_list":
                results.append(rule._do_mailing_list(partner))
            else:
                results.append(rule._do_ma(partner))
        return "; ".join(results)

    def _do_activity(self, log):
        user = self.user_id or log.partner_id.user_id or self.env.ref("base.user_admin")
        act = self.env["mail.activity"].sudo().create({
            "res_model_id": self.env["ir.model"]._get_id("res.partner"),
            "res_id": log.partner_id.id,
            "activity_type_id": self.env.ref("mail.mail_activity_data_todo").id,
            "summary": self.activity_summary or f"CDP: {log.segment_id.name or log.key}",
            "note": log._note(),
            "user_id": user.id,
        })
        return f"mail.activity,{act.id}"

    def _do_mailing_list(self, partner):
        if not partner.cdp_marketing_allowed or not partner.email:
            return "mailing_list: bỏ qua (chưa đồng ý tiếp thị hoặc không có email)"
        Contact = self.env["mailing.contact"].sudo()
        contact = Contact.search([("email_normalized", "=", partner.email_normalized)], limit=1)
        if contact:
            contact.write({"list_ids": [(4, self.mailing_list_id.id)]})
        else:
            contact = Contact.create({"name": partner.name, "email": partner.email,
                                      "list_ids": [(4, self.mailing_list_id.id)]})
        return f"mailing.contact,{contact.id}"

    def _do_ma(self, partner):
        model = self.ma_participant_model
        if model not in self.env:
            return f"ma_campaign: chưa cài model {model}"
        Participant = self.env[model].sudo()
        missing = [f for f in (self.ma_campaign_field, self.ma_record_field) if f not in Participant._fields]
        if missing:
            return f"ma_campaign: thiếu field {missing} trên {model}"
        exists = Participant.search_count([(self.ma_campaign_field, "=", self.ma_campaign_id),
                                           (self.ma_record_field, "=", partner.id)])
        if exists:
            return "ma_campaign: đã có trong chiến dịch"
        part = Participant.create({self.ma_campaign_field: self.ma_campaign_id, self.ma_record_field: partner.id})
        return f"{model},{part.id}"


class CdpTriggerLog(models.Model):
    _inherit = "cdp.trigger.log"

    def _dispatch(self):
        with_rules = self.filtered(lambda t: t.segment_id.trigger_rule_ids.filtered("active") and t.partner_id)
        for trig in with_rules:
            try:
                result = trig.segment_id.trigger_rule_ids.filtered("active")._apply(trig)
                trig.write({"state": "done", "result": result[:250]})
            except Exception as exc:  # one bad rule must not block the webhook
                _logger.exception("CDP trigger rule failed")
                trig.write({"state": "error", "result": str(exc)[:250]})
        # No rule configured: default behaviour (activity for the salesperson).
        return super(CdpTriggerLog, self - with_rules)._dispatch()
