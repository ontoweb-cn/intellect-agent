//! Background delegation registry — in-process handle tracking and completion queue.
//!
//! Mirrors the ``PlatformRetryScheduler`` pattern: state in Rust, Python owns
//! subagent threads. Mutations go through PyO3 with ``&mut self``; under CPython
//! the GIL serializes concurrent Python callers.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use std::collections::{HashMap, VecDeque};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

static NEXT_ID: AtomicU64 = AtomicU64::new(1);

fn now_secs() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

fn new_handle_id() -> String {
    let n = NEXT_ID.fetch_add(1, Ordering::Relaxed);
    format!("d-{n}")
}

#[derive(Clone, Debug, PartialEq, Eq)]
enum DelegationStatus {
    Running,
    Completed,
    Failed,
    Cancelled,
}

impl DelegationStatus {
    fn as_str(&self) -> &'static str {
        match self {
            DelegationStatus::Running => "running",
            DelegationStatus::Completed => "completed",
            DelegationStatus::Failed => "failed",
            DelegationStatus::Cancelled => "cancelled",
        }
    }

    fn parse(s: &str) -> Option<Self> {
        match s {
            "running" => Some(DelegationStatus::Running),
            "completed" => Some(DelegationStatus::Completed),
            "failed" => Some(DelegationStatus::Failed),
            "cancelled" => Some(DelegationStatus::Cancelled),
            _ => None,
        }
    }
}

struct DelegationEntry {
    handle_id: String,
    parent_session_key: String,
    goal: String,
    status: DelegationStatus,
    summary: String,
    error: String,
    started_at: f64,
    finished_at: Option<f64>,
    cancel_requested: bool,
}

/// In-process registry for background ``delegate_task`` handles.
#[pyclass]
pub struct DelegationRegistry {
    entries: HashMap<String, DelegationEntry>,
    /// parent_session_key -> queued handle ids ready for drain
    completion_queue: HashMap<String, VecDeque<String>>,
}

#[pymethods]
impl DelegationRegistry {
    #[new]
    fn new() -> Self {
        DelegationRegistry {
            entries: HashMap::new(),
            completion_queue: HashMap::new(),
        }
    }

    /// Register a new background delegation. Returns handle id.
    fn register(&mut self, parent_session_key: &str, goal: &str) -> PyResult<String> {
        let key = parent_session_key.trim();
        if key.is_empty() {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "parent_session_key must be non-empty",
            ));
        }
        let goal_text = goal.trim();
        if goal_text.is_empty() {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "goal must be non-empty",
            ));
        }
        let handle_id = new_handle_id();
        self.entries.insert(
            handle_id.clone(),
            DelegationEntry {
                handle_id: handle_id.clone(),
                parent_session_key: key.to_string(),
                goal: goal_text.to_string(),
                status: DelegationStatus::Running,
                summary: String::new(),
                error: String::new(),
                started_at: now_secs(),
                finished_at: None,
                cancel_requested: false,
            },
        );
        Ok(handle_id)
    }

    /// Mark a handle complete. ``status`` must be completed|failed|cancelled.
    fn complete(
        &mut self,
        handle_id: &str,
        status: &str,
        summary: &str,
        error: &str,
    ) -> PyResult<bool> {
        let st = DelegationStatus::parse(status).ok_or_else(|| {
            pyo3::exceptions::PyValueError::new_err(format!("invalid status: {status}"))
        })?;
        if st == DelegationStatus::Running {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "complete status cannot be running",
            ));
        }
        let Some(entry) = self.entries.get_mut(handle_id) else {
            return Ok(false);
        };
        if entry.status != DelegationStatus::Running {
            return Ok(false);
        }
        let parent_key = entry.parent_session_key.clone();
        entry.status = st;
        entry.summary = summary.to_string();
        entry.error = error.to_string();
        entry.finished_at = Some(now_secs());
        self.completion_queue
            .entry(parent_key)
            .or_default()
            .push_back(handle_id.to_string());
        Ok(true)
    }

    /// Request cancellation for a running handle. Returns whether request was accepted.
    fn cancel(&mut self, handle_id: &str) -> PyResult<bool> {
        let Some(entry) = self.entries.get_mut(handle_id) else {
            return Ok(false);
        };
        if entry.status != DelegationStatus::Running {
            return Ok(false);
        }
        entry.cancel_requested = true;
        Ok(true)
    }

    fn is_cancel_requested(&self, handle_id: &str) -> bool {
        self.entries
            .get(handle_id)
            .map(|e| e.cancel_requested)
            .unwrap_or(false)
    }

    fn count_running(&self) -> usize {
        self.entries
            .values()
            .filter(|e| e.status == DelegationStatus::Running)
            .count()
    }

    fn len(&self) -> usize {
        self.entries.len()
    }

    fn get<'py>(&self, py: Python<'py>, handle_id: &str) -> PyResult<Option<Py<PyAny>>> {
        let Some(entry) = self.entries.get(handle_id) else {
            return Ok(None);
        };
        Ok(Some(entry_to_dict(py, entry)?.into()))
    }

    /// List entries, optionally filtered by parent_session_key (empty = all).
    fn list<'py>(
        &self,
        py: Python<'py>,
        parent_session_key: Option<&str>,
    ) -> PyResult<Py<PyAny>> {
        let filter = parent_session_key
            .map(str::trim)
            .filter(|s| !s.is_empty());
        let list = PyList::empty_bound(py);
        let mut ids: Vec<&String> = self.entries.keys().collect();
        ids.sort();
        for hid in ids {
            let entry = &self.entries[hid];
            if let Some(f) = filter {
                if entry.parent_session_key != f {
                    continue;
                }
            }
            list.append(entry_to_dict(py, entry)?)?;
        }
        Ok(list.into())
    }

    /// Drain all pending completions for a parent session key.
    fn drain_completions(&mut self, parent_session_key: &str) -> Vec<String> {
        self.drain_completions_up_to(parent_session_key, 0)
    }

    /// Drain up to ``limit`` pending completions (``limit`` 0 = drain all).
    fn drain_completions_up_to(&mut self, parent_session_key: &str, limit: usize) -> Vec<String> {
        let key = parent_session_key.trim();
        let Some(queue) = self.completion_queue.get_mut(key) else {
            return Vec::new();
        };
        let take = if limit == 0 {
            queue.len()
        } else {
            limit.min(queue.len())
        };
        let mut out = Vec::with_capacity(take);
        for _ in 0..take {
            if let Some(id) = queue.pop_front() {
                out.push(id);
            }
        }
        if queue.is_empty() {
            self.completion_queue.remove(key);
        }
        out
    }

    /// Re-queue handle ids at the front of the completion queue (e.g. after a failed inject).
    fn requeue_completions(&mut self, parent_session_key: &str, handle_ids: Vec<String>) {
        let key = parent_session_key.trim();
        if key.is_empty() || handle_ids.is_empty() {
            return;
        }
        let queue = self.completion_queue.entry(key.to_string()).or_default();
        for id in handle_ids.into_iter().rev() {
            if self.entries.contains_key(&id) {
                queue.push_front(id);
            }
        }
    }

    fn clear(&mut self) {
        self.entries.clear();
        self.completion_queue.clear();
    }
}

fn entry_to_dict<'py>(
    py: Python<'py>,
    entry: &DelegationEntry,
) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new_bound(py);
    d.set_item("handle_id", &entry.handle_id)?;
    d.set_item("parent_session_key", &entry.parent_session_key)?;
    d.set_item("goal", &entry.goal)?;
    d.set_item("status", entry.status.as_str())?;
    d.set_item("summary", &entry.summary)?;
    d.set_item("error", &entry.error)?;
    d.set_item("started_at", entry.started_at)?;
    d.set_item(
        "finished_at",
        entry.finished_at.unwrap_or(0.0),
    )?;
    d.set_item("cancel_requested", entry.cancel_requested)?;
    Ok(d)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn reg() -> DelegationRegistry {
        DelegationRegistry::new()
    }

    #[test]
    fn test_register_and_complete() {
        let mut r = reg();
        let id = r.register("agent:main:cli:default", "fix tests").unwrap();
        assert!(id.starts_with("d-"));
        assert_eq!(r.count_running(), 1);
        assert!(r
            .complete(&id, "completed", "all green", "")
            .unwrap());
        assert_eq!(r.count_running(), 0);
        let drained = r.drain_completions("agent:main:cli:default");
        assert_eq!(drained, vec![id]);
        assert!(r.drain_completions("agent:main:cli:default").is_empty());
    }

    #[test]
    fn test_cancel_request_flag() {
        let mut r = reg();
        let id = r.register("sk1", "task").unwrap();
        assert!(r.cancel(&id).unwrap());
        assert!(r.is_cancel_requested(&id));
    }

    #[test]
    fn test_complete_idempotent() {
        let mut r = reg();
        let id = r.register("sk1", "task").unwrap();
        assert!(r.complete(&id, "completed", "ok", "").unwrap());
        assert!(!r.complete(&id, "failed", "nope", "").unwrap());
    }

    #[test]
    fn test_drain_isolated_by_parent_key() {
        let mut r = reg();
        let a = r.register("parent-a", "ga").unwrap();
        let b = r.register("parent-b", "gb").unwrap();
        r.complete(&a, "completed", "sa", "").unwrap();
        r.complete(&b, "completed", "sb", "").unwrap();
        assert_eq!(r.drain_completions("parent-a"), vec![a]);
        assert_eq!(r.drain_completions("parent-b").len(), 1);
    }

    #[test]
    fn test_drain_up_to_preserves_remainder() {
        let mut r = reg();
        let ids: Vec<String> = (0..4)
            .map(|i| r.register("parent", &format!("goal-{i}")).unwrap())
            .collect();
        for id in &ids {
            r.complete(id, "completed", "ok", "").unwrap();
        }
        let first = r.drain_completions_up_to("parent", 2);
        assert_eq!(first, ids[..2]);
        let rest = r.drain_completions("parent");
        assert_eq!(rest, ids[2..]);
    }

    #[test]
    fn test_requeue_completions() {
        let mut r = reg();
        let a = r.register("parent", "ga").unwrap();
        let b = r.register("parent", "gb").unwrap();
        r.complete(&a, "completed", "sa", "").unwrap();
        r.complete(&b, "completed", "sb", "").unwrap();
        let drained = r.drain_completions("parent");
        assert_eq!(drained.len(), 2);
        r.requeue_completions("parent", drained);
        assert_eq!(r.drain_completions("parent").len(), 2);
    }
}
