from odoo import models
from odoo.http import request


class Website(models.Model):
    _inherit = "website"

    def _cdp_page_context(self, main_object):
        ctx = super()._cdp_page_context(main_object)
        if main_object._name == "slide.channel":
            ctx.update(type="course", course_id=main_object.id, course_name=main_object.name,
                       course_tags=main_object.tag_ids.mapped("name"))
        elif main_object._name == "slide.slide":
            channel = main_object.channel_id
            ctx.update(type="lesson", course_id=channel.id, course_name=channel.name,
                       lesson_id=main_object.id, lesson_name=main_object.name,
                       video_source=main_object.video_source_type if "video_source_type" in main_object._fields else None)
        return ctx

    def _cdp_request_context(self):
        ctx = super()._cdp_request_context()
        order_id = request.session.get("sale_order_id")
        if order_id:
            order = self.env["sale.order"].sudo().browse(order_id).exists()
            if order and order.state == "draft":
                ctx.update(cartOrderId=order.id, cartAmount=order.amount_total)
        return ctx
