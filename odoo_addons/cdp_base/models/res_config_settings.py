from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    cdp_enabled = fields.Boolean("CDP sync enabled", config_parameter="cdp.enabled")
    cdp_core_url = fields.Char("CDP Core URL", config_parameter="cdp.core_url",
                               help="Server-to-server URL, e.g. https://cdp.dmag.vn")
    cdp_public_url = fields.Char("CDP public URL", config_parameter="cdp.public_url",
                                 help="URL browsers use for the tracking SDK (collector), e.g. https://t.dmag.vn")
    cdp_tenant_id = fields.Char("Tenant ID", config_parameter="cdp.tenant_id")
    cdp_secret = fields.Char("Shared secret", config_parameter="cdp.secret")
    cdp_write_key = fields.Char("Web write key", config_parameter="cdp.write_key")

    def action_cdp_send_now(self):
        self.env["cdp.outbox"].action_send_now()
