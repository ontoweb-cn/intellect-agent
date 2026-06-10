//! StreamAccumulator — delta state machine for SSE streaming responses.
//!
//! Accumulates content, reasoning, and tool call deltas across SSE chunks,
//! then produces the assembled result on finalize().
//! Replaces the Python dict/list state machine in chat_completion_helpers.py.

use std::collections::HashMap;

use pyo3::prelude::*;

// ── ToolCallEntry ───────────────────────────────────────────────────────────

#[derive(Clone, Default)]
struct ToolCallEntry {
    id: String,
    name: String,
    arguments: String,
}

// ── StreamAccumulator ───────────────────────────────────────────────────────

#[pyclass]
pub struct StreamAccumulator {
    content_parts: Vec<String>,
    tool_calls_map: HashMap<usize, ToolCallEntry>, // slot -> entry
    reasoning_parts: Vec<String>,
    finish_reason: Option<String>,
    model_name: Option<String>,
    // Ollama workaround: detect new tool call reusing same raw index
    last_id_at_idx: HashMap<usize, String>,
    active_slot_by_idx: HashMap<usize, usize>,
    has_content: bool,
    has_tool_calls: bool,
}

#[pymethods]
impl StreamAccumulator {
    #[new]
    fn new() -> Self {
        StreamAccumulator {
            content_parts: Vec::new(),
            tool_calls_map: HashMap::new(),
            reasoning_parts: Vec::new(),
            finish_reason: None,
            model_name: None,
            last_id_at_idx: HashMap::new(),
            active_slot_by_idx: HashMap::new(),
            has_content: false,
            has_tool_calls: false,
        }
    }

    /// Add a text content delta.
    fn add_content(&mut self, text: &str) {
        self.content_parts.push(text.to_string());
        self.has_content = true;
    }

    /// Add a reasoning delta.
    fn add_reasoning(&mut self, text: &str) {
        self.reasoning_parts.push(text.to_string());
    }

    /// Add a tool call delta. `index`, `tc_id`, `name`, `arguments` map to
    /// the OpenAI delta.tool_calls[*] fields.  `name` is assigned (not appended);
    /// `arguments` is concatenated across deltas.
    #[pyo3(signature = (index, tc_id, name=None, arguments=None))]
    fn add_tool_delta(
        &mut self,
        index: usize,
        tc_id: &str,
        name: Option<&str>,
        arguments: Option<&str>,
    ) {
        self.has_tool_calls = true;

        // Ollama workaround: detect reuse of raw index with different id
        let slot = if !tc_id.is_empty() {
            if let Some(last_id) = self.last_id_at_idx.get(&index) {
                if *last_id != tc_id {
                    // New tool call at same index → allocate fresh slot
                    let new_slot = self.tool_calls_map.keys().max().map(|m| m + 1).unwrap_or(0);
                    self.active_slot_by_idx.insert(index, new_slot);
                    new_slot
                } else {
                    *self.active_slot_by_idx.get(&index).unwrap_or(&index)
                }
            } else {
                self.last_id_at_idx.insert(index, tc_id.to_string());
                *self.active_slot_by_idx.get(&index).unwrap_or(&index)
            }
        } else {
            *self.active_slot_by_idx.get(&index).unwrap_or(&index)
        };

        let entry = self.tool_calls_map.entry(slot).or_default();
        if !tc_id.is_empty() {
            entry.id = tc_id.to_string();
        }
        // name is assigned, not appended (OpenAI sends full name each time)
        if let Some(n) = name {
            if !n.is_empty() {
                entry.name = n.to_string();
            }
        }
        // arguments are concatenated across deltas
        if let Some(a) = arguments {
            entry.arguments.push_str(a);
        }
    }

    /// Record model name (from chunk.model or usage chunk).
    fn set_model(&mut self, name: &str) {
        self.model_name = Some(name.to_string());
    }

    /// Record finish_reason (from final chunk).
    fn set_finish_reason(&mut self, reason: &str) {
        self.finish_reason = Some(reason.to_string());
    }

    /// Repair malformed JSON tool call arguments.
    /// Mirrors Python's agent/message_sanitization.py:_repair_tool_call_arguments.
    fn repair_arguments(&self, raw: &str) -> String {
        let raw = raw.trim();
        if raw.is_empty() || raw == "None" {
            return "{}".to_string();
        }

        // Pass 0: strict=false — accept control characters, re-serialize clean
        if let Ok(parsed) = serde_json::from_str::<serde_json::Value>(raw) {
            return serde_json::to_string(&parsed).unwrap_or_else(|_| "{}".to_string());
        }

        // Pass 1: strip trailing commas before } or ]
        let re = fancy_regex::Regex::new(r",\s*([}\]])").unwrap();
        let mut fixed = re.replace_all(raw, "${1}").to_string();

        // Pass 2: close unclosed structures
        let open_curly = fixed.matches('{').count().saturating_sub(fixed.matches('}').count());
        let open_bracket = fixed.matches('[').count().saturating_sub(fixed.matches(']').count());
        if open_curly > 0 {
            fixed.push_str(&"}".repeat(open_curly));
        }
        if open_bracket > 0 {
            fixed.push_str(&"]".repeat(open_bracket));
        }

        // Pass 3: remove excess closing braces/brackets (max 50 iterations)
        for _ in 0..50 {
            if serde_json::from_str::<serde_json::Value>(&fixed).is_ok() {
                return fixed;
            }
            if fixed.ends_with('}') && fixed.matches('}').count() > fixed.matches('{').count() {
                fixed.pop();
            } else if fixed.ends_with(']') && fixed.matches(']').count() > fixed.matches('[').count() {
                fixed.pop();
            } else {
                break;
            }
        }

        // Last chance: try parsing the repaired result
        if serde_json::from_str::<serde_json::Value>(&fixed).is_ok() {
            return fixed;
        }

        // All repairs failed — return empty object (like Python's fallback)
        "{}".to_string()
    }

    /// Finalize and return the assembled result as a 5-tuple:
    /// (full_content, tool_calls_json, reasoning, finish_reason, model_name)
    /// tool_calls_json is a valid JSON string built with serde_json.
    fn finalize(&self) -> (String, String, String, Option<String>, Option<String>) {
        let full_content = if self.has_content {
            self.content_parts.join("")
        } else {
            String::new()
        };

        let tool_calls_json = if self.has_tool_calls {
            let mut slots: Vec<usize> = self.tool_calls_map.keys().copied().collect();
            slots.sort();
            let entries: Vec<serde_json::Value> = slots
                .iter()
                .filter_map(|s| self.tool_calls_map.get(s))
                .filter(|e| !e.name.is_empty() || !e.arguments.is_empty())
                .map(|e| {
                    let args = self.repair_arguments(&e.arguments);
                    let name = if e.name.is_empty() { "?" } else { &e.name };
                    serde_json::json!({
                        "id": e.id,
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": args,
                        }
                    })
                })
                .collect();
            serde_json::Value::Array(entries).to_string()
        } else {
            String::new()
        };

        let reasoning = if self.reasoning_parts.is_empty() {
            String::new()
        } else {
            self.reasoning_parts.join("")
        };

        (full_content, tool_calls_json, reasoning, self.finish_reason.clone(), self.model_name.clone())
    }

    /// Return true if any content has been accumulated.
    fn has_any_content(&self) -> bool {
        self.has_content || self.has_tool_calls
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_content_accumulation() {
        let mut acc = StreamAccumulator::new();
        acc.add_content("Hello ");
        acc.add_content("world");
        let (content, tools, reasoning, finish, model) = acc.finalize();
        assert_eq!(content, "Hello world");
        assert!(tools.is_empty());
        assert!(reasoning.is_empty());
        assert!(finish.is_none());
        assert!(model.is_none());
    }

    #[test]
    fn test_tool_call_accumulation() {
        let mut acc = StreamAccumulator::new();
        // Tool call index 0: name first, then arguments
        acc.add_tool_delta(0, "call_1", Some("search"), None);
        acc.add_tool_delta(0, "call_1", None, Some(r#"{"query""#));
        acc.add_tool_delta(0, "call_1", None, Some(r#":"Rust""#));
        acc.add_tool_delta(0, "call_1", None, Some("}"));
        acc.set_finish_reason("tool_calls");

        let (content, tools, _reasoning, finish, _model) = acc.finalize();
        assert!(content.is_empty());
        assert!(tools.contains("search"));
        assert!(tools.contains(r#""arguments":"{\"query\":\"Rust\"}""#));
        assert_eq!(finish.as_deref(), Some("tool_calls"));
    }

    #[test]
    fn test_reasoning_accumulation() {
        let mut acc = StreamAccumulator::new();
        acc.add_reasoning("Let me think...");
        acc.add_reasoning("Done.");
        let (_c, _t, reasoning, _f, _m) = acc.finalize();
        assert_eq!(reasoning, "Let me think...Done.");
    }

    #[test]
    fn test_ollama_index_reuse() {
        let mut acc = StreamAccumulator::new();
        // Ollama sends two tool calls both at index 0 with different ids
        acc.add_tool_delta(0, "call_a", Some("search"), None);
        acc.add_tool_delta(0, "call_b", Some("read_file"), Some("{}"));

        let (_c, tools, _r, _f, _m) = acc.finalize();
        // Should have two separate tool calls
        assert!(tools.contains("search"));
        assert!(tools.contains("read_file"));
    }

    #[test]
    fn test_model_and_finish() {
        let mut acc = StreamAccumulator::new();
        acc.add_content("ok");
        acc.set_model("claude-opus-4-20250514");
        acc.set_finish_reason("stop");
        let (_c, _t, _r, finish, model) = acc.finalize();
        assert_eq!(model.as_deref(), Some("claude-opus-4-20250514"));
        assert_eq!(finish.as_deref(), Some("stop"));
    }
}
