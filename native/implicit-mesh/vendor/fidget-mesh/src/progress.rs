//! stokes patch: live work counts for a progress display (off unless `enable()` is called).
//!
//! One mesh per process, so the counters are process globals: `PHASE`, `DONE`, `TOTAL`, read by
//! a reporter thread while the work runs.
//!
//! - Octree build: before the parallel tasks start, every task cell is interval-evaluated down
//!   to `level` (two levels below the task split) and the cells that may contain surface are
//!   marked; `TOTAL` = their number. The build adds 1 to `DONE` when it finishes such a cell,
//!   and, when it resolves a coarser cell as empty or full without descending (its tape is
//!   simplified, so its interval can be tighter than the pre-pass's), it adds the marked cells
//!   inside it. So `DONE` reaches `TOTAL` exactly. The work per marked cell varies with how
//!   much surface it holds; it is the unit of the count.
//! - Dual walk: `TOTAL` = 1 + 8 x the number of branch cells (every cell `dc_cell` visits);
//!   `DONE` = cells visited.
use std::sync::OnceLock;
use std::sync::atomic::{AtomicBool, AtomicU8, AtomicU64, Ordering};

/// counting is on
pub static ENABLED: AtomicBool = AtomicBool::new(false);
/// 0 idle, 1 build, 2 walk; the caller uses its own numbers above 2
pub static PHASE: AtomicU8 = AtomicU8::new(0);
/// work done in the current phase
pub static DONE: AtomicU64 = AtomicU64::new(0);
/// work in the current phase
pub static TOTAL: AtomicU64 = AtomicU64::new(0);

pub(crate) struct Marks {
    pub level: usize,
    pub side: usize,
    /// per cell at `level`: 0 = no surface, else its weight (surface cells a few levels down)
    pub weight: Vec<u16>,
}

pub(crate) static MARKS: OnceLock<Marks> = OnceLock::new();

/// Turns counting on (before the build).
pub fn enable() {
    ENABLED.store(true, Ordering::Relaxed);
}

/// Whether counting is on.
pub fn enabled() -> bool {
    ENABLED.load(Ordering::Relaxed)
}

/// Starts a phase.
pub fn start(phase: u8, total: u64) {
    DONE.store(0, Ordering::Relaxed);
    TOTAL.store(total, Ordering::Relaxed);
    PHASE.store(phase, Ordering::Release);
}

/// Adds work done.
pub fn add(n: u64) {
    DONE.fetch_add(n, Ordering::Relaxed);
}

/// (phase, done, total)
pub fn read() -> (u8, u64, u64) {
    let p = PHASE.load(Ordering::Acquire);
    (p, DONE.load(Ordering::Relaxed), TOTAL.load(Ordering::Relaxed))
}

impl Marks {
    pub fn new(level: usize) -> Self {
        let side = 1usize << level;
        Marks { level, side, weight: vec![0; side * side * side] }
    }
    fn idx(&self, x: usize, y: usize, z: usize) -> usize {
        (z * self.side + y) * self.side + x
    }
    pub fn set(&mut self, c: [usize; 3], w: u16) {
        let i = self.idx(c[0], c[1], c[2]);
        self.weight[i] = w.max(1);
    }
    fn get(&self, x: usize, y: usize, z: usize) -> u64 {
        u64::from(self.weight[self.idx(x, y, z)])
    }
    /// Marked cells at `self.level` inside the cell at `depth` whose lowest corner, in cells of
    /// that depth, is `c`.
    pub fn count(&self, depth: usize, c: [usize; 3]) -> u64 {
        if depth > self.level {
            return 0;
        }
        let k = 1usize << (self.level - depth);
        let mut n = 0;
        for z in c[2] * k..(c[2] + 1) * k {
            for y in c[1] * k..(c[1] + 1) * k {
                for x in c[0] * k..(c[0] + 1) * k {
                    n += self.get(x, y, z);
                }
            }
        }
        n
    }
}

/// Integer coordinates of a cell's lowest corner in cells of its depth (bounds are in [-1, 1]).
pub(crate) fn coords(lower: [f32; 3], depth: usize) -> [usize; 3] {
    let s = (1usize << depth) as f32;
    lower.map(|l| (((l + 1.0) * 0.5) * s).round() as usize)
}

/// Sets both counts (for work whose total grows while it runs).
pub fn set(done: u64, total: u64) {
    TOTAL.store(total, Ordering::Relaxed);
    DONE.store(done, Ordering::Relaxed);
}
