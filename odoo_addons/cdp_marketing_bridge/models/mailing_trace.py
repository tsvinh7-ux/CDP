"""E-mail touchpoints from Odoo Email Marketing / Marketing Automation traces.

One event per trace and status (deterministic ids), so repeated opens/clicks of the
same e-mail count once and a trace re-written by Odoo never duplicates in CDP.
"""

from odoo import models

STATUS_EVENTS = {
    "sent": "marketing.email_sent", "pending": "marketing.email_sent",
    "open": "marketing.email_opened", "reply": "marketing.email_replied", "bounce": "marketing.email_bounced",
}


class MailingTrace(models.Model):
    _inherit = "mailing.trace"

    def write(self, vals):
        watch = "trace_status" in vals or "links_click_datetime" in vals
        before = {t.id: (t.trace_status, t.links_click_datetime) for t in self} if watch else {}
        res = super().write(vals)
        if watch:
            for trace in self.filtered(lambda t: t.trace_type == "mail"):
                status, clicked = before.get(trace.id, (None, None))
                if trace.trace_status != status and trace.trace_status in STATUS_EVENTS:
                    trace._cdp_emit(STATUS_EVENTS[trace.trace_status])
                if trace.links_click_datetime and not clicked:
                    trace._cdp_emit("marketing.email_clicked")
        return res

    def _cdp_emit(self, event_type):
        self.ensure_one()
        record = self.env[self.model].sudo().browse(self.res_id).exists() if self.model in self.env else None
        if record is not None and record and record._name == "res.partner":
            partner = record
        else:
            partner = record.partner_id if record and "partner_id" in record._fields else None
        mailing = self.mass_mailing_id
        outbox = self.env["cdp.outbox"]
        outbox._emit_contact(event_type, self, partner, self.email, None, {
            "mailing_id": mailing.id or None,
            "campaign": mailing.subject or mailing.name or None,
            "utm_campaign": self.campaign_id.name or None,
            "recipient_model": self.model,
        }, event_id=outbox._history_event_id(self, event_type))
