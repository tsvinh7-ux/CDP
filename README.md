# DMAG CDP

Customer Data Platform của DMAG: dịch vụ **CDP Core** multi-tenant và các **add-on Odoo 17** cho website/eLearning của DMAG và Dental ERP.

## Tài liệu

| Tài liệu | Nội dung |
|---------|---------|
| [docs/de-xuat-kien-truc-cdp.md](docs/de-xuat-kien-truc-cdp.md) | Đề xuất kiến trúc, nhật ký quyết định, lộ trình |
| [docs/data-contract-v1.md](docs/data-contract-v1.md) | Hợp đồng dữ liệu Odoo ↔ CDP Core (outbox, API, danh mục sự kiện) |
| [docs/huong-dan-trien-khai.md](docs/huong-dan-trien-khai.md) | Triển khai production, cấu hình kênh (Facebook/Zalo), vận hành, bảo mật |
| [docs/huong-dan-chay-demo.md](docs/huong-dan-chay-demo.md) | Demo end-to-end, kiểm thử, ảnh chụp, việc cần thông tin thật |
| [docs/dieu-khoan-hop-dong-bizapp.md](docs/dieu-khoan-hop-dong-bizapp.md) | Checklist điều khoản hợp đồng với Bizapp |

## Cấu trúc

```
core/                    CDP Core (FastAPI, PostgreSQL, ClickHouse, Redpanda tùy chọn)
  cdp_core/              ingest, identity, traits, segments, consent/privacy, destinations, queue, analytics, console
odoo_addons/
  cdp_base/              outbox, mapping, backfill, API nhận dữ liệu, consent, quyền xóa dữ liệu, tab CDP 360
  cdp_website/           SDK tracking trên website, trang Quyền riêng tư (portal)
  cdp_education/         eLearning, giỏ hàng, VNPay/SePay (DMAG)
  cdp_marketing_bridge/  email marketing theo phân khúc, mailing list, quy tắc trigger, Marketing Automation
  cdp_dental/            quy tắc sự kiện cho model Dental ERP
sdk/web/cdp.js           JS SDK (page/track/identify, video, cookie bar, fbp/fbc)
scripts/demo/            demo end-to-end (Odoo thật + Playwright + mock Facebook/Zalo)
docker-compose.yml       stack CDP Core
```

## Bắt đầu nhanh

```bash
cp .env.example .env && docker compose up -d          # CDP Core: http://localhost:8000/console?token=...
cd core && pip install -e ".[dev]" && pytest           # test Core
```
