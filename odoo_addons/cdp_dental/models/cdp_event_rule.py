"""Configurable CDP events for Dental ERP models (data contract §5.3).

Dental ERP is built by Bizapp; instead of hard-coding its model names, an
admin declares e.g. "when `dental.appointment`.state becomes `no_show`, send
`appointment.no_show` for `patient_id.partner_id` with scheduled_at=`start`".
Each rule materialises as a standard Automation Rule (base_automation), so it
fires on every write path (UI, import, API) and stays visible to admins.
"""

from datetime import date, datetime

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

EVENT_TYPES = [
    ("appointment.booked", "Đặt lịch hẹn"),
    ("appointment.rescheduled", "Dời lịch hẹn"),
    ("appointment.cancelled", "Hủy lịch hẹn"),
    ("appointment.checked_in", "Đến khám (check-in)"),
    ("appointment.no_show", "Không đến"),
    ("treatment_plan.proposed", "Tư vấn kế hoạch điều trị"),
    ("treatment_plan.accepted", "Chấp nhận kế hoạch điều trị"),
    ("treatment_plan.declined", "Từ chối kế hoạch điều trị"),
    ("procedure.completed", "Hoàn thành thủ thuật"),
    ("installment.due", "Đến hạn trả góp"),
    ("installment.paid", "Thanh toán trả góp"),
    ("installment.overdue", "Trả góp quá hạn"),
]
# Properties each event should carry (data contract §5.3) – shown as guidance.
EXPECTED_PROPS = {
    "appointment": ["appointment_id", "scheduled_at", "service_group", "branch_id", "channel"],
    "treatment_plan": ["plan_id", "amount", "service_groups"],
    "procedure": ["service_group", "amount", "branch_id"],
    "installment": ["plan_id", "amount", "due_date"],
}
FORBIDDEN_PROPS = {"note", "notes", "diagnosis", "chart", "xray", "x_ray", "clinical_note", "description"}


class CdpEventRule(models.Model):
    _name = "cdp.event.rule"
    _description = "CDP Event Rule"
    _order = "model_id, event_type"

    name = fields.Char(compute="_compute_name", store=True)
    active = fields.Boolean(default=True)
    model_id = fields.Many2one("ir.model", required=True, ondelete="cascade", string="Model Dental ERP")
    model_name = fields.Char(related="model_id.model", string="Model name")
    event_type = fields.Selection(EVENT_TYPES, required=True)
    trigger_field_id = fields.Many2one(
        "ir.model.fields", string="Khi trường", domain="[('model_id', '=', model_id), ('store', '=', True)]",
        help="Để trống: phát khi bản ghi được tạo.")
    trigger_value = fields.Char("Đổi thành giá trị", help="Ví dụ: no_show, done, accepted")
    partner_path = fields.Char("Đường dẫn tới khách hàng", required=True, default="partner_id",
                               help="Ví dụ: partner_id hoặc patient_id.partner_id")
    line_ids = fields.One2many("cdp.event.rule.line", "rule_id", string="Thuộc tính gửi đi")
    automation_id = fields.Many2one("base.automation", readonly=True, ondelete="set null")

    @api.depends("model_id", "event_type", "trigger_field_id", "trigger_value")
    def _compute_name(self):
        for rule in self:
            cond = (f"{rule.trigger_field_id.name} = {rule.trigger_value}" if rule.trigger_field_id
                    else _("khi tạo"))
            rule.name = f"{rule.event_type or ''} ← {rule.model_id.model or ''} ({cond})"

    @api.constrains("line_ids")
    def _check_no_clinical_data(self):
        for rule in self:
            bad = [l.prop for l in rule.line_ids if l.prop.lower() in FORBIDDEN_PROPS
                   or any(part in FORBIDDEN_PROPS for part in (l.field_path or "").lower().split("."))]
            if bad:
                raise ValidationError(_("Không gửi dữ liệu lâm sàng sang CDP (tối thiểu hóa dữ liệu): %s", ", ".join(bad)))

    # ------------------------------------------------------------------ automation rule

    @api.model_create_multi
    def create(self, vals_list):
        rules = super().create(vals_list)
        rules._sync_automation()
        return rules

    def write(self, vals):
        res = super().write(vals)
        if {"model_id", "trigger_field_id", "trigger_value", "active"} & set(vals):
            self._sync_automation()
        return res

    def unlink(self):
        self.automation_id.unlink()
        return super().unlink()

    def _domains(self):
        self.ensure_one()
        if not self.trigger_field_id:
            return "[]", "[]", []
        name, value = self.trigger_field_id.name, self._typed_value()
        return repr([(name, "!=", value)]), repr([(name, "=", value)]), [self.trigger_field_id.id]

    def _typed_value(self):
        ftype = self.trigger_field_id.ttype
        value = self.trigger_value
        if ftype in ("integer", "many2one"):
            return int(value)
        if ftype == "boolean":
            return value.lower() in ("1", "true", "yes")
        return value

    def _sync_automation(self):
        Automation = self.env["base.automation"].sudo()
        for rule in self:
            pre, post, field_ids = rule._domains()
            vals = {
                "name": f"CDP: {rule.name}",
                "model_id": rule.model_id.id,
                "trigger": "on_create_or_write" if rule.trigger_field_id else "on_create",
                "filter_pre_domain": pre if rule.trigger_field_id else False,
                "filter_domain": post,
                "trigger_field_ids": [(6, 0, field_ids)],
                "active": rule.active,
            }
            if rule.automation_id:
                rule.automation_id.write(vals)
                rule.automation_id.action_server_ids.write({"model_id": rule.model_id.id})
            else:
                automation = Automation.create(vals)
                self.env["ir.actions.server"].sudo().create({
                    "name": f"CDP: {rule.event_type}",
                    "model_id": rule.model_id.id,
                    "state": "code",
                    "usage": "base_automation",
                    "base_automation_id": automation.id,
                    "code": f"env['cdp.event.rule'].browse({rule.id})._fire(records)",
                })
                rule.automation_id = automation

    # ------------------------------------------------------------------ firing

    def _fire(self, records):
        self.ensure_one()
        outbox = self.env["cdp.outbox"]
        for record in records:
            partner = record.sudo().mapped(self.partner_path)[:1] if self.partner_path else None
            if not partner or partner._name != "res.partner":
                continue
            props = {"record_id": record.id}
            for line in self.line_ids:
                props[line.prop] = _value(record, line.field_path)
            prefix = self.event_type.split(".")[0]
            id_key = {"appointment": "appointment_id", "treatment_plan": "plan_id"}.get(prefix)
            if id_key and id_key not in props:
                props[id_key] = record.id
            outbox._emit(self.event_type, partner, record, props)


class CdpEventRuleLine(models.Model):
    _name = "cdp.event.rule.line"
    _description = "CDP Event Rule Property"

    rule_id = fields.Many2one("cdp.event.rule", required=True, ondelete="cascade")
    prop = fields.Char("Thuộc tính CDP", required=True, help="Ví dụ: scheduled_at, service_group, amount, branch_id")
    field_path = fields.Char("Lấy từ trường", required=True, help="Ví dụ: start, service_id.cdp_group, amount_total")


def _value(record, path):
    value = record.sudo()
    for part in path.split("."):
        if not value:
            return None
        value = value[part]
    if isinstance(value, models.BaseModel):
        if not value:
            return None
        return value.id if len(value) == 1 else value.ids
    if isinstance(value, datetime):
        return value.isoformat() + "Z"
    if isinstance(value, date):
        return value.isoformat()
    return value if value is not False else None
