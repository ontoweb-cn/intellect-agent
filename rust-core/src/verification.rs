//! Verification evidence storage — SQLite table for tracking test/command
//! verification results (HP-303).  All operations are fail-open: a write
//! failure must never block the agent turn.

use pyo3::prelude::*;
use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VerificationEvidence {
    pub id: String,
    pub session_id: String,
    pub task_id: Option<String>,
    pub kind: String,
    pub command: String,
    pub exit_code: Option<i32>,
    pub output_summary: String,
    pub passed: Option<bool>,
    pub created_at: i64,
}

#[pyfunction]
pub fn insert_verification_evidence(
    db_path: &str,
    id: &str,
    session_id: &str,
    kind: &str,
    command: &str,
    output_summary: &str,
    created_at: i64,
    task_id: Option<&str>,
    exit_code: Option<i32>,
    passed: Option<bool>,
) -> PyResult<()> {
    let conn = match Connection::open(db_path) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("verification: failed to open db: {}", e);
            return Ok(());
        }
    };

    let result = conn.execute(
        "INSERT OR REPLACE INTO verification_evidence
         (id, session_id, task_id, kind, command, exit_code,
          output_summary, passed, created_at)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
        params![
            id,
            session_id,
            task_id,
            kind,
            command,
            exit_code,
            output_summary,
            passed,
            created_at,
        ],
    );

    match result {
        Ok(_) => Ok(()),
        Err(e) => {
            eprintln!("verification: write failed (fail-open): {}", e);
            Ok(())
        }
    }
}

#[pyfunction]
pub fn query_verification_evidence(
    db_path: &str,
    session_id: Option<&str>,
    kind: Option<&str>,
    limit: Option<usize>,
) -> PyResult<String> {
    let conn = match Connection::open(db_path) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("verification: failed to open db: {}", e);
            return Ok("[]".to_string());
        }
    };

    let mut sql = String::from(
        "SELECT id, session_id, task_id, kind, command, exit_code,
                output_summary, passed, created_at
         FROM verification_evidence WHERE 1=1",
    );

    if session_id.is_some() {
        sql.push_str(" AND session_id = ?");
    }
    if kind.is_some() {
        sql.push_str(" AND kind = ?");
    }

    sql.push_str(" ORDER BY created_at DESC");
    if let Some(lim) = limit {
        sql.push_str(&format!(" LIMIT {}", lim));
    }

    let mut stmt = match conn.prepare(&sql) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("verification: query failed: {}", e);
            return Ok("[]".to_string());
        }
    };

    let rows: Vec<VerificationEvidence> = {
        let mut param_vals: Vec<Box<dyn rusqlite::types::ToSql>> = Vec::new();
        if let Some(ref sid) = session_id {
            param_vals.push(Box::new(sid.to_string()));
        }
        if let Some(ref k) = kind {
            param_vals.push(Box::new(k.to_string()));
        }
        let params_refs: Vec<&dyn rusqlite::types::ToSql> =
            param_vals.iter().map(|p| p.as_ref()).collect();
        match stmt.query_map(params_refs.as_slice(), |row| {
            Ok(VerificationEvidence {
                id: row.get(0)?,
                session_id: row.get(1)?,
                task_id: row.get(2)?,
                kind: row.get(3)?,
                command: row.get(4)?,
                exit_code: row.get(5)?,
                output_summary: row.get(6)?,
                passed: row.get(7)?,
                created_at: row.get(8)?,
            })
        }) {
            Ok(mapped) => mapped.filter_map(|r| r.ok()).collect(),
            Err(e) => {
                eprintln!("verification: query failed: {}", e);
                return Ok("[]".to_string());
            }
        }
    };

    Ok(serde_json::to_string(&rows).unwrap_or_else(|_| "[]".to_string()))
}

/// Classify a shell command into a verification kind.
/// Returns None if the command is not a verification command.
#[pyfunction]
pub fn classify_verification_command(command: &str) -> Option<String> {
    let cmd = command.trim().to_lowercase();

    if cmd.contains("pytest") || cmd.contains("python -m pytest") {
        return Some("test_run".to_string());
    }
    if cmd.contains("cargo test")
        || cmd.contains("cargo check")
        || cmd.contains("cargo clippy")
    {
        return Some("test_run".to_string());
    }
    if cmd.contains("npm test")
        || cmd.contains("npm run test")
        || cmd.contains("yarn test")
        || cmd.contains("pnpm test")
        || cmd.contains("jest")
        || cmd.contains("vitest")
        || cmd.contains("mocha")
    {
        return Some("test_run".to_string());
    }
    if cmd.contains("go test") {
        return Some("test_run".to_string());
    }
    if cmd.contains("make check")
        || cmd.contains("make test")
        || cmd.contains("just test")
        || cmd.contains("just check")
    {
        return Some("test_run".to_string());
    }
    if cmd.contains("ruff")
        || cmd.contains("eslint")
        || cmd.contains("clippy")
        || cmd.contains("shellcheck")
        || cmd.contains("mypy")
    {
        return Some("test_run".to_string());
    }
    if cmd.starts_with("git diff") || cmd.starts_with("diff ") {
        return Some("diff_validation".to_string());
    }
    if cmd.contains("check")
        || cmd.contains("verify")
        || cmd.contains("validate")
        || cmd.contains("lint")
        || cmd.contains("audit")
    {
        return Some("command_output".to_string());
    }

    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_classify_pytest() {
        assert_eq!(
            classify_verification_command("pytest tests/"),
            Some("test_run".to_string())
        );
    }

    #[test]
    fn test_classify_cargo() {
        assert_eq!(
            classify_verification_command("cargo test --lib"),
            Some("test_run".to_string())
        );
    }

    #[test]
    fn test_classify_npm() {
        assert_eq!(
            classify_verification_command("npm test -- --verbose"),
            Some("test_run".to_string())
        );
    }

    #[test]
    fn test_classify_non_verification() {
        assert_eq!(classify_verification_command("ls -la"), None);
        assert_eq!(classify_verification_command("echo hello"), None);
    }

    #[test]
    fn test_insert_and_query() {
        let db_path = ":memory:";
        let conn = Connection::open(db_path).unwrap();
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS verification_evidence (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                task_id TEXT,
                kind TEXT NOT NULL,
                command TEXT NOT NULL,
                exit_code INTEGER,
                output_summary TEXT NOT NULL DEFAULT '',
                passed INTEGER,
                created_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ve_session ON verification_evidence(session_id);
            CREATE INDEX IF NOT EXISTS idx_ve_kind ON verification_evidence(kind);",
        )
        .unwrap();

        insert_verification_evidence(
            db_path,
            "ev1",
            "sess1",
            "test_run",
            "pytest",
            "all passed",
            1000,
            None,
            Some(0),
            Some(true),
        )
        .unwrap();

        let result =
            query_verification_evidence(db_path, Some("sess1"), None, None).unwrap();
        assert!(result.contains("ev1"));
        assert!(result.contains("pytest"));
    }
}
