//! Automation Blueprint YAML parsing and validation (HP-304).
//! Returns normalized JSON on success, error message on failure.

use pyo3::prelude::*;
use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize, Deserialize)]
struct BlueprintParam {
    #[serde(rename = "type")]
    param_type: String,
    description: String,
    #[serde(default)]
    default: Option<serde_yaml::Value>,
}

#[derive(Debug, Serialize, Deserialize)]
struct BlueprintDef {
    id: String,
    name: String,
    description: String,
    #[serde(default)]
    params: std::collections::HashMap<String, BlueprintParam>,
    schedule: Option<String>,
    prompt_template: String,
    #[serde(default)]
    skills: Vec<String>,
    #[serde(default)]
    delivery: Option<String>,
    #[serde(default)]
    category: Option<String>,
}

/// Validate and normalize a blueprint YAML string.
/// Returns normalized JSON on success, raises ValueError on failure.
#[pyfunction]
pub fn validate_blueprint_yaml(yaml_content: &str) -> PyResult<String> {
    let bp: BlueprintDef = match serde_yaml::from_str(yaml_content) {
        Ok(b) => b,
        Err(e) => {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "Invalid blueprint YAML: {}",
                e
            )));
        }
    };

    if bp.id.is_empty() {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "Blueprint missing 'id'",
        ));
    }
    if bp.name.is_empty() {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "Blueprint missing 'name'",
        ));
    }
    if bp.prompt_template.is_empty() {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "Blueprint missing 'prompt_template'",
        ));
    }
    if bp.id.contains(' ') || bp.id.contains('/') {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "Blueprint id '{}' must be kebab-case without spaces or slashes",
            bp.id
        )));
    }

    serde_json::to_string(&bp).map_err(|e| {
        pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization error: {}", e))
    })
}

/// Validate parameters for blueprint instantiation.
/// Returns None on success, error string on failure.
#[pyfunction]
pub fn validate_blueprint_params(yaml_content: &str, params_json: &str) -> Option<String> {
    let bp: BlueprintDef = match serde_yaml::from_str(yaml_content) {
        Ok(b) => b,
        Err(e) => return Some(format!("Invalid blueprint YAML: {}", e)),
    };

    let provided: std::collections::HashMap<String, serde_yaml::Value> =
        match serde_json::from_str(params_json) {
            Ok(p) => p,
            Err(e) => return Some(format!("Invalid params JSON: {}", e)),
        };

    for (name, param) in &bp.params {
        if !provided.contains_key(name) && param.default.is_none() {
            return Some(format!(
                "Missing required parameter: '{}' ({})",
                name, param.description
            ));
        }
        if let Some(value) = provided.get(name) {
            match param.param_type.as_str() {
                "string" => {
                    if !value.is_string() {
                        return Some(format!("Parameter '{}' must be a string", name));
                    }
                }
                "number" => {
                    if !value.is_number() {
                        return Some(format!("Parameter '{}' must be a number", name));
                    }
                }
                "boolean" => {
                    if !value.is_bool() {
                        return Some(format!("Parameter '{}' must be a boolean", name));
                    }
                }
                _ => {}
            }
        }
    }

    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_valid_blueprint() {
        let yaml = r#"
id: daily-news-digest
name: Daily News Digest
description: Summarize top news every morning
schedule: "0 8 * * *"
prompt_template: "Summarize today's top {{topic}} news"
params:
  topic:
    type: string
    description: News category
    default: "technology"
skills: []
"#;
        let result = validate_blueprint_yaml(yaml);
        assert!(result.is_ok());
        let json = result.unwrap();
        assert!(json.contains("daily-news-digest"));
    }

    #[test]
    fn test_missing_id() {
        let yaml = "name: Test\nprompt_template: hello";
        assert!(validate_blueprint_yaml(yaml).is_err());
    }

    #[test]
    fn test_invalid_id_format() {
        let yaml = r#"
id: "bad id"
name: Test
description: Test
prompt_template: hello
"#;
        assert!(validate_blueprint_yaml(yaml).is_err());
    }

    #[test]
    fn test_params_validation_missing() {
        let yaml = r#"
id: test
name: Test
description: Test
prompt_template: "{{topic}}"
params:
  topic:
    type: string
    description: A topic
"#;
        assert_eq!(
            validate_blueprint_params(yaml, "{}"),
            Some("Missing required parameter: 'topic' (A topic)".to_string())
        );
    }

    #[test]
    fn test_params_validation_ok() {
        let yaml = r#"
id: test
name: Test
description: Test
prompt_template: "{{topic}}"
params:
  topic:
    type: string
    description: A topic
"#;
        assert_eq!(validate_blueprint_params(yaml, r#"{"topic": "ai"}"#), None);
    }
}
