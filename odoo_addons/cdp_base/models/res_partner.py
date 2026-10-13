from odoo import api, fields, models

from .cdp_profile import RFM_LABELS


class ResPartner(models.Model):
    _name = "res.partner"
    _inherit = ["res.partner", "cdp.sync.mixin"]

    cdp_profile_ids = fields.One2many("cdp.profile", "partner_id", string="CDP Profiles")
    cdp_profile_id = fields.Many2one("cdp.profile", compute="_compute_cdp_profile")
    # Searchable without storing anything on res.partner, so Marketing
    # Automation / CRM can filter with e.g. [('cdp_segment_ids.key', '=', 'clinic_owners')].
    cdp_segment_ids = fields.Many2many("cdp.segment", string="CDP Segments",
                                       compute="_compute_cdp_profile", search="_search_cdp_segment_ids")
    cdp_rfm_segment = fields.Selection(list(RFM_LABELS.items()), compute="_compute_cdp_profile", string="RFM")
    cdp_total_spent = fields.Float(compute="_compute_cdp_profile", search="_search_cdp_total_spent",
                                   string="CDP: Total spent")
    cdp_engagement_score = fields.Integer(compute="_compute_cdp_profile", string="Engagement")
    cdp_last_seen_at = fields.Datetime(compute="_compute_cdp_profile", string="Last seen")
    cdp_summary_html = fields.Html(compute="_compute_cdp_profile", sanitize=False)
    cdp_timeline_html = fields.Html(compute="_compute_cdp_profile", sanitize=False)

    @api.depends("cdp_profile_ids.segment_ids", "cdp_profile_ids.traits", "cdp_profile_ids.rfm_segment",
                 "cdp_profile_ids.total_spent", "cdp_profile_ids.engagement_score", "cdp_profile_ids.last_seen_at")
    def _compute_cdp_profile(self):
        for partner in self:
            prof = partner.cdp_profile_ids[:1]
            partner.cdp_profile_id = prof
            partner.cdp_segment_ids = prof.segment_ids
            partner.cdp_rfm_segment = prof.rfm_segment
            partner.cdp_total_spent = prof.total_spent
            partner.cdp_engagement_score = prof.engagement_score
            partner.cdp_last_seen_at = prof.last_seen_at
            partner.cdp_summary_html = prof.summary_html
            partner.cdp_timeline_html = prof.timeline_html

    def _search_cdp_segment_ids(self, operator, value):
        return [("cdp_profile_ids.segment_ids", operator, value)]

    def _search_cdp_total_spent(self, operator, value):
        return [("cdp_profile_ids.total_spent", operator, value)]


class MergePartnerWizard(models.TransientModel):
    _inherit = "base.partner.merge.automatic.wizard"

    def _merge(self, partner_ids, dst_partner=None, extra_checks=True):
        # Tell CDP Core which partner absorbed the merged ones (data contract §3.1).
        partners = self.env["res.partner"].browse(partner_ids).exists()
        if dst_partner is None and len(partners) > 1:
            ordered = self._get_ordered_partner(partners.ids)
            dst_partner = ordered[-1]
        if dst_partner:
            src = partners - dst_partner
            src._cdp_schedule("delete", merged_into={p.id: dst_partner.id for p in src})
        return super()._merge(partner_ids, dst_partner=dst_partner, extra_checks=extra_checks)
