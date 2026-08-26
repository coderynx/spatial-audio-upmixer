use std::alloc::{GlobalAlloc, Layout, System};
use std::sync::atomic::{AtomicUsize, Ordering};

use upmixer_dsp_core::mastering::limiter::LimiterParams;
use upmixer_dsp_core::stream::conv::StreamingConvolver;
use upmixer_dsp_core::stream::limiter::StreamingLimiter;

struct CountingAllocator;

static ALLOCATIONS: AtomicUsize = AtomicUsize::new(0);

unsafe impl GlobalAlloc for CountingAllocator {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        ALLOCATIONS.fetch_add(1, Ordering::Relaxed);
        System.alloc(layout)
    }

    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        System.dealloc(ptr, layout);
    }

    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, size: usize) -> *mut u8 {
        ALLOCATIONS.fetch_add(1, Ordering::Relaxed);
        System.realloc(ptr, layout, size)
    }
}

#[global_allocator]
static ALLOCATOR: CountingAllocator = CountingAllocator;

#[test]
fn phase_four_stages_do_not_allocate_after_warmup() {
    let kernel: Vec<f64> = (0..6128)
        .map(|i| (i as f64 * 0.017).sin() / (i + 1) as f64)
        .collect();
    let mut pair = [
        StreamingConvolver::new(kernel.clone()),
        StreamingConvolver::new(kernel),
    ];
    let block = vec![0.25; 128];
    let (mut left, mut right) = (Vec::new(), Vec::new());
    StreamingConvolver::process_pair_into(&mut pair, &block, &mut left, &mut right);

    let mut limiter = StreamingLimiter::new(
        LimiterParams {
            ceiling_dbtp: -1.0,
            lookahead_ms: 5.0,
            release_ms: 50.0,
            safety_margin_db: 0.1,
        },
        48_000,
        2,
        None,
    );
    let mut queue = vec![vec![0.8; 2048], vec![0.7; 2048]];
    limiter.process(&mut queue, 0, 0, 128, false);

    ALLOCATIONS.store(0, Ordering::Relaxed);
    StreamingConvolver::process_pair_into(&mut pair, &block, &mut left, &mut right);
    limiter.process(&mut queue, 0, 128, 256, false);
    assert_eq!(ALLOCATIONS.load(Ordering::Relaxed), 0);
}
