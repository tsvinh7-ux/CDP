"""Sales touchpoints. Only the fact and its context are sent (stage, team, UTM, expected
revenue) – never the lead description, notes or activity feedback (data minimisation)."""

from odoo import api, models

TRACKED_ACTIVITY_MODELS = ("res.partner", "crm.lead")


class CrmLead(models.Model):
    _inherit = "crm.lead"

    def _cdp_props(self):
        self.ensure_one()
        return {
            "lead_id": self.id,
            "type": self.type,
            "stage": self.stage_id.name,
            "team": self.team_id.name or None,
            "source": self.source_id.name or None,
            "medium": self.medium_id.name or None,
            "campaign": self.campaign_id.name or None,
            "expected_revenue": self.expected_revenue or 0,
        }

    def _cdp_emit(self, event_type, extra=None):
        outbox = self.env["cdp.outbox"]
        for lead in self:
            outbox._emit_contact(event_type, lead, lead.partner_id, lead.email_from, lead.mobile or lead.phone,
                                 dict(lead._cdp_props(), **(extra or {})))

    @api.model_create_multi
    def create(self, vals_list):
        leads = super().create(vals_list)
        leads._cdp_emit("lead.created")
        return leads

    def write(self, vals):
        before = {lead.id: lead.stage_id for lead in self} if "stage_id" in vals else {}
        res = super().write(vals)
        for lead in self.filtered(lambda l: l.id in before and before[l.id] != l.stage_id):
            if lead.stage_id.is_won:
                lead._cdp_emit("lead.won")
            else:
                lead._cdp_emit("lead.stage_changed", {"previous_stage": before[lead.id].name or None})
        return res

    def action_set_lost(self, **additional_values):
        res = super().action_set_lost(**additional_values)
        for lead in self:
            lead._cdp_emit("lead.lost", {"lost_reason": lead.lost_reason_id.name or None})
        return res


class MailActivity(models.Model):
    _inherit = "mail.activity"

    def _action_done(self, feedback=False, attachment_ids=None):
        done = [(a.res_model, a.res_id, a.activity_type_id.name, (a.summary or "")[:120])
                for a in self if a.res_model in TRACKED_ACTIVITY_MODELS and a.res_id]
        res = super()._action_done(feedback=feedback, attachment_ids=attachment_ids)
        outbox = self.env["cdp.outbox"]
        for model, res_id, activity_type, summary in done:
            record = self.env[model].sudo().browse(res_id).exists()
            if not record:
                continue
            props = {"activity_type": activity_type, "summary": summary, "res_model": model}
            if model == "res.partner":
                outbox._emit("sales.activity_done", record, record, props)
            else:
                outbox._emit_contact("sales.activity_done", record, record.partner_id, record.email_from,
                                     record.mobile or record.phone, dict(props, lead_id=record.id))
        return res
