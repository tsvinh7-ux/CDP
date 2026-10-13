"""Admin CLI: `python -m cdp_core.cli init-db | create-tenant ...`."""

import argparse
import json

from .db import init_db, session_scope
from .jobs import create_tenant
from .models import Tenant


def main():
    parser = argparse.ArgumentParser(prog="cdp_core.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    ct = sub.add_parser("create-tenant")
    ct.add_argument("--id", required=True)
    ct.add_argument("--name", required=True)
    ct.add_argument("--secret", required=True)
    ct.add_argument("--write-key", required=True)
    ct.add_argument("--odoo-url")
    ct.add_argument("--settings", default="{}", help='JSON, e.g. {"abandon_minutes": 60}')
    args = parser.parse_args()

    init_db()
    if args.cmd == "create-tenant":
        with session_scope() as s:
            if s.get(Tenant, args.id):
                print(f"Tenant {args.id} already exists")
                return
            create_tenant(s, args.id, args.name, args.secret, args.write_key, args.odoo_url,
                          json.loads(args.settings))
        print(f"Tenant {args.id} created")


if __name__ == "__main__":
    main()
