from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestEducationEvents(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("cdp.enabled", "1")
        icp.set_param("cdp.tenant_id", "dmag")
        group = cls.env["slide.channel.tag.group"].create({"name": "Cấp độ"})
        cls.tag_basic = cls.env["slide.channel.tag"].create({"name": "Cơ bản", "group_id": group.id})
        cls.channel = cls.env["slide.channel"].create({
            "name": "Implant Cơ bản", "enroll": "public", "tag_ids": [(6, 0, cls.tag_basic.ids)],
            "is_published": True,
        })
        cls.slides = cls.env["slide.slide"].create([
            {"name": f"Bài {i}", "channel_id": cls.channel.id, "slide_category": "article",
             "html_content": "<p>x</p>", "is_published": True}
            for i in (1, 2)
        ])
        cls.partner = cls.env["res.partner"].create({"name": "Lê Thu", "dental_role": "assistant",
                                                     "email": "thu@anphuc.vn"})

    def events(self, event_type):
        return self.env["cdp.outbox"].search([("event_type", "=", event_type)])

    def test_enrolment_progress_completion(self):
        self.channel._action_add_members(self.partner)
        enrolled = self.events("course.enrolled")
        self.assertEqual(len(enrolled), 1)
        props = enrolled.payload["properties"]
        self.assertEqual(props["course_tags"], ["Cơ bản"])
        self.assertEqual(enrolled.payload["subject"]["id"], self.partner.id)

        SlidePartner = self.env["slide.slide.partner"]
        SlidePartner.create({"slide_id": self.slides[0].id, "partner_id": self.partner.id,
                             "channel_id": self.channel.id, "completed": True})
        self.assertEqual(len(self.events("lesson.completed")), 1)
        self.assertEqual(self.events("course.progressed").payload["properties"]["completion"], 50)

        SlidePartner.create({"slide_id": self.slides[1].id, "partner_id": self.partner.id,
                             "channel_id": self.channel.id, "completed": True})
        self.assertEqual(len(self.events("course.completed")), 1)

    def test_payment_events_carry_provider(self):
        product = self.env["product.product"].create({"name": "Implant Nâng cao", "list_price": 4500000})
        order = self.env["sale.order"].create({
            "partner_id": self.partner.id,
            "order_line": [(0, 0, {"product_id": product.id, "product_uom_qty": 1})],
        })
        provider = self.env["payment.provider"].create({"name": "SePay chuyển khoản", "code": "none"})
        method = self.env.ref("payment.payment_method_unknown")
        tx = self.env["payment.transaction"].create({
            "provider_id": provider.id, "payment_method_id": method.id, "amount": order.amount_total,
            "currency_id": order.currency_id.id, "partner_id": self.partner.id,
            "sale_order_ids": [(6, 0, order.ids)],
        })
        placed = self.events("order.placed")
        self.assertEqual(placed.payload["properties"]["provider"], "sepay")
        tx._set_done()
        paid = self.events("order.paid")
        self.assertEqual(len(paid), 1)
        self.assertEqual(paid.payload["properties"]["amount"], order.amount_total)
        tx._set_done()  # already done: no duplicate event
        self.assertEqual(len(self.events("order.paid")), 1)

    def test_backfill_history_is_deterministic(self):
        self.channel._action_add_members(self.partner)
        self.env["cdp.outbox"].search([]).unlink()
        Outbox = self.env["cdp.outbox"].with_context(cdp_no_commit=True, cdp_no_retrigger=True)
        Outbox.action_backfill()
        Outbox._cron_backfill()
        enrolled = self.events("course.enrolled").filtered(lambda r: r.payload["subject"]["id"] == self.partner.id)
        self.assertEqual(len(enrolled), 1)
        self.assertTrue(self.events("entity.upserted").filtered(lambda r: r.res_id == self.partner.id))
        first_id = enrolled.event_id
        Outbox.action_backfill()
        Outbox._cron_backfill()
        again = self.events("course.enrolled").filtered(lambda r: r.payload["subject"]["id"] == self.partner.id)
        # Same deterministic event_id: CDP Core drops the duplicate.
        self.assertEqual(set(again.mapped("event_id")), {first_id})
