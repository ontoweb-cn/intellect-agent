//! Schema constants — equivalent to Python's `state/schema.py`.
//!
//! FTS5 identifier whitelists used across the storage layer.

/// Allowed FTS5 virtual table names.
pub static FTS_TABLES: &[&str] = &["messages_fts"];

/// Expected FTS5 trigger names (3 per table: insert/delete/update).
pub static FTS_TRIGGERS: &[&str] = &[
    "messages_fts_insert",
    "messages_fts_delete",
    "messages_fts_update",
];

/// Validate an FTS identifier against an allowlist.
/// Mirrors Python's `validate_fts_identifier()`.
pub fn validate_fts_identifier(name: &str, allowed: &[&str]) -> Result<String, String> {
    if allowed.contains(&name) {
        Ok(name.to_string())
    } else {
        Err(format!(
            "Unexpected FTS identifier {name:?}; expected one of {allowed:?}"
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_valid_triggers() {
        for t in FTS_TRIGGERS {
            assert!(validate_fts_identifier(t, FTS_TRIGGERS).is_ok());
        }
    }

    #[test]
    fn test_invalid_rejected() {
        assert!(validate_fts_identifier("bogus", FTS_TRIGGERS).is_err());
    }

    #[test]
    fn test_fts_tables() {
        assert!(validate_fts_identifier("messages_fts", FTS_TABLES).is_ok());
    }
}
