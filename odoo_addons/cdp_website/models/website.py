import json

from markupsafe import Markup

from odoo import models
from odoo.http import request

from odoo.addons.cdp_base.models.tools import make_user_token


class Website(models.Model):
    _inherit = "website"

    def _cdp_page_context(self, main_object):
        """Describe the current page for the SDK. Extended by vertical add-ons."""
        return {}

    def _cdp_tracking_config(self, main_object=None):
        icp = self.env["ir.config_parameter"].sudo()
        if icp.get_param("cdp.enabled") not in ("1", "True", "true"):
            return None
        endpoint = icp.get_param("cdp.public_url") or icp.get_param("cdp.core_url")
        write_key = icp.get_param("cdp.write_key")
        if not (endpoint and write_key):
            return None
        cfg = {"endpoint": endpoint.rstrip("/"), "writeKey": write_key}
        user = self.env.user
        secret = icp.get_param("cdp.secret")
        if secret and not user._is_public():
            cfg["userToken"] = make_user_token(secret, user.partner_id.id)
        page = self._cdp_page_context(main_object) if main_object else {}
        if page:
            cfg["page"] = page
        if request:
            cfg.update(self._cdp_request_context())
        return cfg

    def _cdp_request_context(self):
        return {}

    def _cdp_tracking_snippet(self, main_object=None):
        cfg = self._cdp_tracking_config(main_object)
        if not cfg:
            return ""
        # Escape "<" so names such as "</script>" cannot break out of the tag.
        data = json.dumps(cfg, ensure_ascii=False).replace("<", "\\u003c")
        return Markup(
            '<script>window.CDP_CONFIG = {};</script>\n'
            '<script async="async" src="{}/sdk/cdp.js"></script>'
        ).format(Markup(data), cfg["endpoint"])
