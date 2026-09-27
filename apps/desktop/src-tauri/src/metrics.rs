//! System load sampler for the in-app CPU / GPU / NPU monitor.
//!
//! On Windows a background thread samples performance counters once per second (the same
//! counters Task Manager uses): CPU from `\Processor Information(_Total)\% Processor Utility`,
//! GPU and NPU from `\GPU Engine(*)\Utilization Percentage`, split per adapter LUID. DXCore
//! tells which LUID is a graphics adapter (GPU) and which is compute-only (NPU). The thread
//! stops by itself when the UI stops polling, so an idle app costs nothing.

use std::collections::{HashMap, VecDeque};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

pub const INTERVAL_MS: u64 = 1_000;
pub const HISTORY: usize = 300; // 5 minutes at 1 Hz
const IDLE_STOP: Duration = Duration::from_secs(60);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum AdapterKind {
    Gpu,
    Npu,
}

#[derive(Clone, Debug)]
pub struct Adapter {
    /// Lower-case `0xHIGH_0xLOW`, the form used inside `GPU Engine` instance names.
    pub luid: String,
    pub name: String,
    pub kind: AdapterKind,
}

#[derive(Clone, Debug, Default, PartialEq, serde::Serialize)]
pub struct Sample {
    pub at_ms: u64,
    pub cpu: Option<f64>,
    pub gpu: Option<f64>,
    pub npu: Option<f64>,
    pub mem_used_bytes: Option<u64>,
    pub mem_total_bytes: Option<u64>,
}

#[derive(Clone, Debug, serde::Serialize)]
pub struct Snapshot {
    /// `windows-pdh` when counters are live, `unsupported` elsewhere, `starting` before the
    /// first sample, `error` when the counters could not be opened.
    pub source: String,
    pub interval_ms: u64,
    pub gpu_name: Option<String>,
    pub npu_name: Option<String>,
    pub error: Option<String>,
    pub samples: Vec<Sample>,
}

pub fn luid_key(high: i32, low: u32) -> String {
    format!("0x{:08x}_0x{:08x}", high as u32, low)
}

/// `pid_1234_luid_0x00000000_0x0000D1A2_phys_0_eng_3_engtype_3D` →
/// (`0x00000000_0x0000d1a2`, `phys_0_eng_3`, `3D`).
pub fn parse_engine_instance(name: &str) -> Option<(String, String, String)> {
    let lower = name.to_ascii_lowercase();
    let luid_at = lower.find("luid_")? + 5;
    let phys_at = lower[luid_at..].find("_phys_")? + luid_at;
    let luid = lower[luid_at..phys_at].to_string();
    let engtype_at = lower.find("_engtype_")?;
    let engine = lower[phys_at + 1..engtype_at].to_string();
    let engtype = name[engtype_at + 9..].to_string();
    if luid.is_empty() || engine.is_empty() {
        return None;
    }
    Some((luid, engine, engtype))
}

/// Task Manager semantics: an engine's load is the sum over processes, an adapter's load is
/// its busiest engine. Returns (gpu, npu); `None` when no adapter of that kind exists.
pub fn aggregate(items: &[(String, f64)], adapters: &[Adapter]) -> (Option<f64>, Option<f64>) {
    let mut per_engine: HashMap<(String, String), f64> = HashMap::new();
    let mut has_3d: HashMap<String, bool> = HashMap::new();
    for (name, value) in items {
        if let Some((luid, engine, engtype)) = parse_engine_instance(name) {
            if engtype.eq_ignore_ascii_case("3d") {
                has_3d.insert(luid.clone(), true);
            } else {
                has_3d.entry(luid.clone()).or_insert(false);
            }
            *per_engine.entry((luid, engine)).or_insert(0.0) += value.max(0.0);
        }
    }
    let mut per_adapter: HashMap<String, f64> = HashMap::new();
    for ((luid, _), value) in per_engine {
        let slot = per_adapter.entry(luid).or_insert(0.0);
        *slot = slot.max(value);
    }
    let kind_of = |luid: &str| -> Option<AdapterKind> {
        if adapters.is_empty() {
            // No DXCore: graphics adapters expose a 3D engine, compute-only NPUs do not.
            return has_3d.get(luid).map(|&g| {
                if g {
                    AdapterKind::Gpu
                } else {
                    AdapterKind::Npu
                }
            });
        }
        adapters.iter().find(|a| a.luid == luid).map(|a| a.kind)
    };
    let known = |kind: AdapterKind| -> bool {
        if adapters.is_empty() {
            has_3d.keys().any(|l| kind_of(l) == Some(kind))
        } else {
            adapters.iter().any(|a| a.kind == kind)
        }
    };
    let pick = |kind: AdapterKind| -> Option<f64> {
        if !known(kind) {
            return None;
        }
        let best = per_adapter
            .iter()
            .filter(|(l, _)| kind_of(l) == Some(kind))
            .map(|(_, v)| *v)
            .fold(0.0_f64, f64::max);
        Some(round1(best.clamp(0.0, 100.0)))
    };
    (pick(AdapterKind::Gpu), pick(AdapterKind::Npu))
}

pub fn round1(value: f64) -> f64 {
    (value * 10.0).round() / 10.0
}

#[cfg_attr(not(windows), allow(dead_code))]
fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

pub trait Collector: Send {
    fn adapters(&self) -> &[Adapter];
    fn sample(&mut self) -> Result<Sample, String>;
}

struct Inner {
    running: bool,
    last_poll: Instant,
    source: String,
    error: Option<String>,
    gpu_name: Option<String>,
    npu_name: Option<String>,
    samples: VecDeque<Sample>,
}

#[derive(Clone)]
pub struct Monitor {
    inner: Arc<Mutex<Inner>>,
}

impl Default for Monitor {
    fn default() -> Self {
        Self {
            inner: Arc::new(Mutex::new(Inner {
                running: false,
                last_poll: Instant::now(),
                source: "starting".into(),
                error: None,
                gpu_name: None,
                npu_name: None,
                samples: VecDeque::with_capacity(HISTORY),
            })),
        }
    }
}

impl Monitor {
    /// Returns samples newer than `since_ms` (all when `None`) and starts the sampler if needed.
    pub fn snapshot(&self, since_ms: Option<u64>) -> Snapshot {
        self.ensure_running(open_collector);
        let mut inner = self.inner.lock().unwrap_or_else(|p| p.into_inner());
        inner.last_poll = Instant::now();
        Snapshot {
            source: inner.source.clone(),
            interval_ms: INTERVAL_MS,
            gpu_name: inner.gpu_name.clone(),
            npu_name: inner.npu_name.clone(),
            error: inner.error.clone(),
            samples: inner
                .samples
                .iter()
                .filter(|s| since_ms.map_or(true, |t| s.at_ms > t))
                .cloned()
                .collect(),
        }
    }

    pub fn ensure_running<F>(&self, open: F)
    where
        F: FnOnce() -> Result<Box<dyn Collector>, String> + Send + 'static,
    {
        {
            let mut inner = self.inner.lock().unwrap_or_else(|p| p.into_inner());
            if inner.running || inner.source == "unsupported" {
                return;
            }
            inner.running = true;
            inner.last_poll = Instant::now();
        }
        let shared = Arc::clone(&self.inner);
        thread::spawn(move || {
            let mut collector = match open() {
                Ok(c) => c,
                Err(e) => {
                    let mut inner = shared.lock().unwrap_or_else(|p| p.into_inner());
                    inner.running = false;
                    inner.source = if e == "unsupported" {
                        "unsupported".into()
                    } else {
                        "error".into()
                    };
                    inner.error = if e == "unsupported" { None } else { Some(e) };
                    return;
                }
            };
            {
                let mut inner = shared.lock().unwrap_or_else(|p| p.into_inner());
                let name = |k| {
                    collector
                        .adapters()
                        .iter()
                        .find(|a| a.kind == k)
                        .map(|a| a.name.clone())
                };
                inner.gpu_name = name(AdapterKind::Gpu);
                inner.npu_name = name(AdapterKind::Npu);
            }
            loop {
                thread::sleep(Duration::from_millis(INTERVAL_MS));
                let result = collector.sample();
                let mut inner = shared.lock().unwrap_or_else(|p| p.into_inner());
                match result {
                    Ok(sample) => {
                        inner.source = "windows-pdh".into();
                        inner.error = None;
                        if inner.samples.len() == HISTORY {
                            inner.samples.pop_front();
                        }
                        inner.samples.push_back(sample);
                    }
                    Err(e) => inner.error = Some(e),
                }
                if inner.last_poll.elapsed() > IDLE_STOP {
                    inner.running = false;
                    return;
                }
            }
        });
    }
}

#[cfg(not(windows))]
fn open_collector() -> Result<Box<dyn Collector>, String> {
    Err("unsupported".into())
}

#[cfg(windows)]
fn open_collector() -> Result<Box<dyn Collector>, String> {
    win::PdhCollector::open().map(|c| Box::new(c) as Box<dyn Collector>)
}

#[cfg(windows)]
mod win {
    use super::*;
    use windows::core::{GUID, PCWSTR};
    use windows::Win32::Foundation::LUID;
    use windows::Win32::Graphics::DXCore::{
        DXCoreCreateAdapterFactory, DriverDescription, IDXCoreAdapter, IDXCoreAdapterFactory,
        IDXCoreAdapterList, InstanceLuid, IsHardware, DXCORE_ADAPTER_ATTRIBUTE_D3D12_CORE_COMPUTE,
        DXCORE_ADAPTER_ATTRIBUTE_D3D12_GRAPHICS,
    };
    use windows::Win32::System::Performance::{
        PdhAddEnglishCounterW, PdhCloseQuery, PdhCollectQueryData, PdhGetFormattedCounterArrayW,
        PdhGetFormattedCounterValue, PdhOpenQueryW, PDH_FMT_COUNTERVALUE,
        PDH_FMT_COUNTERVALUE_ITEM_W, PDH_FMT_DOUBLE, PDH_HCOUNTER, PDH_HQUERY, PDH_MORE_DATA,
    };
    use windows::Win32::System::SystemInformation::{GlobalMemoryStatusEx, MEMORYSTATUSEX};

    /// `DXCORE_ADAPTER_ATTRIBUTE_D3D12_GENERIC_ML` (dxcore_interface.h); NPUs expose this.
    const GENERIC_ML: GUID = GUID::from_u128(0xb71b0d41_1088_422f_a27c_0250b7d3a988);

    fn wide(s: &str) -> Vec<u16> {
        s.encode_utf16().chain(std::iter::once(0)).collect()
    }

    pub fn enumerate_adapters() -> Vec<Adapter> {
        let mut found: Vec<Adapter> = Vec::new();
        let Ok(factory) = (unsafe { DXCoreCreateAdapterFactory::<IDXCoreAdapterFactory>() }) else {
            return found;
        };
        for attribute in [
            DXCORE_ADAPTER_ATTRIBUTE_D3D12_GRAPHICS,
            DXCORE_ADAPTER_ATTRIBUTE_D3D12_CORE_COMPUTE,
            GENERIC_ML,
        ] {
            let Ok(list) =
                (unsafe { factory.CreateAdapterList::<IDXCoreAdapterList>(&[attribute]) })
            else {
                continue; // older Windows builds reject unknown attributes
            };
            for i in 0..unsafe { list.GetAdapterCount() } {
                let Ok(adapter) = (unsafe { list.GetAdapter::<IDXCoreAdapter>(i) }) else {
                    continue;
                };
                if let Some(a) = describe(&adapter) {
                    if !found.iter().any(|f| f.luid == a.luid) {
                        found.push(a);
                    }
                }
            }
        }
        found
    }

    fn describe(adapter: &IDXCoreAdapter) -> Option<Adapter> {
        unsafe {
            let mut hardware: u8 = 0;
            if adapter.IsPropertySupported(IsHardware)
                && adapter
                    .GetProperty(IsHardware, 1, &mut hardware as *mut u8 as *mut _)
                    .is_ok()
                && hardware == 0
            {
                return None; // Microsoft Basic Render Driver and other software adapters
            }
            let mut luid = LUID::default();
            adapter
                .GetProperty(
                    InstanceLuid,
                    std::mem::size_of::<LUID>(),
                    &mut luid as *mut LUID as *mut _,
                )
                .ok()?;
            let size = adapter.GetPropertySize(DriverDescription).unwrap_or(0);
            let mut name = vec![0u8; size.max(1)];
            let _ = adapter.GetProperty(DriverDescription, name.len(), name.as_mut_ptr() as *mut _);
            let name = String::from_utf8_lossy(&name)
                .trim_end_matches('\0')
                .trim()
                .to_string();
            let graphics = adapter.IsAttributeSupported(&DXCORE_ADAPTER_ATTRIBUTE_D3D12_GRAPHICS);
            Some(Adapter {
                luid: luid_key(luid.HighPart, luid.LowPart),
                name,
                kind: if graphics {
                    AdapterKind::Gpu
                } else {
                    AdapterKind::Npu
                },
            })
        }
    }

    pub struct PdhCollector {
        query: PDH_HQUERY,
        cpu: PDH_HCOUNTER,
        engines: PDH_HCOUNTER,
        adapters: Vec<Adapter>,
    }

    // PDH handles are used from the sampler thread only.
    unsafe impl Send for PdhCollector {}

    impl PdhCollector {
        pub fn open() -> Result<Self, String> {
            let mut query = PDH_HQUERY::default();
            let status = unsafe { PdhOpenQueryW(PCWSTR::null(), 0, &mut query) };
            if status != 0 {
                return Err(format!("PdhOpenQuery failed: 0x{status:08x}"));
            }
            let add = |path: &str, counter: &mut PDH_HCOUNTER| -> u32 {
                let w = wide(path);
                unsafe { PdhAddEnglishCounterW(query, PCWSTR(w.as_ptr()), 0, counter) }
            };
            let mut cpu = PDH_HCOUNTER::default();
            // "% Processor Utility" matches Task Manager; older systems only have "% Processor Time".
            if add(
                r"\Processor Information(_Total)\% Processor Utility",
                &mut cpu,
            ) != 0
            {
                let status = add(r"\Processor(_Total)\% Processor Time", &mut cpu);
                if status != 0 {
                    unsafe { PdhCloseQuery(query) };
                    return Err(format!("CPU counter unavailable: 0x{status:08x}"));
                }
            }
            let mut engines = PDH_HCOUNTER::default();
            if add(r"\GPU Engine(*)\Utilization Percentage", &mut engines) != 0 {
                engines = PDH_HCOUNTER::default(); // no WDDM engines: GPU/NPU stay unknown
            }
            unsafe { PdhCollectQueryData(query) }; // prime rate counters
            Ok(Self {
                query,
                cpu,
                engines,
                adapters: enumerate_adapters(),
            })
        }

        fn engine_items(&self) -> Vec<(String, f64)> {
            if self.engines.is_invalid() {
                return Vec::new();
            }
            let mut size: u32 = 0;
            let mut count: u32 = 0;
            let status = unsafe {
                PdhGetFormattedCounterArrayW(
                    self.engines,
                    PDH_FMT_DOUBLE,
                    &mut size,
                    &mut count,
                    None,
                )
            };
            if status != PDH_MORE_DATA || size == 0 {
                return Vec::new();
            }
            // Items are followed by their name strings in the same buffer; u64 keeps alignment.
            let mut buffer = vec![0u64; (size as usize).div_ceil(8)];
            let items = buffer.as_mut_ptr() as *mut PDH_FMT_COUNTERVALUE_ITEM_W;
            let status = unsafe {
                PdhGetFormattedCounterArrayW(
                    self.engines,
                    PDH_FMT_DOUBLE,
                    &mut size,
                    &mut count,
                    Some(items),
                )
            };
            if status != 0 {
                return Vec::new();
            }
            let slice = unsafe { std::slice::from_raw_parts(items, count as usize) };
            slice
                .iter()
                .filter(|i| i.FmtValue.CStatus <= 1) // PDH_CSTATUS_VALID_DATA / NEW_DATA
                .map(|i| {
                    let name = unsafe { i.szName.to_string() }.unwrap_or_default();
                    (name, unsafe { i.FmtValue.Anonymous.doubleValue })
                })
                .collect()
        }
    }

    impl Drop for PdhCollector {
        fn drop(&mut self) {
            unsafe { PdhCloseQuery(self.query) };
        }
    }

    impl Collector for PdhCollector {
        fn adapters(&self) -> &[Adapter] {
            &self.adapters
        }

        fn sample(&mut self) -> Result<Sample, String> {
            let status = unsafe { PdhCollectQueryData(self.query) };
            if status != 0 {
                return Err(format!("PdhCollectQueryData failed: 0x{status:08x}"));
            }
            let mut value = PDH_FMT_COUNTERVALUE::default();
            let cpu = if unsafe {
                PdhGetFormattedCounterValue(self.cpu, PDH_FMT_DOUBLE, None, &mut value)
            } == 0
                && value.CStatus <= 1
            {
                Some(round1(
                    unsafe { value.Anonymous.doubleValue }.clamp(0.0, 100.0),
                ))
            } else {
                None
            };
            let (gpu, npu) = aggregate(&self.engine_items(), &self.adapters);
            let mut memory = MEMORYSTATUSEX {
                dwLength: std::mem::size_of::<MEMORYSTATUSEX>() as u32,
                ..Default::default()
            };
            let (used, total) = if unsafe { GlobalMemoryStatusEx(&mut memory) }.is_ok() {
                (
                    Some(memory.ullTotalPhys - memory.ullAvailPhys),
                    Some(memory.ullTotalPhys),
                )
            } else {
                (None, None)
            };
            Ok(Sample {
                at_ms: now_ms(),
                cpu,
                gpu,
                npu,
                mem_used_bytes: used,
                mem_total_bytes: total,
            })
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn adapters() -> Vec<Adapter> {
        vec![
            Adapter {
                luid: luid_key(0, 0xd1a2),
                name: "Intel(R) Arc(TM) 140V GPU".into(),
                kind: AdapterKind::Gpu,
            },
            Adapter {
                luid: luid_key(0, 0xe3f0),
                name: "Intel(R) AI Boost".into(),
                kind: AdapterKind::Npu,
            },
        ]
    }

    #[test]
    fn parses_engine_instance_names() {
        let parsed =
            parse_engine_instance("pid_1234_luid_0x00000000_0x0000D1A2_phys_0_eng_3_engtype_3D");
        assert_eq!(
            parsed,
            Some((
                "0x00000000_0x0000d1a2".into(),
                "phys_0_eng_3".into(),
                "3D".into()
            ))
        );
        assert_eq!(parse_engine_instance("garbage"), None);
        assert_eq!(luid_key(0, 0xd1a2), "0x00000000_0x0000d1a2");
        assert_eq!(luid_key(-1, 1), "0xffffffff_0x00000001");
    }

    #[test]
    fn aggregates_like_task_manager() {
        let items = vec![
            // two processes on the same GPU 3D engine add up; the busiest engine wins
            (
                "pid_1_luid_0x00000000_0x0000d1a2_phys_0_eng_0_engtype_3D".to_string(),
                20.0,
            ),
            (
                "pid_2_luid_0x00000000_0x0000d1a2_phys_0_eng_0_engtype_3D".to_string(),
                15.5,
            ),
            (
                "pid_2_luid_0x00000000_0x0000d1a2_phys_0_eng_1_engtype_Compute".to_string(),
                30.0,
            ),
            (
                "pid_3_luid_0x00000000_0x0000e3f0_phys_0_eng_0_engtype_Compute".to_string(),
                250.0,
            ),
        ];
        assert_eq!(aggregate(&items, &adapters()), (Some(35.5), Some(100.0)));
    }

    #[test]
    fn idle_and_missing_adapters() {
        assert_eq!(aggregate(&[], &adapters()), (Some(0.0), Some(0.0)));
        let gpu_only = vec![adapters()[0].clone()];
        assert_eq!(aggregate(&[], &gpu_only), (Some(0.0), None));
    }

    #[test]
    fn heuristic_without_dxcore() {
        let items = vec![
            (
                "pid_1_luid_0x00000000_0x0000d1a2_phys_0_eng_0_engtype_3D".to_string(),
                12.0,
            ),
            (
                "pid_3_luid_0x00000000_0x0000e3f0_phys_0_eng_0_engtype_Compute".to_string(),
                40.0,
            ),
        ];
        assert_eq!(aggregate(&items, &[]), (Some(12.0), Some(40.0)));
    }

    struct Fake(u64, Vec<Adapter>);
    impl Collector for Fake {
        fn adapters(&self) -> &[Adapter] {
            &self.1
        }
        fn sample(&mut self) -> Result<Sample, String> {
            self.0 += 1;
            Ok(Sample {
                at_ms: self.0,
                cpu: Some(10.0),
                ..Default::default()
            })
        }
    }

    #[test]
    fn monitor_collects_and_filters_since() {
        let monitor = Monitor::default();
        let adapters = adapters();
        monitor.ensure_running(move || Ok(Box::new(Fake(0, adapters)) as Box<dyn Collector>));
        thread::sleep(Duration::from_millis(INTERVAL_MS * 2 + 400));
        let inner = monitor.inner.lock().unwrap();
        assert_eq!(inner.source, "windows-pdh");
        assert!(inner.samples.len() >= 2);
        assert_eq!(inner.npu_name.as_deref(), Some("Intel(R) AI Boost"));
        drop(inner);
        let snapshot = monitor.snapshot(Some(1)); // already running: no second thread
        assert!(!snapshot.samples.is_empty());
        assert!(snapshot.samples.iter().all(|s| s.at_ms > 1));
    }

    #[test]
    fn open_failure_reports_error_state() {
        let monitor = Monitor::default();
        monitor.ensure_running(|| Err("PdhOpenQuery failed".into()));
        thread::sleep(Duration::from_millis(200));
        let inner = monitor.inner.lock().unwrap();
        assert_eq!(inner.source, "error");
        assert!(!inner.running);
    }
}
