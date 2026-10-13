from datetime import date, datetime

from odoo import api, fields, models, tools


class CdpMapping(models.Model):
    """Which models/fields are replicated to CDP Core (data contract §7).

    Only fields listed here leave Odoo. Free-text and clinical fields must
    not be added (data minimisation).
    """

    _name = "cdp.mapping"
    _description = "CDP Field Mapping"
    _order = "model_name"

    model_id = fields.Many2one("ir.model", required=True, ondelete="cascade")
    model_name = fields.Char("Model name", related="model_id.model", store=True, index=True)
    field_ids = fields.Many2many(
        "ir.model.fields", string="Fields", domain="[('model_id', '=', model_id), ('store', '=', True)]"
    )
    active = fields.Boolean(default=True)

    _sql_constraints = [("model_uniq", "unique(model_id)", "One mapping per model.")]

    @api.model
    @tools.ormcache("model_name")
    def _get_fields(self, model_name):
        mapping = self.sudo().search([("model_name", "=", model_name), ("active", "=", True)], limit=1)
        return tuple(mapping.field_ids.mapped("name"))

    @api.model_create_multi
    def create(self, vals_list):
        self.env.registry.clear_cache()
        return super().create(vals_list)

    def write(self, vals):
        self.env.registry.clear_cache()
        return super().write(vals)

    def unlink(self):
        self.env.registry.clear_cache()
        return super().unlink()

    def add_fields(self, names):
        """Used by other add-ons' data files to extend a mapping."""
        for mapping in self:
            flds = self.env["ir.model.fields"].search(
                [("model_id", "=", mapping.model_id.id), ("name", "in", list(names))]
            )
            mapping.field_ids = [(4, f.id) for f in flds]

    @api.model
    def _snapshot(self, record, field_names):
        out = {}
        for name in field_names:
            field = record._fields.get(name)
            if field is None:
                continue
            value = record[name]
            if field.type == "many2one":
                out[name] = value.id or None
            elif field.type in ("many2many", "one2many"):
                out[name] = value.ids
                out[name + "_names"] = value.mapped("display_name")
            elif isinstance(value, (datetime, date)):
                out[name] = value.isoformat()
            elif value is False and field.type not in ("boolean",):
                out[name] = None
            else:
                out[name] = value
        return out
