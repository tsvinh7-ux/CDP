import logging

from markupsafe import Markup

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

RFM_LABELS = {
    "prospect": "Chưa mua",
    "new_customer": "Khách mới",
    "one_time": "Mua 1 lần",
    "loyal": "Trung thành",
    "champion": "Khách VIP",
    "at_risk": "Có nguy cơ rời bỏ",
    "lost": "Đã rời bỏ",
}


class CdpSegment(models.Model):
    _name = "cdp.segment"
    _description = "CDP Segment"
    _order = "name"

    key = fields.Char(required=True, index=True, readonly=True)
    name = fields.Char(required=True, readonly=True)
    description = fields.Text(readonly=True)
    trigger_on_enter = fields.Boolean(readonly=True)
    active = fields.Boolean(default=True)
    member_count = fields.Integer("Members (CDP)", readonly=True)
    profile_ids = fields.Many2many("cdp.profile", string="Profiles", readonly=True)
    partner_count = fields.Integer(compute="_compute_partner_count")

    _sql_constraints = [("key_uniq", "unique(key)", "Segment key must be unique.")]

    def _compute_partner_count(self):
        for seg in self:
            seg.partner_count = len(seg.profile_ids)

    def action_view_partners(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.name,
            "res_model": "res.partner",
            "view_mode": "tree,form",
            # The same domain a Marketing Automation campaign uses to target this segment.
            "domain": [("cdp_segment_ids.key", "=", self.key)],
        }

    @api.model
    def _get_or_create(self, keys):
        existing = self.with_context(active_test=False).search([("key", "in", list(keys))])
        missing = set(keys) - set(existing.mapped("key"))
        if missing:
            existing |= self.create([{"key": k, "name": k} for k in missing])
        return existing


class CdpProfile(models.Model):
    """CDP data for a partner, written only by CDP Core.

    Kept out of res.partner on purpose (see proposal §3.2): bulk updates must
    not bump partner write_date (sync loop), fire partner automations or post
    chatter messages.
    """

    _name = "cdp.profile"
    _description = "CDP Profile"
    _rec_name = "partner_id"

    partner_id = fields.Many2one("res.partner", required=True, ondelete="cascade", index=True)
    cdp_id = fields.Char("CDP ID", index=True, readonly=True)
    kind = fields.Selection([("person", "Person"), ("account", "Clinic")], readonly=True)
    traits = fields.Json(readonly=True)
    segment_ids = fields.Many2many("cdp.segment", string="Segments", readonly=True)
    rfm_segment = fields.Selection(list(RFM_LABELS.items()), string="RFM", readonly=True)
    total_spent = fields.Float(readonly=True)
    orders_count = fields.Integer(readonly=True)
    engagement_score = fields.Integer(readonly=True)
    last_seen_at = fields.Datetime(readonly=True)
    core_updated_at = fields.Datetime(readonly=True)
    timeline_html = fields.Html(compute="_compute_html", sanitize=False)
    summary_html = fields.Html(compute="_compute_html", sanitize=False)

    _sql_constraints = [("partner_uniq", "unique(partner_id)", "One CDP profile per partner.")]

    @api.depends("traits")
    def _compute_html(self):
        for prof in self:
            t = prof.traits or {}
            rows = Markup("").join(
                Markup("<tr><td class='text-muted pe-3' style='white-space:nowrap'>{}</td><td>{}</td></tr>").format(
                    (item.get("at") or "")[:16].replace("T", " "), item.get("label") or ""
                )
                for item in t.get("timeline") or []
            )
            prof.timeline_html = Markup("<table class='table table-sm'>{}</table>").format(rows) if rows else False
            prof.summary_html = prof._summary_markup(t)

    def _summary_markup(self, t):
        items = []

        def add(label, value):
            if value not in (None, "", [], {}):
                items.append(Markup("<li><b>{}:</b> {}</li>").format(label, value))

        clinic = t.get("clinic") or {}
        if clinic:
            add(_("Phòng khám"), "{} – {} nhân sự, {} người đã học; đã học: {}".format(
                clinic.get("name") or "", clinic.get("staff_count"), clinic.get("learners_count"),
                ", ".join(clinic.get("staff_course_tags") or []) or "–"))
        add(_("Khóa đã ghi danh"), ", ".join(c.get("name") or str(c.get("id")) for c in t.get("courses_enrolled") or []))
        add(_("Đang quan tâm"), ", ".join(f"{c.get('name')} ({c.get('views')} lượt xem)" for c in t.get("hot_courses") or []))
        cart = t.get("open_cart")
        if cart:
            add(_("Giỏ hàng chưa thanh toán"), f"{int(cart.get('amount') or 0):,}đ".replace(",", "."))
        for o in t.get("awaiting_payment") or []:
            add(_("Đơn chờ thanh toán"), f"{o.get('order_name') or o.get('order_id')} – {int(o.get('amount') or 0):,}đ ({o.get('provider') or '?'})".replace(",", "."))
        touch = t.get("first_touch")
        if touch:
            add(_("Nguồn đầu tiên"), " / ".join(filter(None, [touch.get("source"), touch.get("medium"), touch.get("campaign")])))
        if t.get("stalled_courses"):
            add(_("Học dở"), ", ".join(f"{c.get('name')} ({c.get('completion')}%)" for c in t["stalled_courses"]))
        return Markup("<ul class='mb-0'>{}</ul>").format(Markup("").join(items)) if items else False

    # ------------------------------------------------------------------ API used by the controller

    @api.model
    def _cdp_upsert(self, docs):
        """Bulk upsert from CDP Core. Returns partner ids that do not exist."""
        partner_ids = [int(d["partner_id"]) for d in docs]
        partners = self.env["res.partner"].with_context(active_test=False).browse(partner_ids).exists()
        known = set(partners.ids)
        profiles = {p.partner_id.id: p for p in self.search([("partner_id", "in", list(known))])}
        all_keys = {k for d in docs for k in d.get("segments") or []}
        segments = {s.key: s for s in self.env["cdp.segment"]._get_or_create(all_keys)} if all_keys else {}
        unknown, to_create = [], []
        for doc in docs:
            pid = int(doc["partner_id"])
            if pid not in known:
                unknown.append(pid)
                continue
            t = doc.get("traits") or {}
            if t.get("erased"):
                # Erased in CDP (from Odoo or from the CDP console): drop the copy, stop syncing.
                profiles.pop(pid, self.browse()).unlink()
                self.env["res.partner"].browse(pid).with_context(cdp_no_sync=True).write({"cdp_excluded": True})
                continue
            vals = {
                "cdp_id": doc.get("cdp_id"),
                "kind": doc.get("kind") or "person",
                "traits": t,
                "segment_ids": [(6, 0, [segments[k].id for k in doc.get("segments") or []])],
                "rfm_segment": t.get("rfm_segment") if t.get("rfm_segment") in RFM_LABELS else False,
                "total_spent": t.get("total_spent") or 0,
                "orders_count": t.get("orders_count") or 0,
                "engagement_score": t.get("engagement_score") or 0,
                "last_seen_at": _to_dt(t.get("last_seen_at")),
                "core_updated_at": _to_dt(doc.get("updated_at")),
            }
            if pid in profiles:
                profiles[pid].write(vals)
            else:
                to_create.append(dict(vals, partner_id=pid))
        if to_create:
            self.create(to_create)
        return unknown


def _to_dt(value):
    if not value:
        return False
    return fields.Datetime.to_datetime(value[:19].replace("T", " "))


class CdpTriggerLog(models.Model):
    """Real-time trigger received from CDP Core (e.g. owner entered a segment)."""

    _name = "cdp.trigger.log"
    _description = "CDP Trigger"
    _order = "id desc"

    trigger_id = fields.Char(required=True, index=True, readonly=True)
    key = fields.Char("Segment key", required=True, readonly=True)
    segment_id = fields.Many2one("cdp.segment", readonly=True)
    partner_id = fields.Many2one("res.partner", readonly=True, ondelete="cascade")
    data = fields.Json(readonly=True)
    state = fields.Selection([("received", "Received"), ("done", "Dispatched"), ("error", "Error")],
                             default="received", readonly=True)
    result = fields.Char(readonly=True)

    _sql_constraints = [("trigger_uniq", "unique(trigger_id)", "Trigger already received.")]

    def _dispatch(self):
        """Default action: an activity for the partner's salesperson.

        `cdp_marketing_bridge` overrides this to enrol the partner in the
        matching Marketing Automation campaign.
        """
        activity_type = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
        for trig in self:
            if not trig.partner_id:
                trig.write({"state": "error", "result": "unknown partner"})
                continue
            user = trig.partner_id.user_id or self.env.ref("base.user_admin", raise_if_not_found=False) or self.env.user
            note = trig._note()
            act = self.env["mail.activity"].sudo().create({
                "res_model_id": self.env["ir.model"]._get_id("res.partner"),
                "res_id": trig.partner_id.id,
                "activity_type_id": activity_type.id if activity_type else False,
                "summary": f"CDP: {trig.segment_id.name or trig.key}",
                "note": note,
                "user_id": user.id,
            })
            trig.write({"state": "done", "result": f"mail.activity,{act.id}"})

    def _note(self):
        self.ensure_one()
        data = self.data or {}
        parts = [Markup("<p>Khách vừa vào phân khúc <b>{}</b>.</p>").format(data.get("segment_name") or self.key)]
        cart = data.get("open_cart")
        if cart:
            parts.append(Markup("<p>Giỏ hàng: {}đ, cập nhật {}</p>").format(
                f"{int(cart.get('amount') or 0):,}".replace(",", "."), (cart.get("updated_at") or "")[:16]))
        for c in data.get("hot_courses") or []:
            parts.append(Markup("<p>Quan tâm khóa <b>{}</b> ({} lượt xem)</p>").format(c.get("name"), c.get("views")))
        clinic = data.get("clinic") or {}
        if clinic:
            parts.append(Markup("<p>Phòng khám {}: {} người đã học ({}).</p>").format(
                clinic.get("name") or "", clinic.get("learners_count"),
                ", ".join(c.get("name") or "" for c in clinic.get("staff_courses") or [])))
        for o in data.get("awaiting_payment") or []:
            parts.append(Markup("<p>Đơn {} chờ thanh toán qua {}.</p>").format(
                o.get("order_name") or o.get("order_id"), o.get("provider") or "?"))
        return Markup("").join(parts)
