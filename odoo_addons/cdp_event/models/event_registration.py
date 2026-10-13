from odoo import api, models

STATE_EVENTS = {"open": "event.registered", "done": "event.attended", "cancel": "event.cancelled"}


class EventRegistration(models.Model):
    _inherit = "event.registration"

    def _cdp_emit(self, event_type):
        outbox = self.env["cdp.outbox"]
        for reg in self:
            event = reg.event_id
            outbox._emit_contact(event_type, reg, reg.partner_id, reg.email, reg.phone, {
                "registration_id": reg.id,
                "event_id": event.id,
                "event_name": event.name,
                "event_type": event.event_type_id.name or None,
                "date_begin": event.date_begin,
                "online": bool(getattr(event, "is_online", False)),
            }, event_id=outbox._history_event_id(reg, event_type))

    @api.model_create_multi
    def create(self, vals_list):
        regs = super().create(vals_list)
        regs.filtered(lambda r: r.state in ("open", "done"))._cdp_emit("event.registered")
        regs.filtered(lambda r: r.state == "done")._cdp_emit("event.attended")
        return regs

    def write(self, vals):
        before = {r.id: r.state for r in self} if "state" in vals else {}
        res = super().write(vals)
        for reg in self.filtered(lambda r: r.id in before and before[r.id] != r.state):
            event_type = STATE_EVENTS.get(reg.state)
            if event_type:
                if event_type == "event.attended" and before[reg.id] == "draft":
                    reg._cdp_emit("event.registered")
                reg._cdp_emit(event_type)
        return res
