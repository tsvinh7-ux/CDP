from odoo import api, models


class CdpOutbox(models.Model):
    _inherit = "cdp.outbox"

    @api.model
    def _cdp_backfill_steps(self):
        return super()._cdp_backfill_steps() + [("history", "slide.channel.partner"), ("history", "sale.order")]
