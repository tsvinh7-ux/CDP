import hashlib
import hmac
import time
from urllib.parse import urlencode

from markupsafe import Markup

from odoo import api, fields, models

from .cdp_profile import RFM_LABELS

JOURNEY_LINK_TTL = 3600


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
    # Journey + predictions rendered by CDP Core, embedded with a short-lived signed link.
    cdp_journey_html = fields.Html(compute="_compute_cdp_journey_html", sanitize=False)

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

    def _cdp_journey_url(self):
        """Signed /embed/journey link: HMAC(secret, "tenant.partner_id.exp"), valid one hour."""
        self.ensure_one()
        icp = self.env["ir.config_parameter"].sudo()
        base = (icp.get_param("cdp.public_url") or icp.get_param("cdp.core_url") or "").rstrip("/")
        tenant, secret = icp.get_param("cdp.tenant_id") or "", icp.get_param("cdp.secret") or ""
        if not (base and tenant and secret):
            return False
        exp = int(time.time()) + JOURNEY_LINK_TTL
        sig = hmac.new(secret.encode(), f"{tenant}.{self.id}.{exp}".encode(), hashlib.sha256).hexdigest()
        return f"{base}/embed/journey?" + urlencode({"tenant": tenant, "partner_id": self.id, "exp": exp, "sig": sig})

    def _compute_cdp_journey_html(self):
        for partner in self:
            url = partner.id and partner.cdp_profile_ids[:1].kind == "person" and partner._cdp_journey_url()
            partner.cdp_journey_html = Markup(
                '<iframe src="{}" title="Hành trình khách hàng" loading="lazy" '
                'style="width:100%;height:620px;border:0;border-radius:8px"></iframe>').format(url) if url else False

    def _search_cdp_segment_ids(self, operator, value):
        return [("cdp_profile_ids.segment_ids", operator, value)]

    cdp_marketing_allowed = fields.Boolean("CDP: được phép tiếp thị", compute="_compute_cdp_consent_flags",
                                           search="_search_cdp_marketing_allowed")
    cdp_lead_score = fields.Integer("CDP: điểm lead", compute="_compute_cdp_consent_flags",
                                    search="_search_cdp_lead_score")
    cdp_next_best_course = fields.Char("CDP: khóa gợi ý", compute="_compute_cdp_consent_flags")

    @api.depends("cdp_profile_ids.consent_marketing", "cdp_profile_ids.lead_score", "cdp_profile_ids.next_best_course")
    def _compute_cdp_consent_flags(self):
        for partner in self:
            prof = partner.cdp_profile_ids[:1]
            partner.cdp_marketing_allowed = prof.consent_marketing
            partner.cdp_lead_score = prof.lead_score
            partner.cdp_next_best_course = prof.next_best_course

    def _search_cdp_marketing_allowed(self, operator, value):
        return [("cdp_profile_ids.consent_marketing", operator, value)]

    def _search_cdp_lead_score(self, operator, value):
        return [("cdp_profile_ids.lead_score", operator, value)]

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
