# LocalSub NPU

Ứng dụng desktop Windows 11 tạo phụ đề cho video/audio **hoàn toàn offline**, ưu tiên chạy trên **Intel NPU**
(Intel AI Boost) qua OpenVINO, tự chuyển sang GPU/CPU khi cần và ghi lại lý do.

- Nhận dạng giọng nói **tiếng Việt / tiếng Anh / tiếng Nhật / tiếng Hàn** bằng Whisper (OpenVINO GenAI).
- Dịch phụ đề **Anh ↔ Việt**, **Nhật → Anh/Việt**, **Hàn → Anh/Việt** bằng mô hình ngôn ngữ chạy trên máy (Qwen INT4), giữ nguyên mốc thời gian.
- Sửa phụ đề trực tiếp trong bảng, xuất **SRT, VTT, TXT** và file dự án `.localsub.json`.
- Media không bao giờ rời khỏi máy. Sau khi tải model lần đầu, không cần Internet và không cần API key.

> Phiên bản hiện tại: **0.12.0 (kết nối Perplexity qua MCP: server HTTP chỉ đọc có khóa API + đường hầm HTTPS) · 0.11.0 (tab Phân tích (AI) và MCP server chỉ đọc để trợ lý AI phân tích hiệu suất, log) · 0.10.0 (thêm tiếng Hàn: nhận dạng và dịch Hàn → Việt/Anh) · 0.9.2 (trả runtime OpenVINO về 2026.4.0 như 0.8.0, tắt mặc định ép tốc độ tối đa EcoQoS) · 0.9.1 (tự phục hồi khi driver NPU bị reset giữa chừng) · 0.9.0 (Wave 7: giữ máy thức và chạy tốc độ tối đa khi xử lý, danh mục model dùng chung, thêm Qwen3.5 và Distil-Whisper)**. Có **bộ cài Windows** (không cần Python/Node/Rust) — xem
> [Cài nhanh bằng bộ cài](#cài-nhanh-bằng-bộ-cài). Muốn phát triển hoặc chạy dòng lệnh thì cài từ mã nguồn (mục 2–6).

## Cài nhanh bằng bộ cài

1. Cài FFmpeg (một lần): `winget install --id Gyan.FFmpeg -e`, rồi đăng xuất/đăng nhập lại để PATH có hiệu lực.
2. Tải file `LocalSub.NPU_0.12.0_x64-setup.exe` ở trang
   [Releases](https://github.com/luongminhphu/localsub-npu/releases) (hoặc artifact
   `localsub-npu-windows-installer` của workflow **Release** trong tab Actions).
3. Chạy bộ cài. Bộ cài **chưa được ký số**, nên Windows SmartScreen có thể hiện "Windows protected your PC":
   bấm **More info → Run anyway**. Ứng dụng cài cho người dùng hiện tại, không cần quyền admin.
4. Mở **LocalSub NPU**, vào mục **Model** ở cuối trang, bấm **Tải về** cho `whisper-small-int8`
   (và model dịch nếu cần), rồi dùng như [mục 7](#7-sử-dụng-ứng-dụng-desktop).

Bộ cài chứa sẵn worker đã đóng gói (Python + OpenVINO, khoảng 300–400 MB sau khi cài). Model và phụ đề/nhật ký
được lưu ở `%LOCALAPPDATA%\com.luongminhphu.localsubnpu\models` và `...\output` (đổi được bằng
`LOCALSUB_MODEL_DIR` / `LOCALSUB_OUTPUT_DIR`). Gỡ cài đặt qua **Settings → Apps**; thư mục model không bị xóa tự động
để tránh phải tải lại — xóa tay nếu muốn giải phóng ổ đĩa.

> Nếu máy đã cài bản mã nguồn và đặt `LOCALSUB_WORKER_DIR` / `LOCALSUB_WORKER_PYTHON`, bản cài sẽ ưu tiên dùng
> worker theo các biến đó. Xóa hai biến này để dùng worker đóng gói sẵn.

---

## Mục lục

0. [Cài nhanh bằng bộ cài](#cài-nhanh-bằng-bộ-cài)
1. [Yêu cầu hệ thống](#1-yêu-cầu-hệ-thống)
2. [Cài đặt phần mềm nền](#2-cài-đặt-phần-mềm-nền)
3. [Tải mã nguồn và cài worker](#3-tải-mã-nguồn-và-cài-worker)
4. [Tải model](#4-tải-model)
5. [Kiểm tra NPU](#5-kiểm-tra-npu)
6. [Chạy ứng dụng desktop](#6-chạy-ứng-dụng-desktop)
7. [Sử dụng ứng dụng desktop](#7-sử-dụng-ứng-dụng-desktop)
8. [Sử dụng bằng dòng lệnh](#8-sử-dụng-bằng-dòng-lệnh)
9. [File đầu ra](#9-file-đầu-ra)
10. [Chọn model và thiết bị](#10-chọn-model-và-thiết-bị)
11. [Biến môi trường](#11-biến-môi-trường)
12. [Xử lý sự cố](#12-xử-lý-sự-cố)
13. [Dành cho nhà phát triển](#13-dành-cho-nhà-phát-triển)
14. [Bảo mật và quyền riêng tư](#14-bảo-mật-và-quyền-riêng-tư)
15. [Lộ trình](#15-lộ-trình)

---

## 1. Yêu cầu hệ thống

| Thành phần | Yêu cầu |
|---|---|
| Hệ điều hành | Windows 11 x64 |
| CPU / NPU | Intel Core Ultra (Series 1/2) có Intel AI Boost NPU. Máy không có NPU vẫn chạy được trên GPU/CPU |
| RAM | 16 GB tối thiểu; **32 GB** khuyến nghị khi dùng model dịch 7–8B |
| Ổ đĩa trống | ~1 GB cho Whisper small; thêm ~5 GB cho model dịch 8B; thêm chỗ cho cache biên dịch NPU |
| Driver | Intel NPU driver và Intel Graphics driver mới nhất |
| Phần mềm | Bộ cài: chỉ cần FFmpeg. Từ mã nguồn: Git, Python 3.11, FFmpeg; thêm Node.js 22, Rust, Visual Studio C++ Build Tools nếu dùng app desktop |

---

## 2. Cài đặt phần mềm nền

Mở **PowerShell** (không cần quyền Administrator trừ khi winget yêu cầu) và chạy lần lượt:

```powershell
# Git và Python 3.11 (worker)
winget install --id Git.Git -e
winget install --id Python.Python.3.11 -e

# FFmpeg (tách âm thanh từ video)
winget install --id Gyan.FFmpeg -e

# Chỉ cần cho ứng dụng desktop:
winget install --id OpenJS.NodeJS.LTS -e
winget install --id Rustlang.Rustup -e
winget install --id Microsoft.VisualStudio.2022.BuildTools -e --override "--wait --passive --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
```

Sau khi cài xong, **đóng và mở lại PowerShell** để PATH được cập nhật, rồi kiểm tra:

```powershell
git --version
py -3.11 --version
ffmpeg -version | Select-Object -First 1
node --version        # v22.x trở lên (desktop)
rustc --version       # desktop
```

Nếu `rustc` báo chưa có toolchain: `rustup default stable`.

**Driver NPU:** cập nhật qua Windows Update, Lenovo Vantage (hoặc công cụ của hãng máy), hoặc trang
Intel NPU Driver. Sau khi cài, Task Manager > Performance phải hiện mục **NPU 0**.

**WebView2** đã có sẵn trên Windows 11, không cần cài thêm.

Nếu PowerShell chặn chạy script (`running scripts is disabled`), cho phép trong phiên hiện tại:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

---

## 3. Tải mã nguồn và cài worker

```powershell
cd D:\Projects            # thư mục tùy chọn, tránh thư mục đồng bộ OneDrive
git clone https://github.com/luongminhphu/localsub-npu.git
cd localsub-npu
```

Chạy script cài đặt. Script sẽ tạo môi trường ảo `apps\inference\.venv`, cài OpenVINO GenAI và tải
model Whisper mặc định (`whisper-small-int8`, ~250 MB):

```powershell
# Chỉ nhận dạng giọng nói
.\scripts\setup-wave2.ps1

# Nhận dạng + dịch Anh <-> Việt (tải thêm model dịch ~5 GB)
.\scripts\setup-wave2.ps1 -TranslationModel qwen3-8b-int4-cw
```

Tham số của `setup-wave2.ps1`:

| Tham số | Mặc định | Ý nghĩa |
|---|---|---|
| `-Model` | `whisper-small-int8` | Model Whisper tải về |
| `-TranslationModel` | (không) | Model dịch tải thêm, ví dụ `qwen3-8b-int4-cw` |
| `-PythonVersion` | `3.11` | Phiên bản Python dùng để tạo venv |
| `-SkipModelDownload` | tắt | Chỉ cài thư viện, không tải model |

Khi hoàn tất, script in kết quả chẩn đoán (phiên bản OpenVINO, danh sách thiết bị, model đã cài).

---

## 4. Tải model

Model được lưu trong `data\models\<model-id>` (đã nằm trong `.gitignore`).

**Cách dễ nhất (từ Wave 4):** mở app desktop, vào mục **Model** ở cuối trang, bấm **Tải về** cạnh model cần dùng.
App hiện thanh tiến trình (MB đã tải / tổng), có nút **Dừng**. Model tải dở hiện trạng thái **Tải dở** và nút
**Tải tiếp** — lần tải sau tiếp tục từ phần đã có, không tải lại từ đầu. Trước khi tải, app kiểm tra ổ đĩa còn đủ
chỗ (dung lượng model + 10%). Nút **Xóa** hỏi xác nhận rồi mới xóa thư mục model.

Hoặc dùng dòng lệnh:

```powershell
cd apps\inference
.\.venv\Scripts\python.exe -m app.main models list                         # trạng thái: installed / incomplete / missing
.\.venv\Scripts\python.exe -m app.main models download whisper-large-v3-turbo-int8
.\.venv\Scripts\python.exe -m app.main models download qwen3-8b-int4-cw   # bị ngắt thì chạy lại để tải tiếp
.\.venv\Scripts\python.exe -m app.main models remove qwen3-0.6b-int4      # xóa model không dùng
cd ..\..
```

Một model chỉ được coi là **đã cài** khi đủ các file `.xml` kèm file trọng số `.bin` tương ứng và việc tải
đã hoàn tất (app ghi file đánh dấu `.localsub-model.json`). Model tải dở báo lỗi `MODEL_INCOMPLETE` thay vì
lỗi biên dịch khó hiểu như trước. Mỗi lần tải/xóa được ghi vào `data\models\logs\models.jsonl`.

> Khi chạy lệnh worker trực tiếp (không qua script), hãy đặt thư mục dữ liệu để dùng chung với app desktop:
> `$env:LOCALSUB_MODEL_DIR="$PWD\data\models"; $env:LOCALSUB_OUTPUT_DIR="$PWD\data\output"` (chạy ở thư mục gốc repo),
> hoặc tạo file `.env` ở thư mục gốc (xem [.env.example](.env.example)). Các script `.ps1` và app desktop tự làm việc này.

Xem bảng chọn model ở [mục 10](#10-chọn-model-và-thiết-bị) và chi tiết ở [docs/model-setup.md](docs/model-setup.md).

---

## 5. Kiểm tra NPU

```powershell
.\scripts\diagnostics.ps1
```

Kết quả cần có `NPU` trong `available_devices`. Để so sánh tốc độ NPU / GPU / CPU trên cùng một đoạn video
(nên dùng clip khoảng 60 giây):

```powershell
.\scripts\benchmark-devices.ps1 -Source "D:\Videos\clip-60s.mp4" -Language vi
```

Trong lúc chạy, mở **Task Manager > Performance > NPU 0** để xem NPU có hoạt động.
Lần chạy NPU đầu tiên phải biên dịch model nên chậm (Whisper khoảng 1 phút, model dịch 8B có thể vài phút);
các lần sau nhanh hơn nhiều nhờ cache ở `data\models\.ov-cache`.

---

## 6. Chạy ứng dụng desktop

Yêu cầu: đã hoàn thành mục 2–4 (có venv worker và model).

```powershell
cd apps\desktop
npm ci                 # lần đầu, cài thư viện giao diện
npm run tauri dev      # mở cửa sổ ứng dụng (lần đầu biên dịch Rust mất vài phút)
```

Tự đóng gói bộ cài (giống workflow Release trên GitHub):

```powershell
.\scripts\build-worker.ps1          # đóng băng worker bằng PyInstaller -> apps\inference\dist\localsub-worker\ + smoke test
cd apps\desktop
npx tauri build --bundles nsis --config src-tauri/tauri.bundle.conf.json
# kết quả: apps\desktop\src-tauri\target\release\bundle\nsis\LocalSub.NPU_0.12.0_x64-setup.exe
```

`tauri.bundle.conf.json` chỉ dùng khi build bộ cài (thêm thư mục worker vào gói); `npm run tauri dev` không cần nó.

Thứ tự ứng dụng tìm worker: (1) biến `LOCALSUB_WORKER_DIR` / `LOCALSUB_WORKER_PYTHON` nếu có đặt;
(2) worker đóng gói sẵn trong thư mục cài (`worker\localsub-worker.exe`); (3) `apps\inference\.venv\Scripts\python.exe`
dò ngược từ vị trí file chạy (chế độ phát triển). Muốn bản cài dùng worker từ mã nguồn, đặt biến môi trường:

```powershell
[Environment]::SetEnvironmentVariable("LOCALSUB_WORKER_DIR", "D:\Projects\localsub-npu\apps\inference", "User")
[Environment]::SetEnvironmentVariable("LOCALSUB_WORKER_PYTHON", "D:\Projects\localsub-npu\apps\inference\.venv\Scripts\python.exe", "User")
```

`npm run dev` mở giao diện trong trình duyệt ở chế độ **xem trước với dữ liệu mẫu** (không chạy model), chỉ dùng để xem giao diện.

---

## 7. Sử dụng ứng dụng desktop

Giao diện mặc định bằng **tiếng Việt**; bấm **VI / EN** ở góc trên bên phải để đổi sang tiếng Anh (lựa chọn được
ghi nhớ). Trang gồm 3 bảng cấu hình, thanh chạy, bảng sửa phụ đề, mục **Model** và mục **Lịch sử xử lý**.
Tên nút trong tài liệu dưới đây ghi theo giao diện tiếng Việt, kèm tên tiếng Anh trong ngoặc.

### Bước 1 — Media

- Bấm **Chọn video hoặc audio** (Choose video or audio) và chọn file (MP4, MKV, MOV, AVI, WEBM, MP3, WAV, M4A).
- Hoặc bấm **Mở project đã lưu** (Open saved project) để mở lại phụ đề đã lưu và sửa tiếp.

### Bước 2 — Cấu hình (Configure)

| Mục | Chọn |
|---|---|
| **Ngôn ngữ nói** | Ngôn ngữ trong video: Tiếng Việt, Tiếng Anh, Tiếng Nhật, Tiếng Hàn hoặc Tự nhận diện |
| **Model nhận dạng · thiết bị** | Model Whisper và thiết bị (Tự động, NPU, GPU, CPU) |
| **Dịch sang** | Không dịch, hoặc ngôn ngữ đích (Việt/Anh) để tạo thêm bản dịch |
| **Model dịch · thiết bị** | Model dịch và thiết bị (hiện khi có chọn dịch) |
| **Định dạng xuất** | Tick SRT / VTT / TXT / JSON |

Trong danh sách model, model chưa có được ghi **(chưa tải)** hoặc **(tải dở)**. Khi đó ứng dụng hiện dòng
**Cần tải model trước: …** và nút chạy bị khóa — tải model trong mục **Model** bên dưới.

### Bước 3 — Môi trường chạy (Runtime)

Hiện phiên bản OpenVINO, FFmpeg, danh sách thiết bị khả dụng, thiết bị mà `auto` sẽ chọn và thời điểm kiểm tra
(giờ máy, kèm múi giờ). Bấm **Kiểm tra lại** sau khi cài driver hoặc FFmpeg.

### Chạy

- Bấm **Tạo phụ đề** (Generate subtitles). Thanh tiến trình đi qua các bước: kiểm tra file → tách âm thanh → nạp model →
  nhận dạng → dịch (hiện số câu đã dịch / tổng) → ghi file.
- Khi xong, ứng dụng hiện **Nhận dạng dùng** và **Dịch dùng** — thiết bị thực tế đã chạy (kèm lý do nếu phải chuyển sang GPU/CPU).
- Bấm **Hủy** để dừng; file cũ không bị thay đổi.
- Lỗi được hiển thị bằng câu dễ hiểu kèm mã lỗi gốc, ví dụ model tải dở → "Model mới tải dở. Bấm Tải tiếp…".

### Sửa phụ đề

- Sửa trực tiếp cột thời gian (**Bắt đầu**, **Kết thúc**, định dạng `hh:mm:ss,mmm`), **Gốc** và **Bản dịch**.
- **Tách**: tách câu làm đôi tại điểm giữa. **Gộp**: gộp với câu kế tiếp. **Xóa**: xóa câu.
- Ứng dụng kiểm tra liên tục: thời gian chồng nhau, End trước Start, câu trống, câu dài hơn 84 ký tự
  (tiếng Nhật: 32 ký tự; tiếng Hàn: 36 ký tự).
  Lỗi nghiêm trọng sẽ chặn nút xuất; dòng có lỗi được tô màu.
- Nhãn **chưa lưu** cho biết có thay đổi chưa xuất.

### Xuất file

Bấm **Xuất SRT + JSON …** (tên nút thay đổi theo định dạng đã chọn), chọn thư mục và tên file.
Mỗi ngôn ngữ ra một file riêng, ví dụ `talk.en.srt`, `talk.vi.srt`, kèm `talk.localsub.json` để mở lại sau.

### Quản lý model

Mục **Model** liệt kê 6 model nhận dạng và 5 model dịch với trạng thái **Đã cài / Tải dở / Chưa tải**, dung lượng
thực tế (hoặc ước tính `~` khi chưa tải) và nhãn **Đang chọn** cho model đang dùng trong cấu hình. Mỗi lúc chỉ tải
một model; có thể tải model trong khi đang chạy job khác. Không xóa được model đang được job sử dụng.

### Lịch sử xử lý

Mục **Lịch sử xử lý** đọc 10 job gần nhất từ `data\output\logs\jobs.jsonl` (tự làm mới sau mỗi job):
thời gian bắt đầu (giờ máy, cột ghi múi giờ, ví dụ GMT+7), tên file, ngôn ngữ, thiết bị nhận dạng / dịch
(dấu `*` = đã chuyển thiết bị, rê chuột để xem lý do), thời lượng media, RTF và trạng thái (kèm mã lỗi).
Dòng tổng hợp cho biết tỉ lệ thành công, tỉ lệ chạy trên NPU, RTF trung vị và số lần chuyển thiết bị.
RTF tổng = tổng thời gian job (nạp model, nhận dạng, dịch) / thời lượng media; dòng nhỏ dưới giá trị tách thời gian nhận dạng (NĐ) và dịch. Nhỏ hơn 1 là nhanh hơn thời gian thực. Dòng "Cập nhật lúc …" chuyển
sang màu vàng khi dữ liệu cũ hơn 2 phút. Lịch sử chỉ chứa metadata — không có nội dung phụ đề hay đường dẫn đầy đủ.
Dòng lệnh tương đương: `python -m app.main history --limit 20`.

### Phân tích hiệu suất và tích hợp AI qua MCP (0.11.0)

Tab **Phân tích (AI)** đọc 50 job gần nhất trong `jobs.jsonl` và áp dụng các quy tắc phát hiện điểm chưa tối ưu.
Mỗi phát hiện có mức độ (Cao / Trung bình / Thấp / Thông tin), giải thích bằng số liệu, đề xuất, các job liên quan
và **hàm trong mã nguồn** cần xem (ví dụ `app/devices.py:compile_with_fallback`). Đầu tab ghi giờ phân tích (giờ máy,
kèm múi giờ) và thời điểm job mới nhất, để biết dữ liệu mới hay cũ. Bấm **Phân tích lại** sau khi chạy job mới.

| Mã phát hiện | Khi nào xuất hiện |
|---|---|
| `slow_asr_model_load` / `slow_translation_model_load` | Nạp Whisper > 30 s hoặc model dịch > 60 s (hoặc ≥ 3 lần trung vị cùng model/thiết bị). Lặp lại nhiều lần → mức Cao: bộ đệm `.ov-cache` không được dùng lại |
| `asr_rtf_regression` | Job mới nhất nhận dạng chậm ≥ 1,5 lần trung vị các job trước (cùng model, thiết bị; cần ≥ 3 job) |
| `slow_audio_extraction` | Tách audio > 2% thời lượng video (và > 20 s), thường do đọc từ ổ mạng/UNC |
| `translation_bottleneck` | Phần dịch sau khi nhận dạng xong chiếm > 50% tổng thời gian |
| `translation_fallback_lines` | > 10% số dòng phải giữ nguyên bản gốc |
| `overlap_idle` | Bật dịch chồng nhưng không dịch được dòng nào trong lúc nhận dạng |
| `device_fallback` | Phải chuyển thiết bị khi nạp model (ví dụ NPU → GPU) |
| `npu_device_lost` | Gặp `ZE_RESULT_ERROR_DEVICE_LOST` (có phục hồi hoặc job lỗi) |
| `failed_jobs` | ≥ 20% job thất bại (ít nhất 2 job) |
| `power_not_applied` | Không giữ được máy thức |
| `openvino_version_changed` | Log có nhiều phiên bản OpenVINO; so RTF theo từng phiên bản |

**Kết nối trợ lý AI (MCP).** Worker có sẵn một [MCP](https://modelcontextprotocol.io/specification/2026-07-28) server
**chỉ đọc**, chạy qua stdio: `localsub-worker.exe mcp` (bản cài) hoặc `python -m app.main mcp` (mã nguồn). Server hỗ trợ
phiên bản giao thức 2026-07-28 (`server/discover`) và cả handshake `initialize` cũ (2025-11-25, 2025-06-18, 2025-03-26).
Trong tab, chọn ứng dụng AI (Claude Desktop, VS Code hoặc Khác), bấm **Sao chép cấu hình** rồi dán vào:

- Claude Desktop: `claude_desktop_config.json` (Settings → Developer → Edit Config), khởi động lại Claude.
- VS Code: `.vscode/mcp.json` của workspace, bấm Start trên server `localsub-npu`.

Cấu hình đã có sẵn đường dẫn worker và thư mục dữ liệu (`LOCALSUB_OUTPUT_DIR`, `LOCALSUB_MODEL_DIR`) nên AI đọc đúng
nhật ký của app. Ví dụ (bản cài):

```json
{
  "mcpServers": {
    "localsub-npu": {
      "command": "C:\\Program Files\\LocalSub NPU\\worker\\localsub-worker.exe",
      "args": ["mcp"],
      "env": {
        "LOCALSUB_MODEL_DIR": "C:\\Users\\<bạn>\\AppData\\Local\\com.luongminhphu.localsubnpu\\models",
        "LOCALSUB_OUTPUT_DIR": "C:\\Users\\<bạn>\\AppData\\Local\\com.luongminhphu.localsubnpu\\output",
        "PYTHONUTF8": "1"
      }
    }
  }
}
```

Công cụ (tool) AI được dùng:

| Tool | Nội dung |
|---|---|
| `localsub_diagnostics` | FFmpeg, OpenVINO, thiết bị NPU/GPU/CPU, model đã cài, có `.ov-cache` hay chưa, giờ job cuối |
| `localsub_list_jobs` | Danh sách job (lọc `limit`, `state`, `since`): model, thiết bị, thời gian từng bước, RTF, phiên bản OpenVINO |
| `localsub_get_job` | Toàn bộ bản ghi một job: các bước kèm thời điểm, thiết bị đã thử, lý do chuyển, phục hồi, lỗi |
| `localsub_analyze_performance` | Các phát hiện như trong tab, kèm thống kê theo bước và theo model |
| `localsub_compare_jobs` | So sánh hai job từng bước (ví dụ trước/sau khi cập nhật) |
| `localsub_models` | Danh mục model và trạng thái cài |
| `localsub_optimization_plan` | Bản tóm tắt Markdown cho AI lập trình: phát hiện → bằng chứng → hàm cần sửa → quy trình test và mở pull request |

Ngoài ra có resource `localsub://jobs/recent`, `localsub://analysis/latest`, `localsub://catalog` và prompt
`optimize_localsub` (tham số `focus`: load, asr, translation, io, all), `diagnose_job` (`job_id`).

**Tự động hóa tối ưu code.** Nút **Sao chép prompt cho AI** tạo sẵn yêu cầu cho trợ lý AI có quyền truy cập repo (ví dụ
Claude Code, VS Code Copilot agent): gọi phân tích, chọn điểm quan trọng nhất, xem bằng chứng, đọc các hàm trong
`code_refs`, viết test tái hiện, sửa trên nhánh mới, chạy `ruff` + `pytest` rồi **mở pull request** — AI không tự merge.
Sau khi cài bản mới, gọi lại `localsub_compare_jobs` để xác nhận cải thiện bằng job thật.

**Quyền riêng tư và giới hạn:**

- MCP server chỉ đọc `jobs.jsonl`, chẩn đoán và danh mục model; không chạy job, không tải/xóa model. Chế độ stdio không mở cổng mạng
  (chế độ HTTP cho Perplexity: xem mục dưới).
- Mặc định tên file và đường dẫn được ẩn với AI (`media-<mã băm>.mp4`, thư mục người dùng thành `~`). Thêm
  `--no-redact` vào `args` nếu muốn AI thấy tên file thật. Tab trong app luôn hiện tên thật vì chỉ hiển thị trên máy.
- Nhật ký không có nội dung phụ đề, nên AI không đọc được lời thoại. Nội dung tool trả về sẽ được gửi tới dịch vụ AI bạn dùng.
- Ổ mạng ánh xạ (ví dụ `Z:`) trông như ổ cục bộ trong log, nên quy tắc tách audio chậm chỉ nhận ra chắc chắn đường dẫn UNC `\\máy\thư-mục`.
- Dòng lệnh tương đương: `python -m app.main analyze --limit 50` (JSON) hoặc `--format markdown --language vi`.

### Kết nối Perplexity (0.12.0)

Perplexity không chạy MCP server qua stdio như Claude Desktop. Có hai cách:

| Cách | Dùng khi | Cần gì |
|---|---|---|
| **Custom remote connector** (web, ứng dụng Windows/Mac, Comet, Computer) | Mọi máy, kể cả máy Intel NPU | Gói Perplexity Pro/Max/Enterprise, URL **HTTPS** dùng Streamable HTTP, khóa API ([hướng dẫn của Perplexity](https://www.perplexity.ai/help-center/en/articles/13915507-adding-custom-remote-connectors)) |
| **Portable Computer – Local (stdio)** (ứng dụng Windows) | Máy có GPU NVIDIA RTX từ 24 GB VRAM và model chạy cục bộ | Không cần đường hầm; chỉ dùng ở chế độ Local ([hướng dẫn](https://www.perplexity.ai/help-center/en/articles/20260915-what-is-portable-computer)) |

Máy Intel Lunar Lake (như XBOOK) dùng cách thứ nhất. Trong tab **Phân tích (AI)** → chọn **Perplexity**:

1. **Bật server** (cổng mặc định 8765, có hộp xác nhận). Worker chạy `localsub-worker.exe mcp --http --port 8765`, chỉ nghe trên
   `127.0.0.1`, tạo khóa API ngẫu nhiên lưu ở `%LOCALAPPDATA%\com.luongminhphu.localsubnpu\mcp\http-token`.
2. **Mở đường hầm** (có hộp xác nhận). App chạy [Cloudflare quick tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/)
   `cloudflared tunnel --url http://127.0.0.1:8765` và hiện URL dạng `https://<tên>.trycloudflare.com/mcp`. Chưa có cloudflared thì cài:
   `winget install --id Cloudflare.cloudflared`, rồi bấm **Kiểm tra lại**.
3. Trong Perplexity: **Plugins** (hoặc **Customize → Connectors**) → **Add custom → Connector → Add MCP connector → Remote**, điền:
   - Name: `LocalSub NPU`
   - MCP Server URL: URL ở bước 2 (nút **Sao chép**)
   - Authentication: **API Key**, dán khóa ở bước 1
   - Transport: **Streamable HTTP**
4. Bật connector trong khung chat và hỏi, ví dụ: "Dùng LocalSub NPU phân tích hiệu suất các job gần đây và đề xuất tối ưu".

Tab hiện trạng thái server và đường hầm (kèm giờ GMT+7), yêu cầu gần nhất, số yêu cầu và số lần sai khóa. Mỗi lần mở
đường hầm nhanh, URL sẽ khác nên phải cập nhật lại trong connector; khóa API giữ nguyên cho tới khi bấm **Đổi khóa API và bật lại**.
Đóng app thì server và đường hầm cũng dừng (worker tự dừng khi app thoát). Đường hầm nhanh không cần tài khoản Cloudflare, không có cam kết
uptime và không hỗ trợ SSE, vì vậy chọn Transport **Streamable HTTP** (server luôn trả JSON). Muốn URL cố định thì tạo
Cloudflare Tunnel có tên bằng tài khoản Cloudflare và trỏ về `http://127.0.0.1:8765`.

**Bảo mật của chế độ HTTP:**

- Mọi yêu cầu cần khóa API (`Authorization: Bearer <khóa>`, `Authorization: <khóa>` hoặc `X-API-Key`), so sánh thời gian hằng.
  Sai khóa → 401; quá 10 lần sai/phút → 429 (khóa tạm), quá 120 yêu cầu/phút → 429.
- Kiểm tra `Origin` (chống DNS rebinding): chỉ chấp nhận khi không có Origin, `localhost` hoặc `https://*.perplexity.ai`
  (thêm nguồn khác bằng `LOCALSUB_MCP_ALLOWED_ORIGINS`). Giới hạn body 1 MB, không nhận batch.
- Công cụ vẫn chỉ đọc, đường dẫn được ẩn. Ai có cả URL và khóa đều đọc được nhật ký job, nên không chia sẻ khóa và đóng đường hầm khi không dùng.
- Nhật ký truy cập `output\logs\mcp_http.jsonl`: thời điểm (UTC), phương thức, tool, mã HTTP, kết quả xác thực, thời gian xử lý,
  IP gốc từ Cloudflare (`CF-Connecting-IP`). Không ghi khóa và tham số tool.
- Theo đặc tả [Streamable HTTP 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http):
  chỉ POST (GET/DELETE → 405), thông báo → 202, kiểm tra `MCP-Protocol-Version` / `Mcp-Method` / `Mcp-Name` (lệch → 400 `-32020`),
  không cấp `Mcp-Session-Id`. Client cũ dùng `initialize` (2025-11-25 trở về trước) vẫn chạy.

Dòng lệnh tương đương (không cần app):

```powershell
$env:LOCALSUB_MCP_TOKEN = "<khóa tự đặt, tối thiểu 24 ký tự>"   # bỏ qua để dùng khóa lưu trong file
& $py -m app.main mcp --http --port 8765
cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8765
```

---

## 8. Sử dụng bằng dòng lệnh

### Qua script PowerShell (khuyến nghị)

```powershell
# Video tiếng Việt -> phụ đề tiếng Việt trên NPU
.\scripts\transcribe.ps1 -Source "D:\Videos\hop.mp4" -Language vi -Device NPU

# Video tiếng Nhật -> phụ đề tiếng Nhật + bản dịch tiếng Việt (dùng Whisper large-v3-turbo cho tiếng Nhật)
.\scripts\transcribe.ps1 -Source "D:\Videos\seminar-jp.mp4" -Language ja -TranslateTo vi `
  -Model whisper-large-v3-turbo-int8 -Formats srt,json

# Video tiếng Anh -> phụ đề tiếng Anh + bản dịch tiếng Việt, xuất SRT, VTT và project JSON
.\scripts\transcribe.ps1 -Source "D:\Videos\webinar.mp4" -Language en -TranslateTo vi -Formats srt,vtt,json

# Dùng model dịch khác và chạy dịch trên GPU
.\scripts\transcribe.ps1 -Source ".\talk.mp4" -Language en -TranslateTo vi `
  -TranslationModel qwen2.5-7b-instruct-int4 -TranslationDevice GPU

# Whisper dịch thẳng mọi ngôn ngữ nói sang tiếng Anh (không cần model dịch)
.\scripts\transcribe.ps1 -Source ".\talk.mp4" -Language vi -Task translate
```

Tham số `transcribe.ps1`:

| Tham số | Mặc định | Giá trị |
|---|---|---|
| `-Source` | (bắt buộc) | Đường dẫn video/audio |
| `-Language` | `auto` | `auto`, `vi`, `en`, `ja`, `ko` |
| `-Task` | `transcribe` | `transcribe`, hoặc `translate` (Whisper → tiếng Anh) |
| `-Device` | `auto` | `auto`, `NPU`, `GPU`, `CPU` cho Whisper |
| `-Model` | `whisper-small-int8` | Model Whisper |
| `-TranslateTo` | (không) | `vi` hoặc `en` — thêm bản dịch bằng model dịch (tiếng Nhật, tiếng Hàn chỉ là ngôn ngữ nguồn) |
| `-TranslationModel` | `qwen3-8b-int4-cw` | Model dịch |
| `-TranslationDevice` | `auto` | Thiết bị cho model dịch |
| `-Formats` | `srt` | Danh sách `srt,vtt,txt,json` |
| `-Output` | (tự đặt) | Đường dẫn file `.srt` cho bản gốc |

### Gọi worker trực tiếp

Chạy trong `apps\inference` (nhớ đặt `LOCALSUB_MODEL_DIR` như ghi chú ở mục 4):

```powershell
cd apps\inference
$py = ".\.venv\Scripts\python.exe"

# Chẩn đoán
& $py -m app.main diagnostics

# Nhận dạng + dịch
& $py -m app.main transcribe D:\talk.mp4 --language vi --translate-to en --formats srt,json

# Dịch một file SRT có sẵn (kết quả nằm cạnh file gốc: D:\talk.vi.srt)
& $py -m app.main translate D:\talk.en.srt --from en --to vi --device NPU --formats srt,vtt

# Dịch phụ đề tiếng Nhật sang tiếng Việt hoặc tiếng Anh (kết quả: D:\seminar.vi.srt)
& $py -m app.main translate D:\seminar.ja.srt --from ja --to vi --formats srt
& $py -m app.main translate D:\seminar.ja.srt --from ja --to en --formats srt

# Phân tích hiệu suất từ nhật ký job (JSON, hoặc Markdown cho AI)
& $py -m app.main analyze --limit 50
& $py -m app.main analyze --format markdown --language vi --redact

# MCP server chỉ đọc cho trợ lý AI (stdio; thường do ứng dụng AI tự chạy)
& $py -m app.main mcp

# MCP qua HTTP trên 127.0.0.1 cho Perplexity (cần khóa API; --rotate-token tạo khóa mới)
& $py -m app.main mcp --http --port 8765

# Xuất lại từ project JSON đã sửa
& $py -m app.main export --input D:\talk.localsub.json --output-base D:\final\talk --formats srt,vtt,txt
```

Thêm `--events` để worker in tiến trình dạng JSON Lines (đây là cách ứng dụng desktop nhận tiến trình).

---

## 9. File đầu ra

Mặc định ghi vào `data\output\` ở thư mục gốc repo (đổi bằng `LOCALSUB_OUTPUT_DIR`):

```text
data/output/
  webinar.en.srt           # bản gốc theo ngôn ngữ nói
  webinar.vi.srt           # bản dịch
  webinar.en.vtt  webinar.vi.vtt  webinar.en.txt ...
  webinar.localsub.json    # dự án: mỗi câu có thời gian, bản gốc, bản dịch
  logs/jobs.jsonl          # nhật ký mỗi lần chạy
  .tmp/                    # file WAV tạm (tự dọn)
```

`logs/jobs.jsonl` ghi thiết bị yêu cầu / thực tế, lý do chuyển thiết bị, thời gian từng bước, hệ số thời gian thực,
số câu dịch lỗi (`translation_fallback_lines`) và mã lỗi — **không bao giờ ghi nội dung phụ đề**.

---

## 10. Chọn model và thiết bị

### Nhận dạng giọng nói (Whisper)

| Model | Dùng khi |
|---|---|
| `whisper-tiny-int8`, `whisper-base-int8` | Thử nhanh; tiếng Việt yếu |
| `whisper-small-int8` | **Mặc định**, cân bằng, chạy tốt trên NPU |
| `whisper-medium-int8` | Tiếng Việt tốt hơn, chậm hơn |
| `whisper-large-v3-turbo-int8` | Chất lượng/tốc độ tốt nhất cho tiếng Việt và **tiếng Nhật**; thử cả GPU và NPU |
| `whisper-large-v3-int8` | Chất lượng cao nhất, chậm nhất |
| `distil-whisper-large-v3-int8` (0.9.0, thử nghiệm) | **Chỉ tiếng Anh**: gần bằng large-v3, decoder 2 lớp nên nhanh hơn nhiều. Phải chọn ngôn ngữ nói = Tiếng Anh; app chặn khi chọn Tự nhận diện, tiếng Nhật hoặc tiếng Việt |

### Dịch (LLM)

| Model | Dung lượng | Dùng khi |
|---|---|---|
| `qwen3-8b-int4-cw` | ~5 GB | **Mặc định**, định dạng INT4 channel-wise tối ưu cho NPU |
| `qwen2.5-7b-instruct-int4` | ~4.5 GB | Dịch Anh–Việt tốt, nhanh trên GPU Arc |
| `qwen3-4b-int4` | ~2.5 GB | Máy ít RAM |
| `qwen2.5-1.5b-instruct-int4` | ~1 GB | Bản nháp nhanh, cần soát kỹ |
| `qwen3-0.6b-int4` | ~0.5 GB | Chỉ để kiểm tra luồng chạy; chất lượng dịch kém |
| `qwen3.5-9b-int4` (0.9.0, thử nghiệm) | ~6.3 GB | Thế hệ Qwen3.5 (09/2026), đa ngôn ngữ tốt hơn. Chỉ GPU/CPU |
| `qwen3.5-4b-int4` (0.9.0, thử nghiệm) | ~3.6 GB | Qwen3.5 cân bằng. Chỉ GPU/CPU |
| `qwen3.5-2b-int4` (0.9.0, thử nghiệm) | ~2.1 GB | Qwen3.5 nhẹ. Chỉ GPU/CPU |

**Về Qwen3.5 (thử nghiệm):** Intel chỉ phát hành Qwen3.5 dạng model ảnh + chữ (VLM). App chỉ nạp phần ngôn ngữ qua `ContinuousBatchingPipeline` (không chạy phần xử lý ảnh), vẫn dịch nhiều lô một lần như Qwen3. Cần OpenVINO GenAI 2026.4 trở lên; chưa hỗ trợ NPU. Đo trong sandbox CPU với 79 câu (bản 0.8B, chỉ để so sánh tương đối): dịch đúng tiếng Việt ở các câu mà Qwen3 0.6B để nguyên tiếng Anh, nhưng **chậm hơn** (124 s so với 46 s của Qwen3 0.6B, 127 s của Qwen3 1.7B). Chưa đo trên GPU Arc: hãy chạy thử một video ngắn và so cột RTF tổng trong Lịch sử trước khi dùng thay Qwen3 8B.

**Thêm model mới:** toàn bộ model nằm trong một danh mục `apps/inference/app/catalog.py` (id, repo Hugging Face, dung lượng, cách nạp `whisper`/`llm`/`vlm`, thiết bị chạy được, ngôn ngữ, gợi ý tiếng Việt/Anh). Worker, lệnh `models list` và giao diện đều đọc từ đây, nên thêm một model OpenVINO đã chuyển đổi sẵn chỉ cần thêm một mục, không phải sửa giao diện hay Rust.

### Tiếng Hàn (0.10.0)

- Chọn **Tiếng Hàn** (hoặc `-Language ko`) khi biết chắc video tiếng Hàn; *Tự nhận diện* cũng nhận đúng `ko`. Tên file: `<tên>.ko.srt`.
- Nên dùng `whisper-large-v3-turbo-int8` (hoặc `medium`); app cảnh báo khi chọn tiny/base/small cho tiếng Hàn.
- Tiếng Hàn có khoảng trắng giữa các từ nên phụ đề vẫn **cắt theo từ**, nhưng tối đa 36 ký tự/câu (theo [hướng dẫn phụ đề tiếng Hàn của Netflix](https://partnerhelp.netflixstudios.com/hc/en-us/articles/216001127-Korean-Timed-Text-Style-Guide): 2 dòng × 16 ký tự, khoảng trắng tính 0,5) và ước lượng tốc độ đọc 85 ms/ký tự (~12 ký tự/giây).
- Dịch Hàn → Anh/Việt: nên dùng `qwen3-8b-int4-cw` hoặc `qwen3-4b-int4`. Prompt có quy tắc riêng: câu tiếng Hàn hay bị chia qua nhiều dòng và lược chủ ngữ, model dùng ngữ cảnh nhưng giữ **một dòng dịch cho mỗi dòng gốc**, chuyển kính ngữ/cấp độ lịch sự cho tự nhiên, giữ tên người và công ty. Model rất nhỏ (Qwen3 0.6B) dịch tiếng Hàn kém, nhiều dòng giữ nguyên bản gốc.

### Tiếng Nhật

- Chọn `Japanese` (hoặc `-Language ja`) thay vì `Auto detect` khi biết chắc video tiếng Nhật; tên file sẽ là `<tên>.ja.srt`.
- Nên dùng `whisper-large-v3-turbo-int8` (hoặc `medium`). Ứng dụng hiện cảnh báo khi chọn tiny/base/small cho tiếng Nhật.
- Phụ đề tiếng Nhật được cắt theo ký tự (tối đa 32 ký tự/câu), ưu tiên ngắt tại `。`, `、`, `！`, `？` hoặc khoảng trắng Whisper chèn giữa các câu.
- Dịch Nhật → Anh/Việt: nên dùng `qwen3-8b-int4-cw` hoặc `qwen3-4b-int4`. Prompt dịch có quy tắc riêng: câu tiếng Nhật thường bị chia qua nhiều dòng, model dùng ngữ cảnh nhưng vẫn giữ **một dòng dịch cho mỗi dòng gốc**, giữ tên riêng, chuyển kính ngữ (さん, 様) cho tự nhiên.
- Bản dịch lỗi của model nhỏ (lặp `1. 1. 1.`, trả về khung mẫu `N. text`) bị loại, dịch lại từng dòng, nếu vẫn lỗi thì giữ câu gốc và tăng `translation_fallback_lines`.

### Thiết bị

- `auto` cho **nhận dạng**: thử **NPU → GPU → CPU**, dùng thiết bị đầu tiên chạy được và ghi lý do chuyển.
- `auto` cho **dịch** (từ 0.6.0): thử **GPU → NPU → CPU**. LLM trên NPU dùng shape tĩnh và chỉ xử lý một chuỗi mỗi lần, nên dịch nhiều dòng song song trên GPU thường nhanh hơn nhiều.
- `NPU` / `GPU` / `CPU`: bắt buộc thiết bị đó; nếu không có sẽ báo lỗi `DEVICE_UNAVAILABLE`, không tự đổi.
- Whisper được giải phóng khỏi bộ nhớ trước khi nạp model dịch, nên có thể chạy cả hai trên NPU.
- Gợi ý bắt đầu: Whisper trên `NPU`, dịch trên `auto` (tức GPU). So sánh bằng `benchmark-devices.ps1` rồi cố định thiết bị nhanh nhất.

### Tối ưu tốc độ (0.6.0)

Với video dài, thời gian thường bị chiếm bởi **bước dịch** chứ không phải nhận dạng. Xem cột **RTF tổng** trong *Lịch sử xử lý*: dòng nhỏ bên dưới ghi `NĐ <thời gian nhận dạng> · Dịch <thời gian dịch>` để biết bước nào chậm.

Những thay đổi trong 0.6.0:

| Thay đổi | Tác dụng |
|---|---|
| Dịch nhiều nhóm dòng trong **một lần gọi** model (mặc định 4 nhóm × 8 dòng trên GPU/CPU) | Tận dụng continuous batching của OpenVINO GenAI; đo trên CPU sandbox: bước dịch nhanh gấp ~2 lần |
| Dòng dịch lỗi được **dịch lại cùng lúc** thay vì từng dòng một | Giảm số lần gọi model, nhất là trên NPU |
| NPU dùng `GENERATE_HINT=BEST_PERF` | Sinh token nhanh hơn, đổi lại biên dịch model lâu hơn ở lần đầu |
| `auto` cho dịch ưu tiên **GPU** | Tránh chi phí prefill theo shape tĩnh của NPU |
| Whisper xử lý theo **đoạn ~5 phút**, cắt tại chỗ im lặng nhất, bỏ qua đoạn im lặng | Có tiến độ `%` và thời gian còn lại ở bước Nhận dạng; bộ nhớ ổn định với video nhiều giờ |

Từ 0.8.0 số lô mỗi lần được tự điều chỉnh (xem mục dưới); `LOCALSUB_TRANSLATION_PARALLEL` (1–16) chỉ dùng khi muốn cố định.

### Nhận dạng và dịch chạy chồng (0.8.0)

Mặc định Whisper chạy trên **NPU** và LLM dịch chạy trên **GPU**, nên hai bước có thể chạy cùng lúc thay vì nối tiếp:

- Ngay khi Whisper bắt đầu, model dịch được nạp lên GPU song song (không còn chờ nạp model sau khi nhận dạng xong).
- Sau mỗi đoạn 5 phút, các câu phụ đề đã chắc chắn (mọi câu trừ đoạn nói cuối cùng, vì câu đó có thể đổi thời lượng khi đoạn sau tới) được đưa vào hàng đợi và GPU dịch ngay.
- Khi Whisper xong, app chỉ còn dịch phần đuôi. Tổng thời gian gần bằng **bước lâu hơn** cộng phần đuôi, thay vì nhận dạng + dịch.
- Lô dịch, ngữ cảnh 2 câu trước và prompt giống hệt khi dịch tuần tự; test tự động xác nhận cùng prompt. Kết quả dịch có thể khác vài câu vì batch khác nhau làm LLM tính toán số thực hơi khác.
- Thanh tiến độ: khi đang nhận dạng, ô *Dịch* cũng sáng và hiện số câu đã dịch / số câu đã có.
- Lịch sử: dòng dưới RTF tổng ghi `chạy chồng`; rê chuột để xem thời gian dịch còn lại sau khi nhận dạng xong. `jobs.jsonl` ghi `overlap`, `overlap_reason`, `translation_overlap_lines`, `timings_ms.translation_after_asr`.

App **không chạy chồng** (tự quay về tuần tự và ghi lý do vào `overlap_reason`) khi:

| Trường hợp | Lý do |
|---|---|
| Nhận dạng và dịch cùng một thiết bị (ví dụ cùng GPU) | Hai model chỉ thay phiên nhau, không nhanh hơn |
| RAM trống ít hơn ~1,3 lần dung lượng model dịch | Tránh nạp model dịch sớm khi thiếu RAM |
| `LOCALSUB_OVERLAP=0` | Tắt thủ công |

Lưu ý: GPU và NPU vẫn dùng chung băng thông RAM. Whisper small nhỏ (~250 MB) và chủ yếu tốn tính toán nên ít tranh với LLM hơn nhiều so với việc hai bản LLM cùng dịch, nhưng tốc độ mỗi bước khi chạy chồng có thể chậm đi một chút.

### GPU tự điều chỉnh số lô dịch (0.8.0)

Khi sinh chữ, LLM bị giới hạn bởi **băng thông RAM**: mỗi bước phải đọc lại toàn bộ trọng số model (Qwen3 8B khoảng 4,9 GB). Gửi nhiều lô trong một lần gọi giúp đọc trọng số một lần mà sinh chữ cho nhiều câu, nên đây là cách tăng tốc chính trên GPU.

- Trên GPU/CPU, app thử lần lượt **4 → 8 → 16 lô mỗi lần** (lô đầu tiên để làm nóng, không tính), đo số câu/giây và chỉ lên mức tiếp theo khi nhanh hơn ít nhất 10%. Dừng ở mức tốt nhất cho đến hết job.
- Nếu một mức lớn bị lỗi (ví dụ thiếu bộ nhớ GPU), lần gọi đó chạy lại từng câu và app giữ mức thấp hơn; không mất câu nào.
- NPU luôn dịch 1 lô mỗi lần. Đặt `LOCALSUB_TRANSLATION_PARALLEL` để cố định mức và tắt tự điều chỉnh.
- Lịch sử (rê chuột lên cột RTF tổng) hiện mức đã chọn, ví dụ `dịch ×8 lô/lần`; `jobs.jsonl` ghi `translation_parallel` và `translation_tuning` (mức, câu/giây).

**Vì sao bỏ chế độ GPU+NPU (có ở 0.7.0):** GPU và NPU dùng chung RAM LPDDR5X; mỗi thiết bị giữ một bản model và cùng tranh băng thông. Đo thực tế với video 2h28m: chỉ GPU dịch mất 32m50s, GPU+NPU mất 38m22s (chậm hơn ~17%) và tốn thêm RAM cho bản model thứ hai. Job cũ vẫn hiện đúng trong Lịch sử; dòng lệnh `--translation-device HYBRID` giờ bị từ chối.

### Giữ máy thức khi xử lý (0.9.0)

Trước 0.9.0, Windows có thể cho máy **sleep** khi không có thao tác chuột/bàn phím, hoặc chuyển worker sang chế độ tiết kiệm (EcoQoS, chạy trên lõi tiết kiệm điện) khi cửa sổ app bị thu nhỏ, làm job dài chạy rất chậm hoặc dừng giữa chừng.

Từ 0.9.0, trong suốt job worker:

| Cơ chế | Tác dụng |
|---|---|
| `SetThreadExecutionState(ES_CONTINUOUS \| ES_SYSTEM_REQUIRED)` | Chặn sleep do idle đến khi job xong, lỗi hoặc bị hủy; sau đó trả về chế độ bình thường |
| Tắt power throttling (`SetProcessInformation` / `ProcessPowerThrottling`) — **chỉ khi bật `LOCALSUB_FULL_SPEED=1`** (mặc định tắt từ 0.9.2) | Worker không bị Windows 11 đưa sang EcoQoS. Mặc định tắt vì trên Lunar Lake CPU, GPU, NPU dùng chung ngân sách điện: ép CPU chạy tối đa có thể lấy bớt điện của GPU (dịch) và NPU (nhận dạng) |
| Tùy chọn giữ màn hình sáng | `--keep-display-on` hoặc `LOCALSUB_KEEP_DISPLAY_ON=1`; mặc định cho màn hình tắt (máy vẫn chạy) để tiết kiệm điện |

- Bật/tắt bằng ô **Giữ máy thức khi xử lý** trong *Cấu hình* (mặc định bật, app nhớ lựa chọn). Dòng lệnh: `--allow-sleep` để tắt; biến `LOCALSUB_KEEP_AWAKE=0` cũng tắt.
- Ô *Giữ máy thức* trong *Môi trường chạy* hiện `Bật · tốc độ tối đa` khi cả hai cơ chế đã áp dụng. `jobs.jsonl` ghi trường `power` (`keep_awake`, `display_on`, `throttling_disabled`, `reason`).
- **Giới hạn:** gập màn hình, bấm nút nguồn hoặc chọn Sleep trong Start vẫn làm máy ngủ. Với job dài nên cắm sạc và đặt *Control Panel → Power Options → Choose what closing the lid does* = *Do nothing* khi cắm sạc. Chính sách công ty (GPO) có thể ghi đè.

### Kết hợp CPU, NPU, GPU — đã đo gì (0.9.0)

Phân công hiện tại: **NPU** nhận dạng, **GPU** dịch theo lô và chạy chồng với nhận dạng, **CPU** giải mã âm thanh (FFmpeg), điều phối và tokenizer. Các cách kết hợp thêm đã được thử và **không đưa vào** vì chậm hơn:

| Cách | Kết quả đo | Lý do |
|---|---|---|
| GPU + NPU cùng dịch (0.7.0) | Video 2h28m: 38m22s so với 32m50s chỉ GPU | Hai bản model tranh băng thông RAM chung |
| Speculative decoding: model nháp nhỏ (Qwen3 0.6B) đoán trước, model chính kiểm tra | Sandbox CPU, Qwen3 1.7B, 79 câu: dịch theo lô 4 → 127 s, thêm model nháp → 139 s (chậm hơn 9%); không theo lô: 212 s → 201 s | Dịch theo lô đã đọc trọng số một lần cho nhiều câu, nên model nháp không còn lợi |
| Qwen3.5 MTP (đầu dự đoán nhiều token) | Sandbox CPU, 79 câu: 148 s một-câu-một-lần so với 124 s theo lô | MTP chỉ chạy được từng chuỗi một |

Vì bước dịch bị giới hạn bởi băng thông RAM, cách tăng tốc hiệu quả nhất vẫn là: dịch theo lô trên GPU, chạy chồng với nhận dạng trên NPU, và không để Windows hạ tốc độ worker (mục trên).

### Giao diện (0.7.0)

- Bố cục một màn hình: cột trái là **Media, Cấu hình, Môi trường chạy**; khung phải có thanh tiến độ + nút **Tạo phụ đề** và 4 tab **Phụ đề / Tải hệ thống / Lịch sử / Model**. Chỉ nội dung trong tab cuộn, cả cửa sổ không cuộn.
- Khi bấm Tạo phụ đề, app tự chuyển sang tab *Tải hệ thống*; xong job tự chuyển về tab *Phụ đề*. Dùng phím ←/→ để đổi tab.
- Cửa sổ hẹp hơn 880 px chuyển về một cột.

### Chart tải CPU / GPU / NPU

Khung **Tải hệ thống** trong app hiện tải CPU, GPU, NPU và RAM mỗi giây, lưu 5 phút gần nhất:

- Số liệu lấy từ Windows Performance Counters (`Processor Information`, `GPU Engine`) — cùng nguồn với Task Manager. NPU Intel AI Boost được nhận ra qua DXCore.
- Mỗi thẻ ghi trạng thái `đang đo` / `không phát hiện` / `đang chờ`, giá trị hiện tại, trung bình và đỉnh 5 phút.
- Dòng trạng thái ghi thời điểm mẫu mới nhất kèm múi giờ; nếu quá 5 giây không có mẫu mới sẽ báo *mất tín hiệu*.
- Vạch dọc tím đánh dấu thời điểm job chuyển bước (Nhận dạng, Dịch…), giúp thấy bước nào đang dùng thiết bị nào.
- Bộ đo chỉ chạy khi app đang mở khung này và tự dừng sau 60 giây không được đọc. Nút **Tạm dừng** giữ nguyên chart để xem lại.
- Trên máy không phải Windows (hoặc bản xem trước trên trình duyệt), chart hiện số liệu mô phỏng hoặc thông báo không hỗ trợ.

Cách đọc: nếu ở bước Dịch mà GPU thấp và CPU cao, model dịch đang chạy trên CPU — kiểm tra lại thiết bị dịch và driver GPU.

---

## 11. Biến môi trường

Đặt trong PowerShell hoặc file `.env` ở thư mục gốc (biến môi trường thật được ưu tiên hơn `.env`):

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `OPENVINO_DEVICE` | `auto` | Thiết bị mặc định: `auto`, `NPU`, `GPU`, `CPU` |
| `FFMPEG_PATH` | `ffmpeg` | Lệnh hoặc đường dẫn tuyệt đối tới `ffmpeg.exe` |
| `LOCALSUB_MODEL_DIR` | `<repo>\data\models` | Thư mục model |
| `LOCALSUB_OUTPUT_DIR` | `<repo>\data\output` | Thư mục kết quả |
| `LOCALSUB_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOCALSUB_OVERLAP` | bật | `0` tắt chạy chồng nhận dạng/dịch; `force` cho phép cả khi cùng thiết bị (chỉ để đo) |
| `LOCALSUB_TRANSLATION_PARALLEL` | tự động (NPU: `1`) | Cố định số lô dịch mỗi lần gọi model (1–16) và tắt tự điều chỉnh. Không đặt: GPU/CPU tự chọn 4/8/16, NPU dùng 1 |
| `LOCALSUB_FULL_SPEED` | tắt | `1` để worker không bị EcoQoS (ép CPU tốc độ tối đa); chỉ bật nếu đo thấy nhanh hơn |
| `LOCALSUB_KEEP_AWAKE` | bật | `0` cho phép Windows sleep/hạ tốc độ worker khi đang xử lý (giống `--allow-sleep`) |
| `LOCALSUB_KEEP_DISPLAY_ON` | tắt | `1` giữ màn hình sáng trong lúc xử lý |
| `LOCALSUB_VLM_CACHE_GB` | `2` | Bộ nhớ cache (GB) cho model Qwen3.5 khi dịch theo lô |
| `LOCALSUB_MCP_TOKEN` | khóa lưu trong `mcp\http-token` | Khóa API cho `mcp --http` (tối thiểu 24 ký tự) |
| `LOCALSUB_MCP_ALLOWED_ORIGINS` | trống | Danh sách Origin thêm, phân tách bằng dấu phẩy, cho `mcp --http` |
| `LOCALSUB_WORKER_DIR` | tự dò | App desktop: thư mục chứa `app\main.py` |
| `LOCALSUB_WORKER_PYTHON` | `<worker>\.venv\Scripts\python.exe` | App desktop: Python dùng để chạy worker |

---

## 12. Xử lý sự cố

| Hiện tượng | Cách xử lý |
|---|---|
| `FFMPEG_UNAVAILABLE` | Cài FFmpeg (`winget install Gyan.FFmpeg`), mở lại terminal, hoặc đặt `FFMPEG_PATH` |
| `OPENVINO_UNAVAILABLE` | Chạy lại `.\scripts\setup-wave2.ps1` |
| Không thấy `NPU` trong diagnostics | Cập nhật Intel NPU driver, khởi động lại, kiểm tra Task Manager có NPU 0 |
| Task Manager có NPU 0 nhưng OpenVINO chỉ thấy `CPU, GPU` | Chạy lại với `$env:ZE_ENABLE_LOADER_DEBUG_TRACE="1"; $env:OV_NPU_LOG_LEVEL="LOG_DEBUG"`. Nếu log báo `npu_level_zero_umd.dll ... could not be found` (đường dẫn tới thư mục driver đã bị xóa): khởi động lại máy (Restart, không phải Shut down), rồi `pnputil /restart-device <InstanceId>` bằng quyền Admin, cuối cùng cài lại driver Intel NPU |
| `ZE_RESULT_ERROR_DEVICE_LOST` / `DEVICE_LOST` khi đang nhận dạng | Driver NPU bị reset giữa chừng. Từ 0.9.1 app tự nạp lại Whisper trên NPU và **chạy tiếp từ đoạn 5 phút bị lỗi** (giữ các đoạn đã xong). Nếu lỗi lặp lại và thiết bị là **Tự động**, app chuyển sang GPU (kể cả khi GPU đang dịch chạy chồng), cuối cùng là CPU; thời điểm và thiết bị mới hiện ở *Môi trường chạy* và ghi trong `asr_recoveries` của `jobs.jsonl`. Nếu đã chọn cố định NPU, app chỉ thử lại trên NPU một lần rồi báo `DEVICE_LOST`. Nguyên nhân gốc là lỗi driver NPU Lunar Lake 32.0.100.5540 đã được báo cáo ([openvino#38403](https://github.com/openvinotoolkit/openvino/issues/38403)): NPU treo ngẫu nhiên khi chạy liên tục, chưa có cách khắc phục bằng phần mềm. Nếu gặp thường xuyên: chọn **GPU** cho nhận dạng, hoặc thử cài driver cũ hơn (ví dụ 32.0.100.4841 hay 32.0.100.4512 từ trang Intel), rồi Restart |
| `DEVICE_UNAVAILABLE` khi chọn NPU | Dùng `-Device auto` để xem lý do chuyển thiết bị trong kết quả |
| Lần đầu chạy NPU rất lâu | Bình thường (biên dịch model). Không xóa `data\models\.ov-cache` |
| `MODEL_NOT_FOUND` / app hiện "Cần tải model trước" | Tải model trong mục **Model** (hoặc `models download <id>`) |
| `MODEL_INCOMPLETE` / model hiện "Tải dở" | Bấm **Tải tiếp** (hoặc chạy lại `models download <id>`); phần đã tải được giữ lại |
| `DOWNLOAD_FAILED` | Mạng bị ngắt khi tải; kiểm tra mạng/proxy rồi bấm **Tải tiếp** |
| `INSUFFICIENT_DISK` | Giải phóng ổ đĩa, hoặc đặt `LOCALSUB_MODEL_DIR` sang ổ khác |
| App desktop báo không tìm thấy worker | Kiểm tra `apps\inference\.venv` tồn tại, hoặc đặt `LOCALSUB_WORKER_DIR` / `LOCALSUB_WORKER_PYTHON` |
| `npm run tauri dev` lỗi linker `link.exe` | Cài Visual Studio Build Tools với workload C++ (mục 2) |
| Bản dịch sai hoặc giữ nguyên câu gốc | Dùng model 7–8B; xem `translation_fallback_lines` trong nhật ký; sửa tay trong bảng |
| Phụ đề tiếng Nhật sai chữ Hán (ví dụ 決産 thay cho 決算) | Dùng `whisper-large-v3-turbo-int8` hoặc `whisper-medium-int8`; tiny/base/small hay nhầm đồng âm |
| Thiếu RAM khi dịch | Chọn `qwen3-4b-int4`, đóng ứng dụng khác |
| Script bị chặn | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` |

Chi tiết thêm: [docs/troubleshooting.md](docs/troubleshooting.md).

---

## 13. Dành cho nhà phát triển

### Kiến trúc

```text
Tauri 2 desktop (React)  --invoke-->  Lệnh Rust (kiểm tra tham số theo danh sách cho phép, không dùng shell)
        ^                                   | chạy: localsub-worker.exe (bản cài) hoặc python -m app.main (mã nguồn)
        +---- job-event / job-log / job-exit <-+ stdout JSON Lines, stderr log

Python worker
  probing -> extracting_audio (FFmpeg) -> loading_model -> transcribing (Whisper, NPU/GPU/CPU)
  -> translating (LLM, tùy chọn) -> rendering (SRT/VTT/TXT/JSON) -> completed | failed
```

Thiết kế chi tiết: [docs/architecture.md](docs/architecture.md).

### Kiểm thử

```powershell
# Python (unit test, không cần model)
cd apps\inference; .\.venv\Scripts\python.exe -m pytest; .\.venv\Scripts\python.exe -m ruff check .

# Test tích hợp với model thật
$env:LOCALSUB_IT_MEDIA="D:\clip.mp4"; $env:LOCALSUB_IT_MODEL="whisper-base-int8"
.\.venv\Scripts\python.exe -m pytest -k real_whisper -s
$env:LOCALSUB_IT_LLM="qwen3-8b-int4-cw"; .\.venv\Scripts\python.exe -m pytest -k real_translation -s

# Giao diện và Rust
cd apps\desktop; npm run lint; npm run typecheck; npm test; npm run build
cd src-tauri; cargo test
```

CI (GitHub Actions) chạy Python lint/test, frontend lint/type-check/test/build, và `cargo check` + `cargo test` trên `windows-latest`.

### Trạng thái kiểm chứng

- 88 unit test Python (3.11 và 3.14), 24 test Vitest, 6 test Rust (thêm 2 test chạy worker thật: mã nguồn và bản đóng gói) đều đạt.
- Worker đóng gói bằng PyInstaller (Linux, OpenVINO 2026.4, ~313 MB): diagnostics, models list, nhận dạng tiếng Nhật
  và dịch Nhật→Việt cho kết quả giống hệt bản chạy từ mã nguồn. Bộ cài Windows được build và cài thử (silent) trong
  workflow Release trên `windows-latest`.
- Đã tải thật `whisper-base-int8`: ngắt giữa chừng → trạng thái `incomplete` và job báo `MODEL_INCOMPLETE` →
  chạy lại tải tiếp → `installed` → nhận dạng thành công. Kiểm tra dung lượng ổ đĩa đã chặn đúng khi ổ gần đầy.
- Kiểm tra giao diện bằng Playwright (bản xem trước): tải/dừng/xóa model, lịch sử, đổi VI/EN, màn hình 390 px.
- Đã chạy thật trên CPU (OpenVINO 2026.4): video 44 giây → 8 câu tiếng Anh → dịch tiếng Việt, xuất đủ 4 định dạng.
- Đã chạy thật trên CPU: audio tiếng Nhật 19 giây, `--language auto` nhận đúng `ja`, whisper-small + qwen3-4b dịch sang tiếng Việt và tiếng Anh, 0 dòng fallback.
- 0.9.0: pytest 132 đạt / 2 bỏ qua, Vitest 38 đạt; Qwen3.5 0.8B nạp thật qua `ContinuousBatchingPipeline` trên CPU (OpenVINO 2026.4) và dịch 32 câu, 0 dòng fallback. Giữ máy thức kiểm bằng Windows API giả lập (chưa đo trên máy thật).
- **Chưa kiểm chứng:** chạy trên NPU vật lý (Whisper và model dịch 8B) và `npm run tauri dev` trên Windows.

---

## 14. Bảo mật và quyền riêng tư

- Media, âm thanh tạm, model và phụ đề đều nằm trên máy. Không dùng dịch vụ nhận dạng/dịch trên cloud.
- Kết nối mạng duy nhất là tải model từ Hugging Face (`OpenVINO/whisper-*-ov`, `OpenVINO/Qwen*-ov`) khi bạn chạy lệnh tải.
- Ứng dụng desktop chỉ mở các lệnh được cho phép; mọi giá trị (ngôn ngữ, thiết bị, model, định dạng) được kiểm tra trong Rust
  trước khi chạy worker bằng mảng tham số. Quyền Tauri giới hạn ở `core:default` và `dialog:default`.
- Nhật ký không chứa nội dung phụ đề. Không đưa video, model, token hay mật khẩu vào repository.
- MCP server (0.11.0) chỉ đọc, chạy qua stdio (không mở cổng), mặc định ẩn tên file và đường dẫn khỏi trợ lý AI.
- Chế độ HTTP cho Perplexity (0.12.0) chỉ bật khi bạn xác nhận: nghe trên `127.0.0.1`, cần khóa API, có giới hạn tần suất và
  nhật ký truy cập. Đường hầm HTTPS công khai chỉ mở khi bạn bấm và tự đóng khi tắt server hoặc thoát app.

---

## 15. Lộ trình

1. Wave 1: khung ứng dụng, chẩn đoán, trình tạo SRT. ✅
2. Wave 2: tách âm thanh FFmpeg và nhận dạng Whisper trên OpenVINO ra SRT (dòng lệnh). ✅
3. Wave 3: app desktop nối với worker, dịch Anh↔Việt bằng LLM trên máy, bảng sửa phụ đề, xuất VTT/TXT/JSON. ✅
4. Wave 4: ✅
   - 4A: quản lý model trong app (tải/tiếp tục/xóa, kiểm tra toàn vẹn), lịch sử xử lý, giao diện tiếng Việt.
   - 4B: bộ cài Windows NSIS kèm worker đóng gói sẵn (PyInstaller), workflow Release trên GitHub.
5. Wave 5A: dịch song song, dịch lại theo nhóm, nhận dạng theo đoạn có tiến độ, `auto` dịch ưu tiên GPU, chart tải CPU/GPU/NPU, lịch sử tách thời gian nhận dạng/dịch. ✅
6. Wave 5B: giao diện gọn một màn hình có tab. ✅ (chế độ GPU+NPU thử nghiệm ở 0.7.0 đã bỏ ở 0.8.0)
7. Wave 6A: GPU tự điều chỉnh 4/8/16 lô mỗi lần, lùi mức khi lỗi bộ nhớ. ✅
8. Wave 6B: nhận dạng (NPU) và dịch (GPU) chạy chồng theo từng đoạn, nạp model dịch song song. ✅
9. Wave 7: giữ máy thức + tắt EcoQoS khi xử lý, danh mục model dùng chung, thêm Qwen3.5 (GPU/CPU) và Distil-Whisper EN (thử nghiệm). ✅
10. 0.9.2: khóa runtime OpenVINO 2026.4.0 (`apps/inference/constraints.txt`, giống 0.8.0), EcoQoS opt-out thành tùy chọn. ✅
11. 0.10.0: tiếng Hàn là ngôn ngữ nói (nhận dạng `ko`, dịch Hàn → Việt/Anh, cắt câu theo chuẩn phụ đề tiếng Hàn). ✅
12. 0.11.0: tab Phân tích (AI) và MCP server chỉ đọc (log, chẩn đoán, phân tích hiệu suất, kế hoạch tối ưu cho AI lập trình). ✅
13. 0.12.0: kết nối Perplexity — MCP qua Streamable HTTP (khóa API, kiểm tra Origin, giới hạn tần suất, nhật ký truy cập) và đường hầm HTTPS Cloudflare ngay trong app. ✅
14. Tiếp theo (backlog): MCP giai đoạn 2 (chạy benchmark/đổi cấu hình có xác nhận), đường hầm có tên để URL cố định, OAuth cho connector, tăng tốc nạp model, đo tốc độ Qwen3.5 trên GPU Arc, chẩn đoán rõ lỗi driver NPU, preset tốc độ/chất lượng, benchmark thiết bị ngay trong app, ký số bộ cài, kèm FFmpeg trong bộ cài, tự cập nhật.

## Giấy phép

MIT. Không kèm file model; mỗi model phải được tải và sử dụng theo giấy phép riêng của nó.
