# DMAG CDP

Customer Data Platform của DMAG: dịch vụ CDP Core multi-tenant cùng các add-on Odoo 17 cho website/eLearning của DMAG và Dental ERP.

## Tài liệu

| Tài liệu | Nội dung |
|---------|---------|
| [docs/de-xuat-kien-truc-cdp.md](docs/de-xuat-kien-truc-cdp.md) | Đề xuất kiến trúc, nhật ký quyết định, lộ trình |
| [docs/data-contract-v1.md](docs/data-contract-v1.md) | Hợp đồng dữ liệu Odoo ↔ CDP Core (outbox, API, danh mục sự kiện) |
| [docs/dieu-khoan-hop-dong-bizapp.md](docs/dieu-khoan-hop-dong-bizapp.md) | Checklist điều khoản hợp đồng với Bizapp (sở hữu trí tuệ, giấy phép, dữ liệu) |
| [docs/huong-dan-chay-demo.md](docs/huong-dan-chay-demo.md) | Chạy demo end-to-end, kịch bản, giới hạn bản khung |

## Cấu trúc

```
core/            CDP Core (FastAPI + SQLAlchemy/PostgreSQL): ingest, identity, traits, segments, activation, console
odoo_addons/
  cdp_base/      Outbox, mapping, API nhận dữ liệu từ Core, cdp.profile/segment/trigger, tab CDP 360
  cdp_website/   Gắn SDK tracking vào website Odoo
  cdp_education/ Sự kiện eLearning, giỏ hàng, thanh toán VNPay/SePay
sdk/web/         JS SDK tracking (page/track/identify, video YouTube/NAS/Drive)
scripts/demo/    Kịch bản demo end-to-end
docs/            Tài liệu thiết kế, ảnh demo
```
