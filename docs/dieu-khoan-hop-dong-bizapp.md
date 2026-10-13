# Checklist điều khoản hợp đồng DMAG – Bizapp

> Mục đích: danh sách các điều khoản DMAG cần có trong hợp đồng (hoặc phụ lục) với Bizapp, để bảo vệ quyền sở hữu toàn bộ sản phẩm (Dental ERP, Marketing Automation, CDP và các module liên quan) trước khi thương mại hóa.
> **Đây là danh sách nhắc việc về mặt kỹ thuật–kinh doanh, không phải văn bản pháp lý.** Cần luật sư soạn thảo và rà soát.
> Ngày lập: 13/10/2026 · Trạng thái: **CHƯA đưa vào hợp đồng**

## A. Sở hữu trí tuệ

- [ ] **A1. Chuyển giao toàn bộ quyền sở hữu** cho DMAG đối với mọi sản phẩm Bizapp phát triển theo đặt hàng của DMAG:
  - Dental ERP, Marketing Automation, các module customize Odoo;
  - module `cdp_*`, CDP Core (nếu Bizapp tham gia);
  - tài liệu, thiết kế, dữ liệu mẫu, script triển khai.
- [ ] **A2. Phạm vi quyền**: DMAG toàn quyền sử dụng, sửa đổi, phân phối, bán, cấp phép lại, không giới hạn lãnh thổ và thời hạn. Bizapp không giữ lại quyền sử dụng thương mại.
- [ ] **A3. Sản phẩm đã có từ trước của Bizapp** (thư viện, khung code dùng chung): nếu có trong sản phẩm, Bizapp liệt kê cụ thể và cấp cho DMAG **license vĩnh viễn, không độc quyền, được chuyển nhượng và cấp phép lại** cho khách hàng của DMAG.
- [ ] **A4. Không cạnh tranh / không bán lại**: Bizapp không bán, cấp phép hoặc dùng sản phẩm của DMAG (hay bản phái sinh) cho bên khác. Thời hạn và phạm vi do luật sư xác định cho hợp lệ.

## B. Tính nguyên gốc & giấy phép

- [ ] **B1. Cam kết mã tự viết**: Marketing Automation và mọi module "mô phỏng bản Enterprise" do Bizapp tự viết, **không chứa mã nguồn, giao diện hay tài nguyên của Odoo Enterprise** (giấy phép OEEL) hoặc của bên thứ ba không có quyền.
- [ ] **B2. Bồi thường**: Bizapp chịu trách nhiệm và bồi thường cho DMAG nếu có khiếu nại vi phạm bản quyền phát sinh từ phần Bizapp phát triển, kể cả khi khiếu nại xuất hiện sau khi hợp đồng kết thúc.
- [ ] **B3. Danh mục giấy phép**: Bizapp cung cấp và cập nhật danh sách mọi module/thư viện bên thứ ba kèm giấy phép (LGPL, AGPL, MIT, OPL, độc quyền…). **Không dùng module AGPL** trong sản phẩm thương mại nếu chưa có DMAG đồng ý bằng văn bản.
- [ ] **B4. Sửa lõi Odoo**: liệt kê mọi chỗ sửa trực tiếp mã lõi Odoo (nếu có), ưu tiên chuyển thành module.

## C. Mã nguồn & bàn giao

- [ ] **C1. Repo thuộc DMAG**: toàn bộ mã nằm trên kho Git do DMAG làm chủ (GitHub/GitLab của DMAG), Bizapp được cấp quyền đóng góp. Commit thường xuyên, không giữ mã riêng ngoài repo.
- [ ] **C2. Tài liệu bắt buộc**: tài liệu kỹ thuật (model, luồng nghiệp vụ, API), hướng dẫn cài đặt/nâng cấp, hướng dẫn vận hành.
- [ ] **C3. Bàn giao khi chấm dứt hợp đồng (exit plan)**: bàn giao mã, database, cấu hình server, tài khoản quản trị, khóa/credential, tên miền, chứng chỉ SSL; hỗ trợ chuyển giao trong N ngày với chi phí đã thỏa thuận.
- [ ] **C4. Hạ tầng**: nếu Bizapp đang host Odoo của DMAG, tài khoản cloud/server nên đứng tên DMAG, hoặc có điều khoản chuyển giao.

## D. Dữ liệu & bảo mật

- [ ] **D1. Thỏa thuận xử lý dữ liệu (DPA)**: Bizapp là bên xử lý dữ liệu cá nhân thay mặt DMAG theo **Luật Bảo vệ dữ liệu cá nhân 91/2025/QH15**. Phải có: phạm vi, mục đích, biện pháp bảo vệ, thông báo sự cố, xóa/trả dữ liệu khi kết thúc. Khi vận hành Dental ERP có **dữ liệu sức khỏe của bệnh nhân**, Bizapp là bên xử lý thứ cấp cho phòng khám.
- [ ] **D2. Bảo mật thông tin**: NDA với Bizapp và từng nhân sự tham gia.
- [ ] **D3. Quyền truy cập production**: tối thiểu, có log, thu hồi ngay khi nhân sự nghỉ việc.
- [ ] **D4. Thông báo sự cố bảo mật** trong thời hạn cụ thể, ví dụ 24 giờ, để DMAG kịp thực hiện nghĩa vụ báo cáo theo luật.

## E. Phối hợp kỹ thuật với dự án CDP

- [ ] **E1.** Bizapp thực hiện phần việc phía Odoo theo **data contract** do DMAG ban hành ([`data-contract-v1.md`](data-contract-v1.md)).
- [ ] **E2.** Thông báo trước ít nhất N ngày làm việc khi thay đổi model/field/luồng nghiệp vụ có trong mapping CDP; không phá vỡ tương thích ngược khi chưa thống nhất.
- [ ] **E3.** Tiêu chí nghiệm thu cho từng hạng mục (test tự động, hiệu năng, bảo mật).
- [ ] **E4.** Thời gian bảo hành sửa lỗi sau nghiệm thu; SLA hỗ trợ và vận hành (thời gian phản hồi theo mức độ).
- [ ] **E5.** Hỗ trợ nâng cấp phiên bản Odoo (17 → 18/19): phạm vi, chi phí, thời hạn.

## F. Thương mại

- [ ] **F1.** Cơ chế giá cho phát triển mới, bảo trì, hosting (nếu Bizapp tiếp tục host).
- [ ] **F2.** Nếu Bizapp tham gia triển khai cho khách hàng của DMAG (phòng khám): vai trò, chia doanh thu, cam kết không tiếp cận khách hàng của DMAG để bán trực tiếp.

---

**Nhắc việc:** đưa checklist này cho luật sư **trước khi** Bizapp bắt đầu viết các module mới (`cdp_*`, Dental ERP) để quyền sở hữu được xác lập ngay từ đầu.
