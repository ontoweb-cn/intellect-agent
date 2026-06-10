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
