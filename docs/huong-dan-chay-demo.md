# Hướng dẫn chạy demo CDP

Demo chạy **Odoo 17 Community thật** (eLearning, website_sale, mass_mailing) cùng các module CDP, nối với **CDP Core**. Một trình duyệt thật (Playwright) đóng vai khách hàng. Facebook và Zalo được thay bằng **server giả lập (MOCK)** vì máy demo không truy cập được API thật; mọi lệnh gửi đi đều được ghi lại để kiểm tra.

Triển khai production xem [`huong-dan-trien-khai.md`](huong-dan-trien-khai.md).

## 1. Thành phần

| Thành phần | Thư mục | Vai trò |
|-----------|--------|--------|
| CDP Core | `core/` | Nhận sự kiện (outbox Odoo, web SDK, webhook Zalo, Facebook Lead Ads); hợp nhất danh tính; traits (RFM, điểm lead, gợi ý khóa học, nha khoa); **hành trình điểm chạm và dự đoán** (điểm chạm tiếp theo, sản phẩm sẽ mua); phân khúc; consent và quyền dữ liệu; kênh kích hoạt (Facebook CAPI, Custom Audiences, Zalo ZNS); hàng đợi (PostgreSQL/Redpanda); xuất ClickHouse; đối soát; console có **dashboard** |
| `cdp_base` | `odoo_addons/` | Outbox, mapping, backfill, API nhận dữ liệu, `cdp.profile`, consent, yêu cầu xóa dữ liệu, tab **CDP 360** |
| `cdp_website` | `odoo_addons/` | SDK trên website (tôn trọng cookie bar), trang **Quyền riêng tư** cho khách |
| `cdp_education` | `odoo_addons/` | Sự kiện eLearning, giỏ hàng, VNPay/SePay, backfill lịch sử |
| `cdp_marketing_bridge` | `odoo_addons/` | Email marketing theo phân khúc, đồng bộ mailing list, quy tắc trigger (việc cho sales, mailing list, Marketing Automation) |
| `cdp_dental` | `odoo_addons/` | Quy tắc sự kiện cấu hình được cho model của Dental ERP |
| `cdp_crm` | `odoo_addons/` | Điểm chạm CRM: lead, chuyển giai đoạn, chốt/mất, cuộc gọi đã làm |
| `cdp_event` | `odoo_addons/` | Điểm chạm sự kiện/webinar: đăng ký, tham dự, hủy |
| Web SDK | `sdk/web/cdp.js` | page/track/identify, video YouTube/HTML5/Drive, fbp/fbc, cookie bar |
| Docker | `core/Dockerfile`, `docker-compose.yml` | PostgreSQL, ClickHouse, Redpanda (tùy chọn), API, consumer, worker |

## 2. Kịch bản demo (`scripts/demo/run_demo.sh`)

| Bước | Diễn ra | Kết quả |
|-----|--------|--------|
| 1 | Tạo tenant DMAG; cấu hình 3 kênh (Pixel, Audiences, Zalo OA) trỏ tới MOCK | |
| 2–3 | Cài Odoo + module; dữ liệu mẫu. **An Phúc**: chủ BS. Minh (đồng ý tiếp thị + quảng cáo), phụ tá Lê Thu (mua Cơ bản qua VNPay, học xong), BS. Hùng (SePay, học dở), lễ tân dùng chung SĐT với chủ. **Hòa Bình**: nhân sự đã học Nâng cao | |
| 4 | Outbox → Core → đẩy về Odoo và các kênh | BS. Minh vào **"Chủ PK: nhân sự đã học Cơ bản, chưa học Nâng cao"**, gợi ý *Implant Nâng cao* cho phòng khám → **ZNS mời chủ PK** (MOCK). Facebook nhận Purchase của khách có consent quảng cáo. Audience "Chủ phòng khám" chỉ gồm người đồng ý. Lễ tân **không** bị gộp với chủ |
| 5 | Quy tắc trong Odoo | "Quan tâm cao" → việc cho sales + mailing list; phân khúc chủ PK đồng bộ mailing list |
| 6 | Trình duyệt: khách ẩn danh từ Facebook xem khóa Nâng cao 3 lần → đăng nhập → *Add to Cart*; BS. Hòa đặt đơn chọn SePay | Lịch sử ẩn danh gộp vào hồ sơ BS. Minh; vào "Quan tâm cao" |
| 7 | Webhook Zalo OA (có ký): BS. Minh quan tâm OA, chia sẻ SĐT | Zalo ID gắn vào hồ sơ BS. Minh |
| 8 | Sau 1 phút | "Bỏ giỏ hàng" (Minh): ZNS bị **chặn bởi giới hạn tần suất** (đã nhận 1 tin tiếp thị trong ngày). "Chờ chuyển khoản" (Hòa): ZNS loại **care** vẫn gửi dù Hòa chưa đồng ý tiếp thị |
| 9 | Thêm điểm chạm từ app chuẩn của Odoo: lead từ form (chưa có liên hệ), cơ hội của BS. Minh chuyển giai đoạn, cuộc gọi tư vấn đã làm, BS. Minh tham dự webinar, nhận/mở/click email ưu đãi | Lead form được nhận diện qua email/SĐT. Hành trình của BS. Minh có thêm các kênh Sales/CRM, Sự kiện, Email; tab CDP 360 hiện hành trình nhúng |
| 10 | Tenant thứ hai: phòng khám dùng Dental ERP (sự kiện mô phỏng) | Hồ sơ bệnh nhân: đến hạn tái khám, kế hoạch điều trị chưa chốt, nguy cơ vắng mặt, trả góp quá hạn |
| 11 | Tenant **mô phỏng** `dmag_sim`: 2.000 người, 120 ngày hành vi giả lập (xem khóa, đăng ký, Zalo, email, webinar, sales, giỏ hàng, VNPay/SePay, học, lên Nâng cao, ảnh hưởng đồng nghiệp); huấn luyện mô hình | Dashboard và mô hình dự đoán có đủ dữ liệu để xem. Console ghi rõ **"Dữ liệu mô phỏng – không phải số liệu thật"** |

Ngưỡng 1 phút chỉ để demo; thực tế cấu hình theo tenant.

## 3. Cách chạy

Yêu cầu:
- PostgreSQL 16;
- Python 3.12;
- mã nguồn Odoo 17.0.

```bash
sudo -u postgres psql -c "CREATE ROLE odoo LOGIN SUPERUSER PASSWORD 'odoo';"
sudo -u postgres psql -c "CREATE ROLE cdp LOGIN PASSWORD 'cdp';"
sudo -u postgres createdb -O cdp cdp_core
python3.12 -m venv $RUNTIME/odoo-venv && $RUNTIME/odoo-venv/bin/pip install -r odoo17/requirements.txt
python3.12 -m venv $RUNTIME/core-venv && $RUNTIME/core-venv/bin/pip install -e "core[dev,kafka]" playwright
# odoo.conf: addons_path gồm .../CDP/odoo_addons; dbfilter = ^dmag$
RUNTIME=/đường/dẫn/runtime scripts/demo/run_demo.sh
```

Sau khi chạy:
- Odoo: <http://localhost:8069>
  - admin / admin;
  - tài khoản portal: minh@anphuc.vn / demo1234.
- Console: <http://localhost:8000/console?token=demo-admin&tenant=dmag>
- Các lệnh đã gửi tới MOCK: `docs/demo/mock-provider-calls.json`

## 4. Kiểm thử

```bash
cd core && pytest                                    # SQLite; 2 test hạ tầng được bỏ qua
CDP_TEST_DATABASE_URL=postgresql+psycopg://cdp:cdp@localhost/cdp_core_test \
CDP_TEST_CLICKHOUSE_URL=http://cdp:cdp@localhost:8123/cdp CDP_TEST_KAFKA=1 pytest   # PostgreSQL + ClickHouse + Redpanda thật
odoo-bin -c odoo.conf -d cdp_test --db-filter='^cdp_test$' \
  -i cdp_base,cdp_website,cdp_education,cdp_marketing_bridge,cdp_dental,cdp_crm,cdp_event \
  --test-enable --test-tags /cdp_base,/cdp_website,/cdp_education,/cdp_marketing_bridge,/cdp_dental,/cdp_crm,/cdp_event \
  --stop-after-init
```

## 5. Phần cần thông tin/tài khoản thật để hoàn tất

| Việc | Cần gì | Ai |
|-----|-------|----|
| Gửi thật lên Facebook | Pixel ID + access token Conversions API; System User token có quyền `ads_management` + ad account | DMAG (Business Manager) |
| Gửi ZNS thật | Zalo OA đã xác thực, App ID/secret, refresh token, template ZNS đã được duyệt | DMAG |
| Ghi danh chiến dịch Marketing Automation | Tên model/field của MA Bizapp. `cdp_marketing_bridge` đã có adapter cấu hình được, mặc định theo cấu trúc bản Enterprise | Bizapp |
| Sự kiện Dental ERP | Tên model/field của Dental ERP để khai báo quy tắc trong `cdp_dental` | Bizapp |
| Thanh toán | `code` của provider VNPay/SePay của Bizapp | Bizapp |
| Hợp đồng | Checklist [`dieu-khoan-hop-dong-bizapp.md`](dieu-khoan-hop-dong-bizapp.md) | DMAG + luật sư |
| AI | Mô hình dự đoán đã có (mục 3.9 bản đề xuất) và tự huấn luyện mỗi ngày. Số liệu trong demo tính trên dữ liệu mô phỏng nên **chưa nói lên độ chính xác thật**. Sau 1–2 tháng dữ liệu DMAG: xem bảng "Chất lượng mô hình", chỉ dùng để kích hoạt khi thắng baseline rõ ràng. Điểm lead vẫn tính theo quy tắc | DMAG + đội CDP |
| Facebook Lead Ads | App Facebook có quyền `leads_retrieval`, Page access token, đăng ký webhook `leadgen` trỏ tới `/v1/webhooks/facebook/dmag` | DMAG |

## 6. Ảnh chụp từ lần chạy demo

| Màn hình | Ảnh |
|---------|-----|
| Website: giỏ hàng của BS. Minh (bỏ dở) | ![](demo/01-website-gio-hang.png) |
| Odoo: tab **CDP 360** (phân khúc, điểm lead, gợi ý khóa, consent, timeline) | ![](demo/02-odoo-ho-so-cdp-360.png) |
| Odoo: trigger từ CDP và kết quả quy tắc | ![](demo/03-odoo-trigger.png) |
| Odoo: danh sách phân khúc | ![](demo/04-odoo-phan-khuc.png) |
| Odoo: lọc khách theo phân khúc (domain mà Marketing Automation dùng) | ![](demo/05-odoo-loc-theo-phan-khuc.png) |
| Odoo: phân khúc chủ PK, mailing list đồng bộ, quy tắc trigger | ![](demo/06-odoo-phan-khuc-trigger-rule.png) |
| Cổng khách hàng: Quyền riêng tư | ![](demo/07-portal-quyen-rieng-tu.png) |
| Console: tổng quan | ![](demo/08-console-tong-quan.png) |
| Console: tạo phân khúc + xem trước | ![](demo/09-console-tao-phan-khuc.png) |
| Console: kênh kích hoạt (đã gửi/bỏ qua) | ![](demo/10-console-kenh-kich-hoat.png) |
| Console: doanh thu theo nguồn | ![](demo/11-console-doanh-thu-theo-nguon.png) |
| Console: hồ sơ BS. Minh | ![](demo/12-console-ho-so.png) |
| Console: tenant phòng khám (Dental ERP) | ![](demo/13-console-nha-khoa.png) |
| Console: **dashboard** (tenant mô phỏng): KPI so với kỳ trước, khách hoạt động, doanh thu, phễu, điểm chạm theo giai đoạn/kênh, dự báo sản phẩm và điểm chạm, chất lượng mô hình so với baseline | ![](demo/14-console-dashboard.png) |
| Console: **hành trình** một khách (làn theo kênh, giai đoạn, vùng dự đoán) và **dự đoán** (khả năng mua 30 ngày, sản phẩm/dịch vụ sẽ mua kèm lý do, điểm chạm tiếp theo) | ![](demo/15-console-hanh-trinh-du-doan.png) |
| Odoo: tab **CDP 360** của BS. Minh với hành trình nhúng từ CDP Core | ![](demo/16-odoo-cdp-360-hanh-trinh.png) |
| Console: dashboard ở giao diện tối | ![](demo/17-console-dashboard-dark.png) |
