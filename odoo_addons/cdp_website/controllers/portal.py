from odoo import _, http
from odoo.http import request

from odoo.addons.portal.controllers.portal import CustomerPortal

SELF_SERVICE_PURPOSES = ("marketing", "ads", "analytics")


class CdpPrivacyPortal(CustomerPortal):

    @http.route("/my/privacy", type="http", auth="user", website=True, methods=["GET", "POST"])
    def my_privacy(self, **post):
        partner = request.env.user.partner_id
        Consent = request.env["cdp.consent"].sudo()
        if request.httprequest.method == "POST":
            req = request.httprequest
            evidence = f"portal ip={req.remote_addr} ua={(req.user_agent.string or '')[:80]}"
            for purpose in SELF_SERVICE_PURPOSES:
                Consent._set(partner, purpose, post.get(purpose) == "on", channel="website", evidence=evidence)
            if post.get("erasure_request"):
                self._cdp_request_erasure(partner)
                return request.redirect("/my/privacy?erasure=1")
            return request.redirect("/my/privacy?saved=1")
        decisions = {c.purpose: c.granted for c in partner.sudo().cdp_consent_ids}
        values = self._prepare_portal_layout_values()
        values.update({
            "page_name": "cdp_privacy",
            "decisions": decisions,
            "purposes": [p for p in request.env["cdp.consent"]._fields["purpose"].selection
                         if p[0] in SELF_SERVICE_PURPOSES],
            "saved": post.get("saved"),
            "erasure": post.get("erasure"),
        })
        return request.render("cdp_website.portal_my_privacy", values)

    def _cdp_request_erasure(self, partner):
        """Erasure must be verified by a person (invoices may have to be kept by law)."""
        env = request.env(su=True)
        manager_group = env.ref("cdp_base.group_cdp_manager")
        user = manager_group.users.filtered(lambda u: not u.share)[:1] or env.ref("base.user_admin")
        env["mail.activity"].create({
            "res_model_id": env["ir.model"]._get_id("res.partner"),
            "res_id": partner.id,
            "activity_type_id": env.ref("mail.mail_activity_data_todo").id,
            "summary": _("Yêu cầu xóa dữ liệu cá nhân"),
            "note": _("Khách gửi yêu cầu xóa dữ liệu từ cổng khách hàng. Xác minh rồi bấm "
                      "'Yêu cầu xóa dữ liệu cá nhân (CDP)' trên tab CDP 360."),
            "user_id": user.id,
        })
        partner.sudo().message_post(body=_("Khách gửi yêu cầu xóa dữ liệu cá nhân qua cổng khách hàng."))
