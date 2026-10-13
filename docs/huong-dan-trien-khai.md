# Hướng dẫn triển khai & vận hành CDP

Dành cho đội kỹ thuật DMAG và Bizapp khi đưa CDP lên production. Demo cục bộ xem [`huong-dan-chay-demo.md`](huong-dan-chay-demo.md).

## 1. Thành phần

```
Odoo (Bizapp host)                          CDP Core (DMAG host, docker compose)
 cdp_base ─ outbox ─────── HTTPS + HMAC ──►  cdp-api ──► ingest_queue / Redpanda ──► cdp-consumer
 cdp_website ─ SDK (trình duyệt) ────────►      │                                         │
 cdp_education / cdp_dental                     ▼                                         ▼
 cdp_marketing_bridge ◄── hồ sơ, phân khúc, trigger ── cdp-worker ──► Facebook CAPI/Audiences, Zalo ZNS
                                                PostgreSQL (hồ sơ)      ClickHouse (sự kiện, phân tích)
```

## 2. Triển khai CDP Core

```bash
git clone <repo> && cd CDP
cp .env.example .env        # đặt mật khẩu, CDP_ADMIN_TOKEN, CDP_MASTER_KEY (bắt buộc)
docker compose up -d        # PostgreSQL, ClickHouse, API, consumer, worker
# Lưu lượng lớn: CDP_QUEUE_BACKEND=kafka trong .env, rồi: docker compose --profile kafka up -d
```

- Đặt CDP Core sau reverse proxy có HTTPS (Nginx/Caddy), ví dụ:
  - `cdp.dmag.vn`: API cho Odoo và console;
  - `t.dmag.vn`: collector cho trình duyệt (`/v1/collect`, `/sdk/cdp.js`).
- **`CDP_MASTER_KEY`** mã hóa access token của Facebook/Zalo. Mất key thì phải nhập lại các token. Sao lưu key riêng, không để cùng chỗ với database.
- Tạo tenant:

  ```bash
  curl -X POST https://cdp.dmag.vn/v1/admin/tenants -H "X-Admin-Token: $TOKEN" -H "Content-Type: application/json" \
    -d '{"id":"dmag","name":"DMAG Academy","secret":"<chuỗi ngẫu nhiên 32+ ký tự>","write_key":"wk_dmag",
         "odoo_url":"https://erp.dmag.vn","settings":{"abandon_minutes":60,"transfer_minutes":30}}'
  ```

  Tenant phòng khám (Dental ERP): thêm `"vertical":"dental"` vào `settings`.
- **Sao lưu**: PostgreSQL (hồ sơ, consent, cấu hình) là bắt buộc. ClickHouse có thể dựng lại từ PostgreSQL trong khi PostgreSQL còn giữ sự kiện.

### Tham số tenant (`settings`)

| Khóa | Mặc định | Ý nghĩa |
|-----|---------|--------|
| `vertical` | `education` | `education` (DMAG) hoặc `dental` (phòng khám): bộ phân khúc mẫu |
| `abandon_minutes` / `transfer_minutes` | 60 / 30 | Ngưỡng bỏ giỏ hàng / chờ thanh toán |
| `consent_policy` | marketing, ads: `opt_in`; care, analytics: `opt_out` | Chính sách đồng ý theo mục đích |
| `frequency_cap` | `{"per_day": 1, "per_week": 3}` | Số tin tiếp thị tối đa cho mỗi khách, tính trên mọi kênh |
| `levels` | `["Cơ bản", "Nâng cao"]` | Thẻ cấp độ khóa học, dùng cho gợi ý khóa tiếp theo |
| `recall_months` | scaling 6, ortho_check 1, implant_check 12… | Chu kỳ tái khám (nha khoa) |
| `stall_days`, `hot_course_views` | 14, 3 | Ngưỡng "học dở" và "quan tâm cao" |

## 3. Cài đặt phía Odoo (Bizapp)

1. Chép `odoo_addons/` vào addons path.
2. Cài theo thứ tự:
   - `cdp_base`;
   - `cdp_website` (website, portal);
   - `cdp_education` (DMAG) hoặc `cdp_dental` (Dental ERP);
   - `cdp_marketing_bridge`;
   - `cdp_crm`, `cdp_event` (tự cài khi Odoo đã có CRM / Sự kiện).
3. Vào **Cài đặt → CDP**:
   - bật đồng bộ;
   - điền Core URL, Public URL, Tenant ID, Shared secret, Write key.
   - Public URL là địa chỉ trình duyệt dùng để gọi CDP Core: SDK tracking và hành trình nhúng trong tab CDP 360. Người dùng Odoo phải mở được địa chỉ này (cùng mạng/VPN với console nếu có giới hạn IP).
4. Bấm **Đồng bộ toàn bộ (backfill)**. Cron sẽ gửi dần khách hàng, rồi lịch sử đơn hàng và ghi danh. Chạy lại không tạo trùng.
5. **Mapping** (CDP → Cấu hình → Mapping): thêm các field custom của Bizapp cần dùng cho phân khúc. Không thêm ghi chú tự do hay dữ liệu lâm sàng.
6. Nếu Bizapp đã có field vai trò nghề nghiệp: map field đó (Core đọc `dental_role` hoặc `job_role`), hoặc đổi tên field trong `cdp_education`.
7. Thanh toán: `cdp_education` nhận diện VNPay/SePay theo `code` của provider. Bizapp cần cho biết `code` thật, hoặc đặt tên provider có chữ "VNPay"/"SePay".

### Dental ERP: cấu hình sự kiện (không cần code)

**CDP → Cấu hình → Sự kiện Dental ERP**: mỗi dòng là một quy tắc.

| Sự kiện | Model (ví dụ) | Khi trường | Giá trị | Khách hàng | Thuộc tính |
|--------|--------------|-----------|--------|-----------|-----------|
| `appointment.booked` | `dental.appointment` | *(khi tạo)* | | `patient_id.partner_id` | `scheduled_at` ← `start`, `service_group` ← `service_id.cdp_group`, `branch_id` ← `branch_id` |
| `appointment.no_show` | `dental.appointment` | `state` | `no_show` | `patient_id.partner_id` | |
| `treatment_plan.proposed` | `dental.treatment.plan` | `state` | `proposed` | `patient_id.partner_id` | `amount` ← `amount_total` |
| `procedure.completed` | `dental.procedure` | `state` | `done` | `patient_id.partner_id` | `service_group`, `amount` |
| `installment.overdue` | `dental.installment` | `state` | `overdue` | `plan_id.partner_id` | `plan_id`, `amount`, `due_date` |

Tên model và field ở trên chỉ là ví dụ; dùng tên thật của Dental ERP. Mỗi quy tắc tự sinh một Automation Rule. Thuộc tính tên `diagnosis`, `note`… bị chặn.

## 4. Kênh kích hoạt (console → Kênh kích hoạt)

| Kênh | Cấu hình | Bí mật | Ghi chú |
|-----|---------|-------|--------|
| **Facebook Conversions API** | `pixel_id`, `test_event_code` (khi thử) | `access_token` (Events Manager → Settings → Conversions API) | Gửi Purchase/InitiateCheckout/AddToCart/ViewContent. Chỉ khách có consent `ads`. Pixel trên web nên dùng cùng `event_id` để Meta loại trùng |
| **Facebook Custom Audiences** | `ad_account_id` (`act_…`), `segments` (danh sách khóa phân khúc) | `access_token` (System User, quyền `ads_management`) | Mỗi phân khúc là một audience. Chỉ gửi email/SĐT đã băm SHA-256; chỉ đồng bộ phần thay đổi; xóa khách khi rút consent hoặc yêu cầu xóa dữ liệu |
| **Zalo ZNS** | `app_id`, `oa_id`, `templates` {khóa phân khúc → `template_id`, `purpose`, `data`} | `secret_key` (App), `refresh_token` (OA), `oa_secret_key` (webhook) | Template phải được Zalo duyệt trước. `purpose`: `care` (thông báo dịch vụ) hoặc `marketing`. Refresh token dùng một lần, Core tự lưu token mới (đã mã hóa). Thử nghiệm: `"mode": "development"` |

- `data` của template ánh xạ tham số sang trait, ví dụ `{"ten_khach": "name", "so_tien": "open_cart.amount", "thuong_hieu": "=DMAG"}`. Giá trị bắt đầu bằng `=` là hằng số.
- **Webhook Zalo OA**: khai báo URL `https://cdp.dmag.vn/v1/webhooks/zalo/<tenant>` trong trang quản lý ứng dụng Zalo. Core kiểm tra chữ ký `X-ZEvent-Signature`. Khi khách chia sẻ số điện thoại, Zalo ID được ghép vào hồ sơ khách.
- **Biên nhận ZNS**: các sự kiện `user_received_message` và `user_seen_message` của webhook OA được gắn với tin ZNS đã gửi (qua `tracking_id`) và hiện trên hành trình khách.

### Facebook Lead Ads

1. Tạo kênh loại **Facebook Lead Ads**:
   - cấu hình: `app_id`, `verify_token` (chuỗi tự đặt);
   - bí mật: `app_secret`, `page_access_token` (quyền `leads_retrieval`, `pages_manage_metadata`).
2. Trong App Dashboard → Webhooks → Page, đăng ký trường `leadgen` với URL `https://cdp.dmag.vn/v1/webhooks/facebook/<tenant>` và đúng `verify_token`.
3. Đăng ký app vào Page: `POST /<page_id>/subscribed_apps?subscribed_fields=leadgen`.

Cách hoạt động:

- Core kiểm tra chữ ký `X-Hub-Signature-256` bằng `app_secret`, rồi lấy chi tiết lead qua Graph API.
- Lead được nhận diện bằng email/SĐT. Khi khách sau này đăng ký trên web hoặc có trong Odoo, lịch sử được gộp lại.
- Nguồn đầu tiên của khách được ghi là `facebook / lead_ads`.

## 4b. Hành trình, dashboard và mô hình dự đoán

- **Console → Dashboard**: chọn 7/30/90 ngày. Mọi biểu đồ có nút **Bảng** để xem số liệu. Tenant mô phỏng có nhãn "Dữ liệu mô phỏng".
- **Hành trình khách**: Console → Hồ sơ → chọn khách, hoặc tab **CDP 360** trong Odoo.
  - Tab CDP 360 nhúng trang `/<public_url>/embed/journey`, ký HMAC bằng shared secret của tenant, link hết hạn sau 1 giờ.
  - Trang nhúng chỉ trả dữ liệu đúng khách đó.
- **Mô hình**:
  - Core tự huấn luyện lại mỗi 24 giờ trong job định kỳ. Huấn luyện tay: nút **Huấn luyện mô hình** hoặc `POST /v1/admin/models/train?tenant=<id>`.
  - Bảng **Chất lượng mô hình** so sánh với baseline, ghi cỡ mẫu và thời điểm huấn luyện.
  - Khi số lượt mua trong 30 ngày còn ít (< 15), mô hình mua hàng dùng trọng số quy tắc và ghi rõ chế độ này.
- **Kích hoạt theo dự đoán**: phân khúc có sẵn `likely_buyers_30d` (≥ 50%). Có thể dùng trait `purchase_probability_30d`, `predicted_products.0.item`, `next_touchpoints.0.key` trong trình tạo phân khúc.
- **Dữ liệu mô phỏng để demo/đào tạo**: `python -m cdp_core.simulate --tenant dmag_sim --people 2000`. Tenant này được gắn cờ `simulated` và không nên tạo trên môi trường production.

## 5. Vận hành hằng ngày

| Việc | Ở đâu |
|-----|------|
| Theo dõi outbox (chờ gửi, lỗi) | Odoo: CDP → Cấu hình → Outbox. `GET /cdp/v1/health` trả độ trễ |
| Theo dõi kênh (đã gửi, bỏ qua, lỗi) | Console → Kênh kích hoạt |
| Tạo/sửa phân khúc | Console → Phân khúc (xem trước số lượng trước khi lưu) |
| Hành động khi khách vào phân khúc | Odoo: CDP → Phân khúc → (mở phân khúc) → bảng "Khi khách vào phân khúc" |
| Email marketing theo phân khúc | Odoo: mở phân khúc → **Tạo email marketing** (đã lọc consent và blacklist) |
| Đối soát Odoo ↔ CDP | Tự động mỗi 24 giờ; chạy tay: Console → Đối soát với Odoo |
| Yêu cầu xóa dữ liệu | Odoo: hồ sơ khách → tab CDP 360 → Yêu cầu xóa dữ liệu. Khách tự gửi yêu cầu ở `/my/privacy` |
| Nhật ký yêu cầu quyền dữ liệu | Console → Quyền riêng tư |
| Dashboard, chất lượng mô hình | Console → Dashboard |

## 6. Bảo mật

- Toàn bộ trao đổi Odoo ↔ Core được ký HMAC-SHA256, chống phát lại trong 5 phút. Mỗi tenant dùng secret riêng.
- Secret của kênh được mã hóa bằng Fernet (`CDP_MASTER_KEY`). API và console chỉ hiển thị `••••`.
- Admin token: chỉ đội vận hành DMAG giữ. Đặt console sau VPN hoặc giới hạn IP.
- `userToken` trên website được Odoo ký và có hạn dùng; trình duyệt không giả mạo được danh tính người khác.
- Dữ liệu tới Facebook chỉ là hash SHA-256; nhắn tin Zalo theo SĐT chỉ khi khách có consent đúng mục đích.
