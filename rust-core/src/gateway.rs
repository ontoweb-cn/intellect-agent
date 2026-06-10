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
}
