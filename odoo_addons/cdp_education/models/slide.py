from odoo import api, models

PROGRESS_MILESTONES = (25, 50, 75)


def _course_props(channel):
    return {
        "course_id": channel.id,
        "course_name": channel.name,
        "course_tags": channel.tag_ids.mapped("name"),
    }


class SlideChannel(models.Model):
    _name = "slide.channel"
    _inherit = ["slide.channel", "cdp.sync.mixin"]


class SlideChannelPartner(models.Model):
    _inherit = "slide.channel.partner"

    def _cdp_emit_enrolled(self):
        outbox = self.env["cdp.outbox"]
        for member in self.filtered(lambda m: m.member_status != "invited"):
            channel = member.channel_id
            if member.partner_id == channel.user_id.partner_id:
                continue  # Odoo enrols the course's responsible (instructor) automatically
            props = _course_props(channel)
            props["enroll_type"] = {"payment": "paid", "invite": "invited"}.get(channel.enroll, "free")
            outbox._emit("course.enrolled", member.partner_id, channel, props)

    @api.model_create_multi
    def create(self, vals_list):
        members = super().create(vals_list)
        members._cdp_emit_enrolled()
        return members

    def write(self, vals):
        invited = self.filtered(lambda m: m.member_status == "invited") if "member_status" in vals else self.browse()
        res = super().write(vals)
        # Accepting an invitation is the actual enrolment.
        invited.filtered(lambda m: m.member_status != "invited")._cdp_emit_enrolled()
        return res

    def _recompute_completion(self):
        before = {m.id: m.completion for m in self}
        res = super()._recompute_completion()
        outbox = self.env["cdp.outbox"]
        for member in self:
            old, new = before.get(member.id, 0), member.completion
            if new <= old:
                continue
            props = _course_props(member.channel_id)
            if new >= 100:
                outbox._emit("course.completed", member.partner_id, member.channel_id, props)
            elif any(old < m <= new for m in PROGRESS_MILESTONES):
                outbox._emit("course.progressed", member.partner_id, member.channel_id,
                             dict(props, completion=new))
        return res


class SlideSlidePartner(models.Model):
    _inherit = "slide.slide.partner"

    def _cdp_emit_completed(self):
        outbox = self.env["cdp.outbox"]
        for rec in self:
            props = _course_props(rec.channel_id)
            props.update(lesson_id=rec.slide_id.id, lesson_name=rec.slide_id.name,
                         lesson_type=rec.slide_id.slide_category)
            outbox._emit("lesson.completed", rec.partner_id, rec.slide_id, props)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records.filtered("completed")._cdp_emit_completed()
        return records

    def write(self, vals):
        newly = self.filtered(lambda r: not r.completed) if vals.get("completed") else self.browse()
        res = super().write(vals)
        newly._cdp_emit_completed()
        return res


class SlideChannelPartnerHistory(models.Model):
    _inherit = "slide.channel.partner"

    def _cdp_history_events(self):
        """Backfill: past enrolments and completions."""
        outbox = self.env["cdp.outbox"]
        for member in self.filtered(lambda m: m.member_status != "invited"):
            channel = member.channel_id
            if member.partner_id == channel.user_id.partner_id:
                continue
            props = _course_props(channel)
            outbox._emit("course.enrolled", member.partner_id, channel, props, occurred_at=member.create_date,
                         event_id=outbox._history_event_id(member, "course.enrolled"))
            if member.completion >= 100:
                outbox._emit("course.completed", member.partner_id, channel, props, occurred_at=member.write_date,
                             event_id=outbox._history_event_id(member, "course.completed"))
            elif member.completion:
                outbox._emit("course.progressed", member.partner_id, channel, dict(props, completion=member.completion),
                             occurred_at=member.write_date,
                             event_id=outbox._history_event_id(member, "course.progressed"))
