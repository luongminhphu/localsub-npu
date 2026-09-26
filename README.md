# LocalSub NPU

Ứng dụng desktop Windows 11 tạo phụ đề cho video/audio **hoàn toàn offline**, ưu tiên chạy trên **Intel NPU**
(Intel AI Boost) qua OpenVINO, tự chuyển sang GPU/CPU khi cần và ghi lại lý do.

- Nhận dạng giọng nói **tiếng Việt / tiếng Anh / tiếng Nhật** bằng Whisper (OpenVINO GenAI).
- Dịch phụ đề **Anh ↔ Việt**, **Nhật → Anh**, **Nhật → Việt** bằng mô hình ngôn ngữ chạy trên máy (Qwen INT4), giữ nguyên mốc thời gian.
- Sửa phụ đề trực tiếp trong bảng, xuất **SRT, VTT, TXT** và file dự án `.localsub.json`.
- Media không bao giờ rời khỏi máy. Sau khi tải model lần đầu, không cần Internet và không cần API key.

> Phiên bản hiện tại: **0.4.0 (Wave 3 + tiếng Nhật)**. Chưa có bộ cài (installer); chạy từ mã nguồn theo hướng dẫn bên dưới.

---

## Mục lục

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
| Phần mềm | Git, Python 3.11, FFmpeg; thêm Node.js 22, Rust, Visual Studio C++ Build Tools nếu dùng app desktop |

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

Model được lưu trong `data\models\<model-id>` (đã nằm trong `.gitignore`). Có thể tải thêm bất cứ lúc nào:

```powershell
cd apps\inference
.\.venv\Scripts\python.exe -m app.main models list                         # xem model và trạng thái đã cài
.\.venv\Scripts\python.exe -m app.main models download whisper-large-v3-turbo-int8
.\.venv\Scripts\python.exe -m app.main models download qwen3-8b-int4-cw
cd ..\..
```

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

Đóng gói thành bộ cài MSI/NSIS để dùng hằng ngày:

```powershell
npm run tauri build    # kết quả: apps\desktop\src-tauri\target\release\bundle\
```

Ứng dụng tự tìm worker tại `apps\inference\.venv\Scripts\python.exe` bằng cách dò ngược từ vị trí file chạy.
Nếu cài bản đóng gói ra thư mục khác, đặt biến môi trường trỏ về repo:

```powershell
[Environment]::SetEnvironmentVariable("LOCALSUB_WORKER_DIR", "D:\Projects\localsub-npu\apps\inference", "User")
[Environment]::SetEnvironmentVariable("LOCALSUB_WORKER_PYTHON", "D:\Projects\localsub-npu\apps\inference\.venv\Scripts\python.exe", "User")
```

`npm run dev` mở giao diện trong trình duyệt ở chế độ **xem trước với dữ liệu mẫu** (không chạy model), chỉ dùng để xem giao diện.

---

## 7. Sử dụng ứng dụng desktop

Giao diện hiện đang bằng tiếng Anh. Quy trình gồm 3 bảng bên trái và bảng sửa phụ đề bên phải.

### Bước 1 — Media

- Bấm **Choose video or audio** và chọn file (MP4, MKV, MOV, AVI, WEBM, MP3, WAV, M4A).
- Hoặc bấm **Open saved project (.localsub.json)** để mở lại phụ đề đã lưu và sửa tiếp.

### Bước 2 — Configure

| Mục | Chọn |
|---|---|
| **Spoken language** | Ngôn ngữ nói trong video: `Vietnamese`, `English`, `Japanese` hoặc `Auto detect` |
| **Speech model · device** | Model Whisper và thiết bị (`auto`, `NPU`, `GPU`, `CPU`) |
| **Translate to** | `No translation`, hoặc ngôn ngữ đích (Việt/Anh) để tạo thêm bản dịch |
| **Translation model · device** | Model dịch và thiết bị (hiện khi có chọn dịch) |
| **Export formats** | Tick SRT / VTT / TXT / JSON |

Nếu model chưa được tải, ứng dụng hiện dòng **Download first: …** kèm lệnh cần chạy, và nút chạy bị khóa.

### Bước 3 — Runtime

Hiện phiên bản OpenVINO, FFmpeg, danh sách thiết bị khả dụng và thiết bị mà `auto` sẽ chọn.
Bấm **Re-check** sau khi cài driver hoặc tải model.

### Chạy

- Bấm **Generate subtitles**. Thanh tiến trình đi qua các bước: kiểm tra file → tách âm thanh → nạp model →
  nhận dạng → dịch (hiện số câu đã dịch / tổng) → ghi file.
- Khi xong, ứng dụng hiện **Speech used** và **Translation used** — thiết bị thực tế đã chạy (kèm lý do nếu phải chuyển sang GPU/CPU).
- Bấm **Cancel** để dừng; file cũ không bị thay đổi.

### Sửa phụ đề

- Sửa trực tiếp cột thời gian (**Start**, **End**, định dạng `hh:mm:ss,mmm`), **Source** và **Translation**.
- **Split**: tách câu làm đôi tại điểm giữa. **Merge**: gộp với câu kế tiếp. **Delete**: xóa câu.
- Ứng dụng kiểm tra liên tục: thời gian chồng nhau, End trước Start, câu trống, câu dài hơn 84 ký tự
  (tiếng Nhật: 32 ký tự).
  Lỗi nghiêm trọng sẽ chặn nút xuất; dòng có lỗi được tô màu.
- Nhãn **unsaved** cho biết có thay đổi chưa xuất.

### Xuất file

Bấm **Export SRT + JSON …** (tên nút thay đổi theo định dạng đã chọn), chọn thư mục và tên file.
Mỗi ngôn ngữ ra một file riêng, ví dụ `talk.en.srt`, `talk.vi.srt`, kèm `talk.localsub.json` để mở lại sau.

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
| `-Language` | `auto` | `auto`, `vi`, `en`, `ja` |
| `-Task` | `transcribe` | `transcribe`, hoặc `translate` (Whisper → tiếng Anh) |
| `-Device` | `auto` | `auto`, `NPU`, `GPU`, `CPU` cho Whisper |
| `-Model` | `whisper-small-int8` | Model Whisper |
| `-TranslateTo` | (không) | `vi` hoặc `en` — thêm bản dịch bằng model dịch (tiếng Nhật chỉ là ngôn ngữ nguồn) |
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

### Dịch (LLM)

| Model | Dung lượng | Dùng khi |
|---|---|---|
| `qwen3-8b-int4-cw` | ~5 GB | **Mặc định**, định dạng INT4 channel-wise tối ưu cho NPU |
| `qwen2.5-7b-instruct-int4` | ~4.5 GB | Dịch Anh–Việt tốt, nhanh trên GPU Arc |
| `qwen3-4b-int4` | ~2.5 GB | Máy ít RAM |
| `qwen2.5-1.5b-instruct-int4` | ~1 GB | Bản nháp nhanh, cần soát kỹ |
| `qwen3-0.6b-int4` | ~0.5 GB | Chỉ để kiểm tra luồng chạy; chất lượng dịch kém |

### Tiếng Nhật

- Chọn `Japanese` (hoặc `-Language ja`) thay vì `Auto detect` khi biết chắc video tiếng Nhật; tên file sẽ là `<tên>.ja.srt`.
- Nên dùng `whisper-large-v3-turbo-int8` (hoặc `medium`). Ứng dụng hiện cảnh báo khi chọn tiny/base/small cho tiếng Nhật.
- Phụ đề tiếng Nhật được cắt theo ký tự (tối đa 32 ký tự/câu), ưu tiên ngắt tại `。`, `、`, `！`, `？` hoặc khoảng trắng Whisper chèn giữa các câu.
- Dịch Nhật → Anh/Việt: nên dùng `qwen3-8b-int4-cw` hoặc `qwen3-4b-int4`. Prompt dịch có quy tắc riêng: câu tiếng Nhật thường bị chia qua nhiều dòng, model dùng ngữ cảnh nhưng vẫn giữ **một dòng dịch cho mỗi dòng gốc**, giữ tên riêng, chuyển kính ngữ (さん, 様) cho tự nhiên.
- Bản dịch lỗi của model nhỏ (lặp `1. 1. 1.`, trả về khung mẫu `N. text`) bị loại, dịch lại từng dòng, nếu vẫn lỗi thì giữ câu gốc và tăng `translation_fallback_lines`.

### Thiết bị

- `auto`: thử **NPU → GPU → CPU**, dùng thiết bị đầu tiên chạy được và ghi lý do chuyển.
- `NPU` / `GPU` / `CPU`: bắt buộc thiết bị đó; nếu không có sẽ báo lỗi `DEVICE_UNAVAILABLE`, không tự đổi.
- Whisper được giải phóng khỏi bộ nhớ trước khi nạp model dịch, nên có thể chạy cả hai trên NPU.
- Gợi ý bắt đầu: Whisper trên `NPU`, dịch trên `auto`. So sánh bằng `benchmark-devices.ps1` rồi cố định thiết bị nhanh nhất.

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
| `LOCALSUB_WORKER_DIR` | tự dò | App desktop: thư mục chứa `app\main.py` |
| `LOCALSUB_WORKER_PYTHON` | `<worker>\.venv\Scripts\python.exe` | App desktop: Python dùng để chạy worker |

---

## 12. Xử lý sự cố

| Hiện tượng | Cách xử lý |
|---|---|
| `FFMPEG_UNAVAILABLE` | Cài FFmpeg (`winget install Gyan.FFmpeg`), mở lại terminal, hoặc đặt `FFMPEG_PATH` |
| `OPENVINO_UNAVAILABLE` | Chạy lại `.\scripts\setup-wave2.ps1` |
| Không thấy `NPU` trong diagnostics | Cập nhật Intel NPU driver, khởi động lại, kiểm tra Task Manager có NPU 0 |
| `DEVICE_UNAVAILABLE` khi chọn NPU | Dùng `-Device auto` để xem lý do chuyển thiết bị trong kết quả |
| Lần đầu chạy NPU rất lâu | Bình thường (biên dịch model). Không xóa `data\models\.ov-cache` |
| `MODEL_NOT_FOUND` / app hiện "Download first" | Chạy lệnh `models download <id>` được gợi ý |
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
        ^                                   | chạy: python -m app.main transcribe ... --events
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

- 55 unit test Python (3.11 và 3.14), 12 test Vitest, test Rust (bao gồm chạy worker thật) đều đạt.
- Đã chạy thật trên CPU (OpenVINO 2026.4): video 44 giây → 8 câu tiếng Anh → dịch tiếng Việt, xuất đủ 4 định dạng.
- Đã chạy thật trên CPU: audio tiếng Nhật 19 giây, `--language auto` nhận đúng `ja`, whisper-small + qwen3-4b dịch sang tiếng Việt và tiếng Anh, 0 dòng fallback.
- **Chưa kiểm chứng:** chạy trên NPU vật lý (Whisper và model dịch 8B) và `npm run tauri dev` trên Windows.

---

## 14. Bảo mật và quyền riêng tư

- Media, âm thanh tạm, model và phụ đề đều nằm trên máy. Không dùng dịch vụ nhận dạng/dịch trên cloud.
- Kết nối mạng duy nhất là tải model từ Hugging Face (`OpenVINO/whisper-*-ov`, `OpenVINO/Qwen*-ov`) khi bạn chạy lệnh tải.
- Ứng dụng desktop chỉ mở các lệnh được cho phép; mọi giá trị (ngôn ngữ, thiết bị, model, định dạng) được kiểm tra trong Rust
  trước khi chạy worker bằng mảng tham số. Quyền Tauri giới hạn ở `core:default` và `dialog:default`.
- Nhật ký không chứa nội dung phụ đề. Không đưa video, model, token hay mật khẩu vào repository.

---

## 15. Lộ trình

1. Wave 1: khung ứng dụng, chẩn đoán, trình tạo SRT. ✅
2. Wave 2: tách âm thanh FFmpeg và nhận dạng Whisper trên OpenVINO ra SRT (dòng lệnh). ✅
3. Wave 3: app desktop nối với worker, dịch Anh↔Việt bằng LLM trên máy, bảng sửa phụ đề, xuất VTT/TXT/JSON. ✅
4. Wave 4: bộ cài kèm Python runtime, giao diện tiếng Việt, benchmark, giám sát và hoàn thiện phát hành.

## Giấy phép

MIT. Không kèm file model; mỗi model phải được tải và sử dụng theo giấy phép riêng của nó.
