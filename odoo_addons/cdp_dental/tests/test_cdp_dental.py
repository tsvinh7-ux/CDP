from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestDentalRules(TransactionCase):
    """Uses crm-free core models as stand-ins for Dental ERP models (res.partner.bank-like shape)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param("cdp.enabled", "1")
        cls.patient = cls.env["res.partner"].create({"name": "Bệnh nhân A", "mobile": "0903000111"})
        # Stand-in "appointment" model: res.partner.category is not linked to a partner, so we use
        # res.partner itself with field `type` as the status and `self` as the patient path.
        cls.model = cls.env["ir.model"]._get("res.partner")
        cls.field = cls.env["ir.model.fields"]._get("res.partner", "type")

    def events(self, event_type):
        return self.env["cdp.outbox"].search([("event_type", "=", event_type)])

    def test_rule_creates_automation_and_fires_on_change(self):
        rule = self.env["cdp.event.rule"].create({
            "model_id": self.model.id, "event_type": "appointment.no_show",
            "trigger_field_id": self.field.id, "trigger_value": "other", "partner_path": "commercial_partner_id",
            "line_ids": [(0, 0, {"prop": "service_group", "field_path": "function"}),
                         (0, 0, {"prop": "scheduled_at", "field_path": "create_date"})],
        })
        self.assertTrue(rule.automation_id.action_server_ids)
        self.patient.write({"function": "scaling"})
        self.assertFalse(self.events("appointment.no_show"))
        self.patient.write({"type": "other"})
        ev = self.events("appointment.no_show")
        self.assertEqual(len(ev), 1)
        props = ev.payload["properties"]
        self.assertEqual(props["service_group"], "scaling")
        self.assertEqual(props["appointment_id"], self.patient.id)
        self.assertTrue(props["scheduled_at"].endswith("Z"))
        self.assertEqual(ev.payload["subject"]["identifiers"]["phone"], "+84903000111")
        # Unchanged value: no new event.
        self.patient.write({"name": "Bệnh nhân A."})
        self.assertEqual(len(self.events("appointment.no_show")), 1)

    def test_clinical_fields_are_refused(self):
        with self.assertRaises(ValidationError):
            self.env["cdp.event.rule"].create({
                "model_id": self.model.id, "event_type": "procedure.completed", "partner_path": "id",
                "line_ids": [(0, 0, {"prop": "diagnosis", "field_path": "comment"})],
            })

    def test_deactivating_rule_disables_automation(self):
        rule = self.env["cdp.event.rule"].create({"model_id": self.model.id, "event_type": "appointment.booked",
                                                  "partner_path": "commercial_partner_id"})
        rule.active = False
        self.assertFalse(rule.automation_id.active)
