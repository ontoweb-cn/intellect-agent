//! Gateway utilities — session keys, rate limiting, retry backoff.
//!
//! Stage 4a: build_session_key — deterministic session key from source fields.

use pyo3::prelude::*;

/// Build a deterministic session key from message source fields.
/// Mirrors `gateway/session.py:build_session_key()`.
///
/// Python handles WhatsApp canonicalization before calling this function;
/// Rust receives already-canonicalized identifiers.
#[pyfunction]
#[allow(clippy::too_many_arguments)]
pub fn build_session_key_rs(
    platform: &str,
    chat_type: &str,
    chat_id: &str,
    thread_id: &str,
    user_id: &str,
    user_id_alt: &str,
    group_sessions_per_user: bool,
    thread_sessions_per_user: bool,
    member_id: &str,
    team_id: &str,
    project_id: &str,
) -> String {
    let mut key = String::with_capacity(128);
    let participant_id = if !user_id_alt.is_empty() {
        user_id_alt
    } else {
        user_id
    };

    if chat_type == "dm" {
        // DM: agent:main:<platform>:dm[:chat_id][:thread_id]
        key.push_str("agent:main:");
        key.push_str(platform);
        key.push_str(":dm");
        if !chat_id.is_empty() {
            key.push(':');
            key.push_str(chat_id);
        }
        if !thread_id.is_empty() {
            key.push(':');
            key.push_str(thread_id);
        }
    } else {
        // Group/channel/thread
        key.push_str("agent:main:");
        key.push_str(platform);
        key.push(':');
        key.push_str(chat_type);
        if !chat_id.is_empty() {
            key.push(':');
            key.push_str(chat_id);
        }
        if !thread_id.is_empty() {
            key.push(':');
            key.push_str(thread_id);
        }

        // Thread isolation policy
        let isolate_user = if !thread_id.is_empty() && !thread_sessions_per_user {
            false
        } else {
            group_sessions_per_user
        };

        if isolate_user && !participant_id.is_empty() {
            key.push(':');
            key.push_str(participant_id);
        }
    }

    // Multi-user extensions (additive)
    if !member_id.is_empty() {
        key.push_str(":member:");
        key.push_str(member_id);
    }
    if !team_id.is_empty() {
        key.push_str(":team:");
        key.push_str(team_id);
    }
    if !project_id.is_empty() {
        key.push_str(":project:");
        key.push_str(project_id);
    }

    key
}

// ── Session reset policy (Stage 4b) ────────────────────────────────────────

/// Evaluate whether a session should be reset based on policy.
/// Returns the reason string ("idle" or "daily") or None.
/// All timestamps are Unix seconds (f64, as used by Python's time.time()).
#[pyfunction]
pub fn evaluate_reset_policy_rs(
    mode: &str,
    idle_minutes: f64,
    at_hour: u32,
    updated_at: f64,
    now: f64,
) -> Option<String> {
    match mode {
        "none" => None,
        "idle" => {
            let idle_secs = idle_minutes * 60.0;
            if now > updated_at + idle_secs {
                Some("idle".to_string())
            } else {
                None
            }
        }
        "daily" => {
            if is_daily_reset(updated_at, now, at_hour) {
                Some("daily".to_string())
            } else {
                None
            }
        }
        "both" => {
            let idle_secs = idle_minutes * 60.0;
            if now > updated_at + idle_secs {
                return Some("idle".to_string());
            }
            if is_daily_reset(updated_at, now, at_hour) {
                return Some("daily".to_string());
            }
            None
        }
        _ => None,
    }
}

fn is_daily_reset(updated_at: f64, now: f64, at_hour: u32) -> bool {
    // Compute the Unix timestamp of today's reset hour in local time.
    // Python's .replace(hour=..., minute=0, second=0, microsecond=0) on a
    // datetime.  We approximate using Unix epoch day alignment.
    let seconds_per_day: f64 = 86400.0;
    let days_since_epoch = (now / seconds_per_day).floor();
    let today_reset = days_since_epoch * seconds_per_day + (at_hour as f64) * 3600.0;

    let reset_deadline = if now < today_reset {
        today_reset - seconds_per_day // use yesterday's reset
    } else {
        today_reset
    };

    updated_at < reset_deadline
}

// ── Exponential backoff (Stage 4c) ──────────────────────────────────────────

/// Compute the next retry delay using exponential backoff with jitter.
/// Returns seconds as f64.
/// `attempt`: zero-based attempt number
/// `base_seconds`: initial delay
/// `max_seconds`: cap
#[pyfunction]
pub fn backoff_delay_rs(attempt: u32, base_seconds: f64, max_seconds: f64) -> f64 {
    let raw = base_seconds * (2u64.pow(attempt) as f64);
    let capped = raw.min(max_seconds);
    // Add ±25% jitter
    let jitter = capped * 0.25;
    let lo = capped - jitter;
    let hi = capped + jitter;
    // Use a simple deterministic jitter based on attempt for reproducibility
    let frac = (attempt as f64).sin().abs();
    lo + (hi - lo) * frac
}

// ── Rate limiter: token bucket (Stage 4d) ───────────────────────────────────

use std::sync::Mutex;
use std::time::Instant;

/// Simple token bucket rate limiter for gateway message throttling.
#[pyclass]
pub struct TokenBucket {
    rate: f64,          // tokens per second
    capacity: f64,      // max burst
    tokens: Mutex<f64>,
    last_refill: Mutex<Instant>,
}

#[pymethods]
impl TokenBucket {
    /// Create a new token bucket.
    /// `rate`: tokens per second (sustained rate)
    /// `capacity`: max burst size
    #[new]
    fn new(rate: f64, capacity: f64) -> Self {
        TokenBucket {
            rate,
            capacity,
            tokens: Mutex::new(capacity),
            last_refill: Mutex::new(Instant::now()),
        }
    }

    /// Try to consume `n` tokens. Returns true if allowed, false if rate-limited.
    fn consume(&self, n: f64) -> bool {
        let now = Instant::now();
        let mut tokens = self.tokens.lock().unwrap();
        let mut last = self.last_refill.lock().unwrap();
        let elapsed = now.duration_since(*last).as_secs_f64();
        *last = now;

        // Refill
        *tokens = (*tokens + elapsed * self.rate).min(self.capacity);

        if *tokens >= n {
            *tokens -= n;
            true
        } else {
            false
        }
    }

    /// Current token count (for diagnostics).
    fn available(&self) -> f64 {
        let mut tokens = self.tokens.lock().unwrap();
        let mut last = self.last_refill.lock().unwrap();
        let now = Instant::now();
        let elapsed = now.duration_since(*last).as_secs_f64();
        *last = now;
        *tokens = (*tokens + elapsed * self.rate).min(self.capacity);
        *tokens
    }
}

// ── Batch session expiry check (Stage 4e) ───────────────────────────────────

/// Check multiple sessions for expiry in a single call.
///
/// Takes parallel arrays of session data and returns the indices of expired
/// sessions. This is more efficient than calling evaluate_reset_policy_rs
/// in a Python loop for large session stores.
///
/// Parameters:
/// - modes: reset policy mode per session ("none", "idle", "daily", "both")
/// - idle_minutes: idle timeout per session
/// - at_hours: daily reset hour per session
/// - updated_ats: last update timestamp per session (Unix seconds)
/// - now: current timestamp (Unix seconds)
///
/// Returns: Vec of indices of expired sessions.
#[pyfunction]
pub fn check_session_expiry_batch_rs(
    modes: Vec<String>,
    idle_minutes: Vec<f64>,
    at_hours: Vec<u32>,
    updated_ats: Vec<f64>,
    now: f64,
) -> Vec<usize> {
    let len = modes.len().min(idle_minutes.len()).min(at_hours.len()).min(updated_ats.len());
    let mut expired = Vec::new();

    for i in 0..len {
        let reason = evaluate_reset_policy_rs(
            &modes[i],
            idle_minutes[i],
            at_hours[i],
            updated_ats[i],
            now,
        );
        if reason.is_some() {
            expired.push(i);
        }
    }

    expired
}

/// Compute next retry delays for multiple sessions in batch.
///
/// Takes parallel arrays and returns the delay for each.
/// More efficient than calling backoff_delay_rs in a Python loop.
#[pyfunction]
pub fn backoff_delay_batch_rs(
    attempts: Vec<u32>,
    base_seconds: Vec<f64>,
    max_seconds: Vec<f64>,
) -> Vec<f64> {
    let len = attempts.len().min(base_seconds.len()).min(max_seconds.len());
    let mut delays = Vec::with_capacity(len);

    for i in 0..len {
        delays.push(backoff_delay_rs(attempts[i], base_seconds[i], max_seconds[i]));
    }

    delays
}

// ── Platform retry scheduler (Stage 4f) ─────────────────────────────────────

use std::collections::HashMap;

/// Manages retry timing for multiple platforms.
///
/// Tracks connection state and computes next retry times using exponential
/// backoff. More efficient than Python dict + monotonic() comparisons in
/// a loop.
#[pyclass]
pub struct PlatformRetryScheduler {
    /// platform_name -> (attempts, next_retry_ts, paused)
    platforms: HashMap<String, (u32, f64, bool)>,
    base_delay: f64,
    max_delay: f64,
}

#[pymethods]
impl PlatformRetryScheduler {
    #[new]
    fn new(base_delay: f64, max_delay: f64) -> Self {
        PlatformRetryScheduler {
            platforms: HashMap::new(),
            base_delay,
            max_delay,
        }
    }

    /// Register a platform failure. Returns the computed next retry timestamp.
    fn record_failure(&mut self, platform: &str, now: f64) -> f64 {
        let entry = self.platforms.entry(platform.to_string()).or_insert((0, 0.0, false));
        entry.0 += 1;  // attempts
        let delay = backoff_delay_rs(entry.0 - 1, self.base_delay, self.max_delay);
        entry.1 = now + delay;  // next_retry
        entry.1
    }

    /// Mark a platform as paused (requires explicit resume).
    fn pause(&mut self, platform: &str) {
        if let Some(entry) = self.platforms.get_mut(platform) {
            entry.2 = true;
        }
    }

    /// Resume a paused platform and reset its retry state.
    fn resume(&mut self, platform: &str) {
        self.platforms.remove(platform);
    }

    /// Check if a platform is paused.
    fn is_paused(&self, platform: &str) -> bool {
        self.platforms.get(platform).map_or(false, |e| e.2)
    }

    /// Get platforms ready to retry (not paused, next_retry <= now).
    /// Returns list of platform names.
    fn ready_to_retry(&self, now: f64) -> Vec<String> {
        self.platforms.iter()
            .filter(|(_, (_, next_retry, paused))| !paused && *next_retry <= now)
            .map(|(name, _)| name.clone())
            .collect()
    }

    /// Get all tracked platforms with their state.
    /// Returns list of (platform, attempts, next_retry, paused).
    fn get_all_states(&self) -> Vec<(String, u32, f64, bool)> {
        self.platforms.iter()
            .map(|(name, (attempts, next_retry, paused))| {
                (name.clone(), *attempts, *next_retry, *paused)
            })
            .collect()
    }

    /// Remove a platform from tracking.
    fn remove(&mut self, platform: &str) {
        self.platforms.remove(platform);
    }

    /// Clear all tracked platforms.
    fn clear(&mut self) {
        self.platforms.clear();
    }

    /// Number of tracked platforms.
    fn len(&self) -> usize {
        self.platforms.len()
    }

    /// Check if no platforms are tracked.
    fn is_empty(&self) -> bool {
        self.platforms.is_empty()
    }
}

// ── Rust unit tests ─────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_dm_simple() {
        let key = build_session_key_rs(
            "telegram", "dm", "12345", "", "", "", true, false, "", "", "",
        );
        assert_eq!(key, "agent:main:telegram:dm:12345");
    }

    #[test]
    fn test_dm_with_thread() {
        let key = build_session_key_rs(
            "telegram", "dm", "12345", "67890", "", "", true, false, "", "", "",
        );
        assert_eq!(key, "agent:main:telegram:dm:12345:67890");
    }

    #[test]
    fn test_group_shared() {
        let key = build_session_key_rs(
            "telegram", "group", "chat_1", "", "user_a", "", true, false, "", "", "",
        );
        assert_eq!(key, "agent:main:telegram:group:chat_1:user_a");
    }

    #[test]
    fn test_group_not_isolated() {
        let key = build_session_key_rs(
            "telegram", "group", "chat_1", "", "user_a", "", false, false, "", "", "",
        );
        assert_eq!(key, "agent:main:telegram:group:chat_1");
    }

    #[test]
    fn test_thread_shared_default() {
        // Thread is shared by default (thread_sessions_per_user=false)
        let key = build_session_key_rs(
            "discord", "thread", "chat_1", "thread_1", "user_a", "", true, false, "", "", "",
        );
        assert_eq!(key, "agent:main:discord:thread:chat_1:thread_1");
    }

    #[test]
    fn test_thread_per_user() {
        let key = build_session_key_rs(
            "discord", "thread", "chat_1", "thread_1", "user_a", "", true, true, "", "", "",
        );
        assert_eq!(key, "agent:main:discord:thread:chat_1:thread_1:user_a");
    }

    #[test]
    fn test_multi_user_extensions() {
        let key = build_session_key_rs(
            "slack", "dm", "chat_x", "", "user_a", "", true, false, "mem_1", "team_1", "proj_1",
        );
        assert!(key.contains(":member:mem_1"));
        assert!(key.contains(":team:team_1"));
        assert!(key.contains(":project:proj_1"));
    }

    #[test]
    fn test_batch_expiry_empty() {
        let expired = check_session_expiry_batch_rs(vec![], vec![], vec![], vec![], 1000.0);
        assert!(expired.is_empty());
    }

    #[test]
    fn test_batch_expiry_mixed() {
        let modes = vec!["idle".to_string(), "none".to_string(), "idle".to_string()];
        let idle_minutes = vec![5.0, 5.0, 5.0];
        let at_hours = vec![0, 0, 0];
        let updated_ats = vec![0.0, 0.0, 900.0]; // first and third expired (idle 5min = 300s)
        let now = 600.0;

        let expired = check_session_expiry_batch_rs(modes, idle_minutes, at_hours, updated_ats, now);
        assert_eq!(expired, vec![0, 2]);
    }

    #[test]
    fn test_batch_backoff() {
        let attempts = vec![0, 1, 2];
        let base = vec![1.0, 1.0, 1.0];
        let max = vec![60.0, 60.0, 60.0];

        let delays = backoff_delay_batch_rs(attempts, base, max);
        assert_eq!(delays.len(), 3);
        // First delay should be ~1s (base * 2^0 = 1, with jitter)
        assert!(delays[0] > 0.5 && delays[0] < 1.5);
        // Second delay should be ~2s
        assert!(delays[1] > 1.0 && delays[1] < 3.0);
    }

    #[test]
    fn test_retry_scheduler_basic() {
        let mut sched = PlatformRetryScheduler::new(30.0, 300.0);

        // Record a failure
        let next = sched.record_failure("telegram", 100.0);
        assert!(next > 100.0);  // should be in the future
        assert_eq!(sched.len(), 1);

        // Record another failure — should increase delay
        let next2 = sched.record_failure("telegram", next + 1.0);
        assert!(next2 > next);

        // Ready to retry after the retry time
        let ready = sched.ready_to_retry(next2 + 1.0);
        assert_eq!(ready.len(), 1);
        assert_eq!(ready[0], "telegram");
    }

    #[test]
    fn test_retry_scheduler_pause_resume() {
        let mut sched = PlatformRetryScheduler::new(30.0, 300.0);
        sched.record_failure("discord", 100.0);

        // Pause
        sched.pause("discord");
        assert!(sched.is_paused("discord"));
        assert!(sched.ready_to_retry(9999.0).is_empty());

        // Resume
        sched.resume("discord");
        assert!(!sched.is_paused("discord"));
        assert!(sched.is_empty());
    }

    #[test]
    fn test_retry_scheduler_multiple_platforms() {
        let mut sched = PlatformRetryScheduler::new(30.0, 300.0);
        sched.record_failure("telegram", 100.0);
        sched.record_failure("discord", 100.0);
        sched.record_failure("slack", 100.0);

        assert_eq!(sched.len(), 3);

        // Only telegram and slack are ready
        let ready = sched.ready_to_retry(200.0);
        assert_eq!(ready.len(), 3);  // all ready since delay is ~30s

        // Pause telegram
        sched.pause("telegram");
        let ready = sched.ready_to_retry(200.0);
        assert_eq!(ready.len(), 2);
    }
}
