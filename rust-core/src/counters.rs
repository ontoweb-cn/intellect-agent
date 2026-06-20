//! Counters — iteration budget and jittered backoff.
//!
//! Phase 1: Migrates `agent/iteration_budget.py` (thread-safe consume/refund
//! counter) and `agent/retry_utils.py` (jittered exponential backoff with
//! configurable jitter ratio) to Rust.

use pyo3::prelude::*;
use std::sync::atomic::{AtomicI64, Ordering};
use std::sync::Mutex;

// ── IterationBudget ────────────────────────────────────────────────────────

/// Thread-safe iteration counter for agent loop control.
///
/// Each agent (parent or subagent) gets its own ``IterationBudget``.
/// ``consume()`` returns True if the iteration was allowed.
/// ``refund()`` is used for ``execute_code`` (programmatic tool calling)
/// turns so they don't eat into the budget.
///
/// The Rust implementation uses an ``AtomicI64`` for the counter (no lock
/// needed for the common consume path) and a ``Mutex`` for the refund
/// guard (to prevent underflow races).
#[pyclass(name = "IterationBudget")]
pub struct IterationBudget {
    max_total: i64,
    used: AtomicI64,
    _refund_lock: Mutex<()>,
}

#[pymethods]
impl IterationBudget {
    #[new]
    fn new(max_total: i64) -> Self {
        IterationBudget {
            max_total,
            used: AtomicI64::new(0),
            _refund_lock: Mutex::new(()),
        }
    }

    /// Try to consume one iteration. Returns True if allowed.
    fn consume(&self) -> bool {
        loop {
            let current = self.used.load(Ordering::SeqCst);
            if current >= self.max_total {
                return false;
            }
            if self
                .used
                .compare_exchange(current, current + 1, Ordering::SeqCst, Ordering::SeqCst)
                .is_ok()
            {
                return true;
            }
            // CAS failed — another thread consumed concurrently, retry
        }
    }

    /// Give back one iteration (e.g. for execute_code turns).
    fn refund(&self) {
        // Lock to prevent racing refunds from causing underflow
        let _guard = self._refund_lock.lock().unwrap();
        let current = self.used.load(Ordering::SeqCst);
        if current > 0 {
            self.used.store(current - 1, Ordering::SeqCst);
        }
    }

    #[getter]
    fn max_total(&self) -> i64 {
        self.max_total
    }

    #[getter]
    fn used(&self) -> i64 {
        self.used.load(Ordering::SeqCst)
    }

    #[getter]
    fn remaining(&self) -> i64 {
        let u = self.used.load(Ordering::SeqCst);
        std::cmp::max(0, self.max_total - u)
    }
}

// ── Jittered Backoff ───────────────────────────────────────────────────────

use std::sync::atomic::AtomicU64;

static JITTER_COUNTER: AtomicU64 = AtomicU64::new(0);

/// Compute a jittered exponential backoff delay with configurable jitter ratio.
///
/// Mirrors ``agent/retry_utils.py:jittered_backoff()``.
///
/// Args:
///     attempt: 1-based retry attempt number.
///     base_delay: Base delay in seconds for attempt 1.
///     max_delay: Maximum delay cap in seconds.
///     jitter_ratio: Fraction of computed delay to use as random jitter
///         range. 0.5 means jitter is uniform in [0, 0.5 * delay].
///
/// Returns:
///     Delay in seconds: min(base * 2^(attempt-1), max_delay) + jitter.
///
/// The jitter decorrelates concurrent retries so multiple sessions
/// hitting the same provider don't all retry at the same instant.
///
/// Note: This differs from ``gateway::backoff_delay_rs`` which uses a
/// hardcoded ±25% jitter factor and a deterministic sin-based spread.
/// This function uses a configurable jitter_ratio and a decoupled seed
/// (time_ns XOR golden-ratio-hashed counter), matching the Python version.
#[pyfunction]
pub fn jittered_backoff_rs(
    attempt: u32,
    base_delay: f64,
    max_delay: f64,
    jitter_ratio: f64,
) -> f64 {
    // Seed from time + monotonic counter for cross-process decorrelation.
    // Matches Python's `(time.time_ns() ^ (tick * 0x9E3779B9)) & 0xFFFFFFFF`.
    let tick = JITTER_COUNTER.fetch_add(1, Ordering::SeqCst);
    let now_ns = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64;
    let seed = (now_ns ^ (tick.wrapping_mul(0x9E3779B9))) & 0xFFFFFFFF;

    let exponent = if attempt == 0 { 0 } else { attempt - 1 };
    let delay = if exponent >= 63 || base_delay <= 0.0 {
        max_delay
    } else {
        let raw = base_delay * (2u64.pow(exponent) as f64);
        raw.min(max_delay)
    };

    // Linear congruential generator seeded from the hashed counter
    // Matches Python's random.Random(seed).uniform(0, jitter_ratio * delay)
    let lcg = (seed.wrapping_mul(1103515245).wrapping_add(12345)) & 0x7FFFFFFF;
    let norm = lcg as f64 / 0x7FFFFFFFu64 as f64;
    let jitter = norm * jitter_ratio * delay;

    delay + jitter
}

// ── Tests ──────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_budget_consume_until_exhausted() {
        let b = IterationBudget::new(3);
        assert!(b.consume());
        assert!(b.consume());
        assert!(b.consume());
        assert!(!b.consume(), "should be exhausted after 3 consumes");
    }

    #[test]
    fn test_budget_refund() {
        let b = IterationBudget::new(10);
        for _ in 0..5 {
            b.consume();
        }
        assert_eq!(b.used(), 5);
        b.refund();
        assert_eq!(b.used(), 4);
        b.refund();
        assert_eq!(b.used(), 3);
        // Refund below 0 should be a no-op
        b.refund();
        b.refund();
        b.refund();
        b.refund();
        assert_eq!(b.used(), 0);
    }

    #[test]
    fn test_budget_remaining() {
        let b = IterationBudget::new(10);
        assert_eq!(b.remaining(), 10);
        b.consume();
        assert_eq!(b.remaining(), 9);
        for _ in 0..9 {
            b.consume();
        }
        assert_eq!(b.remaining(), 0);
    }

    #[test]
    fn test_budget_consume_refund_consume() {
        let b = IterationBudget::new(2);
        assert!(b.consume());
        assert!(b.consume());
        assert!(!b.consume(), "exhausted");
        b.refund();
        assert!(b.consume(), "should be allowed after refund");
        assert!(!b.consume(), "exhausted again");
    }

    #[test]
    fn test_jittered_backoff_no_jitter() {
        // jitter_ratio=0 → no jitter, pure exponential
        let d1 = jittered_backoff_rs(1, 5.0, 120.0, 0.0);
        assert!((d1 - 5.0).abs() < 0.001, "attempt 1: expected 5.0, got {d1}");

        let d2 = jittered_backoff_rs(2, 5.0, 120.0, 0.0);
        assert!((d2 - 10.0).abs() < 0.001, "attempt 2: expected 10.0, got {d2}");

        let d3 = jittered_backoff_rs(3, 5.0, 120.0, 0.0);
        assert!((d3 - 20.0).abs() < 0.001, "attempt 3: expected 20.0, got {d3}");
    }

    #[test]
    fn test_jittered_backoff_with_jitter() {
        // jitter_ratio=0.5 → jitter in [0, 0.5*delay]
        let d1 = jittered_backoff_rs(1, 5.0, 120.0, 0.5);
        assert!(d1 >= 5.0, "delay should be at least base");
        assert!(d1 <= 7.5, "delay should be at most base + 50%");

        let d2 = jittered_backoff_rs(2, 5.0, 120.0, 0.5);
        assert!(d2 >= 10.0);
        assert!(d2 <= 15.0);
    }

    #[test]
    fn test_jittered_backoff_max_cap() {
        // High attempt → capped at max_delay
        let d = jittered_backoff_rs(100, 5.0, 120.0, 0.5);
        assert!(d >= 120.0, "capped at max: got {d}");
        assert!(d <= 180.0, "capped at max + 50% jitter: got {d}");
    }

    #[test]
    fn test_jittered_backoff_zero_base() {
        // base_delay <= 0 → capped at max
        let d = jittered_backoff_rs(1, 0.0, 120.0, 0.5);
        assert!(d >= 120.0);
        assert!(d <= 180.0);
    }

    #[test]
    fn test_jittered_backoff_overflow_protection() {
        // attempt with exponent >= 63 → max_delay
        let d = jittered_backoff_rs(65, 5.0, 120.0, 0.5);
        assert!(d >= 120.0);
        assert!(d <= 180.0);
    }
}
