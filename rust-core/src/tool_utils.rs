//! Tool & prompt utility functions — pure computation ported from Python.
//!
//! Task 2: ``file_mutation_result_landed`` from ``agent/tool_result_classification.py``.
//! Skill frontmatter validation for ``tools/skill_manager_tool.py``.
//!
//! (Tasks 3/5/6 — strip_yaml_frontmatter / truncate_content / paths_overlap /
//! canonical_tool_args — were ported but never wired on the Python side and
//! were removed as dead exports.)

use pyo3::prelude::*;

// ── Task 2: File mutation result classification ────────────────────────────

/// Return True when a file mutation result proves the write landed.
#[pyfunction]
pub fn file_mutation_result_landed_rs(tool_name: &str, result: &Bound<'_, PyAny>) -> PyResult<bool> {
    if tool_name != "write_file" && tool_name != "patch" {
        return Ok(false);
    }
    let result_str: String = match result.extract() {
        Ok(s) => s,
        Err(_) => return Ok(false),
    };
    let data: serde_json::Value = match serde_json::from_str(result_str.trim()) {
        Ok(v) => v,
        Err(_) => return Ok(false),
    };
    let obj = match data.as_object() {
        Some(o) => o,
        None => return Ok(false),
    };
    if obj.contains_key("error") {
        return Ok(false);
    }
    if tool_name == "write_file" {
        return Ok(obj.contains_key("bytes_written"));
    }
    if tool_name == "patch" {
        return Ok(obj.get("success").and_then(|v| v.as_bool()) == Some(true));
    }
    Ok(false)
}

/// Validate SKILL.md frontmatter structure. Returns None when valid, else error text.
#[pyfunction]
pub fn validate_skill_frontmatter_rs(content: &str) -> Option<String> {
    const MAX_DESCRIPTION_LEN: usize = 1024;
    let trimmed = content.trim();
    if trimmed.is_empty() {
        return Some("Content cannot be empty.".to_string());
    }
    if !trimmed.starts_with("---") {
        return Some(
            "SKILL.md must start with YAML frontmatter (---). See existing skills for format."
                .to_string(),
        );
    }
    let rest = &trimmed[3..];
    let end = match rest.find("\n---") {
        Some(i) => i,
        None => {
            return Some(
                "SKILL.md frontmatter is not closed. Ensure you have a closing '---' line."
                    .to_string(),
            );
        }
    };
    let yaml_block = &rest[..end];
    let body = rest[end + 4..].trim_start_matches('\n').trim();
    if body.is_empty() {
        return Some(
            "SKILL.md must have content after the frontmatter (instructions, procedures, etc.)."
                .to_string(),
        );
    }

    let mut has_name = false;
    let mut has_description = false;
    for line in yaml_block.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        if let Some(val) = line.strip_prefix("name:") {
            has_name = !val.trim().is_empty();
        } else if let Some(val) = line.strip_prefix("description:") {
            let desc = val.trim();
            if desc.is_empty() {
                has_description = false;
            } else if desc.len() > MAX_DESCRIPTION_LEN {
                return Some(format!(
                    "Description exceeds {MAX_DESCRIPTION_LEN} characters."
                ));
            } else {
                has_description = true;
            }
        }
    }
    if !has_name {
        return Some("Frontmatter must include 'name' field.".to_string());
    }
    if !has_description {
        return Some("Frontmatter must include 'description' field.".to_string());
    }
    None
}

// ── Tests ───────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_validate_skill_frontmatter_ok() {
        let content = "---\nname: foo\ndescription: Short skill.\n---\n# Foo\nBody.\n";
        assert!(validate_skill_frontmatter_rs(content).is_none());
    }

    #[test]
    fn test_validate_skill_frontmatter_missing_name() {
        let content = "---\ndescription: Short skill.\n---\n# Foo\nBody.\n";
        assert!(validate_skill_frontmatter_rs(content).is_some());
    }
}
