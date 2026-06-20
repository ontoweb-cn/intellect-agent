//! API error classification for smart failover and recovery.
//!
//! Phase 3: Rust implementation of ``agent/error_classifier.py``.
//! Provides ``FailoverReason``, ``ClassifiedError``, and ``classify_api_error_rs``.

use pyo3::prelude::*;
use pyo3::types::PyDict;

// Helpers for the common "get item + downcast" pattern
// Bound<PyDict>::get_item returns PyResult<Option<Bound<'py, PyAny>>>
// Bound<PyAny>::get_item also returns PyResult<Option<Bound<'py, PyAny>>>

fn try_get_dict<'py>(d: &Bound<'py, PyAny>, key: &str) -> Option<Bound<'py, PyDict>> {
    d.get_item(key).ok().and_then(|v| v.downcast::<PyDict>().ok().cloned())
}

fn try_get_str(d: &Bound<'_, PyAny>, key: &str) -> Option<String> {
    d.get_item(key).ok().and_then(|v| v.extract::<String>().ok())
}

fn try_get_i64(d: &Bound<'_, PyAny>, key: &str) -> Option<i64> {
    d.get_item(key).ok().and_then(|v| v.extract::<i64>().ok())
}

// ── FailoverReason ────────────────────────────────────────────────────────

#[pyclass(name = "FailoverReason")]
#[derive(Clone, PartialEq, Eq, Hash)]
pub struct FailoverReason {
    #[pyo3(get)]
    pub value: String,
}

#[pymethods]
impl FailoverReason {
    #[new]
    fn new(value: &str) -> Self {
        FailoverReason { value: value.to_string() }
    }

    fn __eq__(&self, other: &Bound<'_, PyAny>) -> PyResult<bool> {
        if let Ok(other) = other.extract::<PyRef<FailoverReason>>() {
            return Ok(self.value == other.value);
        }
        if let Ok(s) = other.extract::<String>() {
            return Ok(self.value == s);
        }
        Ok(false)
    }

    fn __hash__(&self) -> u64 {
        let mut h: u64 = 5381;
        for b in self.value.bytes() {
            h = h.wrapping_mul(33).wrapping_add(b as u64);
        }
        h
    }

    fn __repr__(&self) -> String {
        format!("FailoverReason.{}", self.value)
    }

    fn __str__(&self) -> String {
        self.value.clone()
    }

    #[classattr] fn auth() -> Self { Self { value: "auth".into() } }
    #[classattr] fn auth_permanent() -> Self { Self { value: "auth_permanent".into() } }
    #[classattr] fn billing() -> Self { Self { value: "billing".into() } }
    #[classattr] fn rate_limit() -> Self { Self { value: "rate_limit".into() } }
    #[classattr] fn overloaded() -> Self { Self { value: "overloaded".into() } }
    #[classattr] fn server_error() -> Self { Self { value: "server_error".into() } }
    #[classattr] fn timeout() -> Self { Self { value: "timeout".into() } }
    #[classattr] fn context_overflow() -> Self { Self { value: "context_overflow".into() } }
    #[classattr] fn payload_too_large() -> Self { Self { value: "payload_too_large".into() } }
    #[classattr] fn image_too_large() -> Self { Self { value: "image_too_large".into() } }
    #[classattr] fn model_not_found() -> Self { Self { value: "model_not_found".into() } }
    #[classattr] fn provider_policy_blocked() -> Self { Self { value: "provider_policy_blocked".into() } }
    #[classattr] fn content_policy_blocked() -> Self { Self { value: "content_policy_blocked".into() } }
    #[classattr] fn format_error() -> Self { Self { value: "format_error".into() } }
    #[classattr] fn invalid_encrypted_content() -> Self { Self { value: "invalid_encrypted_content".into() } }
    #[classattr] fn multimodal_tool_content_unsupported() -> Self { Self { value: "multimodal_tool_content_unsupported".into() } }
    #[classattr] fn thinking_signature() -> Self { Self { value: "thinking_signature".into() } }
    #[classattr] fn long_context_tier() -> Self { Self { value: "long_context_tier".into() } }
    #[classattr] fn oauth_long_context_beta_forbidden() -> Self { Self { value: "oauth_long_context_beta_forbidden".into() } }
    #[classattr] fn llama_cpp_grammar_pattern() -> Self { Self { value: "llama_cpp_grammar_pattern".into() } }
    #[classattr] fn unknown() -> Self { Self { value: "unknown".into() } }
}

// ── ClassifiedError ───────────────────────────────────────────────────────

#[pyclass(name = "ClassifiedError")]
#[derive(Clone)]
pub struct ClassifiedError {
    #[pyo3(get)] pub reason: Py<FailoverReason>,
    #[pyo3(get)] pub status_code: Option<i32>,
    #[pyo3(get)] pub provider: Option<String>,
    #[pyo3(get)] pub model: Option<String>,
    #[pyo3(get)] pub message: String,
    #[pyo3(get)] pub retryable: bool,
    #[pyo3(get)] pub should_compress: bool,
    #[pyo3(get)] pub should_rotate_credential: bool,
    #[pyo3(get)] pub should_fallback: bool,
}

#[pymethods]
impl ClassifiedError {
    #[new]
    #[pyo3(signature = (reason, status_code=None, provider=None, model=None, message=String::new(), retryable=None, should_compress=None, should_rotate_credential=None, should_fallback=None))]
    fn new(
        reason: Py<FailoverReason>,
        status_code: Option<i32>,
        provider: Option<String>,
        model: Option<String>,
        message: String,
        retryable: Option<bool>,
        should_compress: Option<bool>,
        should_rotate_credential: Option<bool>,
        should_fallback: Option<bool>,
    ) -> Self {
        ClassifiedError {
            reason,
            status_code,
            provider,
            model,
            message,
            retryable: retryable.unwrap_or(true),
            should_compress: should_compress.unwrap_or(false),
            should_rotate_credential: should_rotate_credential.unwrap_or(false),
            should_fallback: should_fallback.unwrap_or(false),
        }
    }

    #[getter]
    fn is_auth(&self) -> bool {
        Python::with_gil(|py| {
            let reason = self.reason.bind(py);
            let val: String = reason.getattr("value").ok().and_then(|v| v.extract().ok()).unwrap_or_default();
            val == "auth" || val == "auth_permanent"
        })
    }

    fn __repr__(&self) -> String {
        Python::with_gil(|py| {
            let r = self.reason.bind(py).repr().ok().map(|v| v.to_string()).unwrap_or_default();
            format!("ClassifiedError(reason={r}, status={:?})", self.status_code)
        })
    }
}

// ── Pattern lists ─────────────────────────────────────────────────────────

const BILLING: &[&str] = &[
    "insufficient credits", "insufficient_quota", "insufficient balance",
    "credit balance", "credits exhausted", "credits have been exhausted",
    "no usable credits", "top up your credits", "payment required",
    "billing hard limit", "exceeded your current quota", "account is deactivated",
    "plan does not include", "out of funds", "run out of funds",
    "balance_depleted", "model_not_supported_on_free_tier", "not available on the free tier",
];
const RATE_LIMIT: &[&str] = &[
    "rate limit", "rate_limit", "too many requests", "throttled",
    "requests per minute", "tokens per minute", "requests per day",
    "try again in", "please retry after", "resource_exhausted",
    "rate increased too quickly", "throttlingexception",
    "too many concurrent requests", "servicequotaexceededexception",
];
const USAGE_LIMIT: &[&str] = &["usage limit", "quota", "limit exceeded", "key limit exceeded"];
const USAGE_TRANSIENT: &[&str] = &[
    "try again", "retry", "resets at", "reset in", "wait",
    "requests remaining", "periodic", "window",
];
const PAYLOAD_TOO_LARGE: &[&str] = &["request entity too large", "payload too large", "error code: 413"];
const IMAGE_TOO_LARGE: &[&str] = &["image exceeds", "image too large", "image_too_large", "image size exceeds"];
const MULTIMODAL_TOOL: &[&str] = &[
    "text is not set", "tool message content must be a string",
    "tool content must be a string", "tool message must be a string",
    "expected string, got list", "expected string, got array",
    "tool_call.content must be string",
];
const CONTEXT_OVERFLOW: &[&str] = &[
    "context length", "context size", "maximum context", "token limit",
    "too many tokens", "reduce the length", "exceeds the limit", "context window",
    "prompt is too long", "prompt exceeds max length", "max_tokens",
    "maximum number of tokens", "exceeds the max_model_len", "max_model_len",
    "prompt length", "input is too long", "maximum model length",
    "context length exceeded", "truncating input", "slot context", "n_ctx_slot",
    "超过最大长度", "上下文长度", "max input token", "input token",
    "exceeds the maximum number of input tokens",
];
const MODEL_NOT_FOUND: &[&str] = &[
    "is not a valid model", "invalid model", "model not found",
    "model_not_found", "does not exist", "no such model",
    "unknown model", "unsupported model",
];
const REQUEST_VALIDATION: &[&str] = &[
    "unknown parameter", "unsupported parameter", "unrecognized request argument",
    "invalid_request_error", "unknown_parameter", "unsupported_parameter",
];
const PROVIDER_POLICY: &[&str] = &[
    "no endpoints available matching your guardrail",
    "no endpoints available matching your data policy",
    "no endpoints found matching your data policy",
];
const CONTENT_POLICY: &[&str] = &[
    "flagged for possible cybersecurity risk", "trusted access for cyber",
    "violates our usage policies", "violates openai's usage policies",
    "your request was flagged by", "prompt was flagged by our safety",
    "responses cannot be generated due to safety", "content_filter",
    "responsibleaipolicyviolation",
];
const AUTH: &[&str] = &[
    "invalid api key", "invalid_api_key", "authentication", "unauthorized",
    "forbidden", "invalid token", "token expired", "token revoked", "access denied",
];
const TIMEOUT_MSG: &[&str] = &[
    "timed out", "turn timed out", "request timed out", "deadline exceeded",
    "operation timed out", "upstream timed out",
];
const TRANSPORT_TYPES: &[&str] = &[
    "ReadTimeout", "ConnectTimeout", "PoolTimeout", "ConnectError",
    "RemoteProtocolError", "ConnectionError", "ConnectionResetError",
    "ConnectionAbortedError", "BrokenPipeError", "TimeoutError", "ReadError",
    "ServerDisconnectedError", "SSLError", "SSLZeroReturnError", "SSLWantReadError",
    "SSLWantWriteError", "SSLEOFError", "SSLSyscallError",
    "APIConnectionError", "APITimeoutError",
];
const SERVER_DISCONNECT: &[&str] = &[
    "server disconnected", "peer closed connection", "connection reset by peer",
    "connection was closed", "network connection lost", "unexpected eof",
    "incomplete chunked read",
];
const SSL_TRANSIENT: &[&str] = &[
    "bad record mac", "ssl alert", "tls alert", "ssl handshake failure",
    "tlsv1 alert", "sslv3 alert", "bad_record_mac", "ssl_alert",
    "tls_alert", "tls_alert_internal_error", "[ssl:",
];

#[inline] fn any_match(haystack: &str, patterns: &[&str]) -> bool {
    patterns.iter().any(|p| haystack.contains(p))
}

// ── Helpers: extract from Python exception ────────────────────────────────

fn extract_status_code(error: &Bound<'_, PyAny>) -> Option<i32> {
    let mut current = error.clone();
    for _ in 0..5 {
        if let Ok(code) = current.getattr("status_code").and_then(|v| v.extract::<i32>()) {
            return Some(code);
        }
        if let Ok(code) = current.getattr("status").and_then(|v| v.extract::<i32>()) {
            if (100..600).contains(&code) {
                return Some(code);
            }
        }
        let cause = current.getattr("__cause__")
            .or_else(|_| current.getattr("__context__"));
        match cause {
            Ok(c) if c.is_none() || c.is(&current) => break,
            Ok(c) => current = c,
            Err(_) => break,
        }
    }
    None
}

fn extract_error_body(error: &Bound<'_, PyAny>) -> Option<Py<PyDict>> {
    if let Ok(body) = error.getattr("body") {
        if let Ok(dict) = body.downcast::<PyDict>() {
            return Some(dict.clone().unbind());
        }
    }
    if let Ok(response) = error.getattr("response") {
        if let Ok(json) = response.call_method0("json") {
            if let Ok(dict) = json.downcast::<PyDict>() {
                return Some(dict.clone().unbind());
            }
        }
    }
    None
}

fn extract_error_message(error: &Bound<'_, PyAny>, body_dict: Option<&Bound<'_, PyDict>>) -> String {
    if let Some(body) = body_dict {
        if let Some(err_dict) = try_get_dict(body, "error") {
            if let Some(msg) = try_get_str(&err_dict, "message") {
                let t = msg.trim();
                if !t.is_empty() {
                    let end = std::cmp::min(500, t.len());
                    return t[..end].to_string();
                }
            }
        }
        if let Some(msg) = try_get_str(body, "message") {
            let t = msg.trim();
            if !t.is_empty() {
                let end = std::cmp::min(500, t.len());
                return t[..end].to_string();
            }
        }
    }
    if let Ok(s) = error.str() {
        let s = s.to_string();
        let end = std::cmp::min(500, s.len());
        return s[..end].to_string();
    }
    String::new()
}

fn extract_error_code(py: Python<'_>, body_dict: Option<&Bound<'_, PyDict>>) -> String {
    let body = match body_dict {
        Some(b) => b,
        None => return String::new(),
    };

    // Try error.code or error.type inside error object
    if let Some(err_obj) = try_get_dict(body, "error") {
        if let Some(code) = try_get_str(&err_obj, "code") {
            let t = code.trim();
            if !t.is_empty() && t != "400" { return t.to_string(); }
        }
        if let Some(typ) = try_get_str(&err_obj, "type") {
            let t = typ.trim();
            if !t.is_empty() && t != "400" { return t.to_string(); }
        }
        // Peek inside error.message for nested JSON
        if let Some(msg) = try_get_str(&err_obj, "message") {
            if msg.trim().starts_with('{') {
                if let Ok(json_mod) = py.import_bound("json") {
                    if let Ok(inner) = json_mod.call_method1("loads", (msg.trim(),)) {
                        if let Some(inner_err) = try_get_dict(&inner, "error") {
                            if let Some(c) = try_get_str(&inner_err, "code") {
                                let t = c.trim();
                                if !t.is_empty() { return t.to_string(); }
                            }
                        }
                    }
                }
            }
        }
    }

    // Top-level code / error_code
    if let Some(s) = try_get_str(body, "code") {
        let t = s.trim();
        if !t.is_empty() && t != "400" { return t.to_string(); }
    }
    if let Some(s) = try_get_str(body, "error_code") {
        let t = s.trim();
        if !t.is_empty() && t != "400" { return t.to_string(); }
    }
    if let Some(n) = try_get_i64(body, "code") {
        let s = n.to_string();
        if s != "400" { return s; }
    }
    String::new()
}

fn extract_metadata_msg(py: Python<'_>, err_dict: &Bound<'_, PyAny>) -> String {
    let meta = match try_get_dict(err_dict, "metadata") {
        Some(d) => d,
        None => return String::new(),
    };
    let raw = match try_get_str(&meta, "raw") {
        Some(s) => s,
        None => return String::new(),
    };
    let trimmed = raw.trim();
    if trimmed.is_empty() { return String::new(); }
    if let Ok(json_mod) = py.import_bound("json") {
        if let Ok(inner) = json_mod.call_method1("loads", (trimmed,)) {
            if let Some(inner_err) = try_get_dict(&inner, "error") {
                if let Some(msg) = try_get_str(&inner_err, "message") {
                    return msg.to_lowercase();
                }
            }
        }
    }
    String::new()
}

// ── Internal: make reason ─────────────────────────────────────────────────

fn make_reason(py: Python<'_>, value: &str) -> Py<FailoverReason> {
    Py::new(py, FailoverReason { value: value.to_string() }).unwrap()
}

fn build_result(
    _py: Python<'_>,
    reason: Py<FailoverReason>,
    status_code: Option<i32>,
    provider: Option<String>,
    model: Option<String>,
    message: String,
    retryable: bool,
    should_compress: bool,
    should_rotate_credential: bool,
    should_fallback: bool,
) -> ClassifiedError {
    ClassifiedError {
        reason, status_code, provider, model, message,
        retryable, should_compress, should_rotate_credential, should_fallback,
    }
}

// ── Sub-classifiers ───────────────────────────────────────────────────────

#[allow(clippy::too_many_arguments)]
fn classify_400(
    error_msg: &str, error_code: &str, body_msg: &str, py: Python<'_>,
    approx_tokens: i64, context_length: i64, num_messages: i64,
) -> ClassifiedError {
    if any_match(error_msg, MULTIMODAL_TOOL) {
        return build_result(py, make_reason(py, "multimodal_tool_content_unsupported"), None, None, None, String::new(), true, false, false, false);
    }
    if any_match(error_msg, IMAGE_TOO_LARGE) {
        return build_result(py, make_reason(py, "image_too_large"), None, None, None, String::new(), true, false, false, false);
    }
    let code_lower = error_code.to_lowercase();
    if code_lower == "invalid_encrypted_content"
        || error_msg.contains("invalid_encrypted_content")
        || (error_msg.contains("encrypted content for item") && error_msg.contains("could not be verified"))
    {
        return build_result(py, make_reason(py, "invalid_encrypted_content"), None, None, None, String::new(), true, false, false, false);
    }
    if any_match(error_msg, CONTEXT_OVERFLOW) {
        return build_result(py, make_reason(py, "context_overflow"), None, None, None, String::new(), true, true, false, false);
    }
    if any_match(error_msg, PROVIDER_POLICY) {
        return build_result(py, make_reason(py, "provider_policy_blocked"), None, None, None, String::new(), false, false, false, false);
    }
    if any_match(error_msg, MODEL_NOT_FOUND) {
        return build_result(py, make_reason(py, "model_not_found"), None, None, None, String::new(), false, false, false, true);
    }
    if any_match(error_msg, RATE_LIMIT) {
        return build_result(py, make_reason(py, "rate_limit"), None, None, None, String::new(), true, false, true, true);
    }
    if any_match(error_msg, BILLING) {
        return build_result(py, make_reason(py, "billing"), None, None, None, String::new(), false, false, true, true);
    }
    // Generic 400 + large session → probable context overflow.
    // Analogous to the Python classifier's heuristic: when the body message is
    // short/generic ("Error" or empty) and the session is large, it's likely a
    // context overflow that the provider didn't signal explicitly.
    let is_generic = body_msg.len() < 30 || body_msg == "error" || body_msg.is_empty();
    let is_large = approx_tokens > context_length * 4 / 10  // 0.4 × context_length (integer math)
        || (context_length <= 256_000 && (approx_tokens > 80_000 || num_messages > 80));
    if is_generic && is_large {
        return build_result(py, make_reason(py, "context_overflow"), None, None, None, String::new(), true, true, false, false);
    }
    build_result(py, make_reason(py, "format_error"), None, None, None, String::new(), false, false, false, true)
}

fn classify_by_error_code(code: &str, py: Python<'_>) -> Option<ClassifiedError> {
    let c = code.to_lowercase();
    if matches!(c.as_str(), "resource_exhausted" | "throttled" | "rate_limit_exceeded") {
        return Some(build_result(py, make_reason(py, "rate_limit"), None, None, None, String::new(), true, false, true, false));
    }
    if matches!(c.as_str(), "insufficient_quota" | "billing_not_active" | "payment_required"
        | "insufficient_credits" | "no_usable_credits" | "balance_depleted" | "model_not_supported_on_free_tier") {
        return Some(build_result(py, make_reason(py, "billing"), None, None, None, String::new(), false, false, true, true));
    }
    if matches!(c.as_str(), "model_not_found" | "model_not_available" | "invalid_model") {
        return Some(build_result(py, make_reason(py, "model_not_found"), None, None, None, String::new(), false, false, false, true));
    }
    if matches!(c.as_str(), "context_length_exceeded" | "max_tokens_exceeded") {
        return Some(build_result(py, make_reason(py, "context_overflow"), None, None, None, String::new(), true, true, false, false));
    }
    if c == "invalid_encrypted_content" {
        return Some(build_result(py, make_reason(py, "invalid_encrypted_content"), None, None, None, String::new(), true, false, false, false));
    }
    None
}

// ── Main classifier ───────────────────────────────────────────────────────

#[pyfunction]
#[allow(clippy::too_many_arguments)]
pub fn classify_api_error_rs(
    error: &Bound<'_, PyAny>,
    provider: &str,
    model: &str,
    approx_tokens: i64,
    context_length: i64,
    num_messages: i64,
) -> PyResult<ClassifiedError> {
    let py = error.py();
    let mut status_code = extract_status_code(error);

    // Get error type name
    let error_type: String = error.get_type()
        .name()
        .map(|n| n.to_string())
        .unwrap_or_default();

    // RateLimitError without status_code → force 429
    if status_code.is_none() && error_type == "RateLimitError" {
        status_code = Some(429);
    }

    let body_py = extract_error_body(error);

    // Pre-compute in GIL context
    let (error_code, error_msg, message_str, body_msg) = Python::with_gil(|py| {
        let body_bound = body_py.as_ref().map(|b| b.bind(py).clone());
        let body_ref = body_bound.as_ref();

        let error_code = extract_error_code(py, body_ref);
        let message_str = extract_error_message(error, body_ref);

        // Build error message for pattern matching (combine raw msg + body msg + metadata)
        let raw_msg: String = error.str()
            .map(|s| s.to_string().to_lowercase())
            .unwrap_or_default();

        let mut combined = raw_msg.clone();
        let mut body_err_msg = String::new();

        if let Some(b) = body_ref {
            if let Some(err_obj) = try_get_dict(b, "error") {
                if let Some(body_msg) = try_get_str(&err_obj, "message") {
                    let lower = body_msg.to_lowercase();
                    if !lower.is_empty() && !combined.contains(&lower) {
                        combined.push(' ');
                        combined.push_str(&lower);
                    }
                    body_err_msg = lower;
                }
                let meta_msg = extract_metadata_msg(py, &err_obj);
                if !meta_msg.is_empty() && !combined.contains(&meta_msg) {
                    combined.push(' ');
                    combined.push_str(&meta_msg);
                }
            }
            if body_err_msg.is_empty() {
                if let Some(body_msg) = try_get_str(b, "message") {
                    let lower = body_msg.to_lowercase();
                    if !lower.is_empty() && !combined.contains(&lower) {
                        combined.push(' ');
                        combined.push_str(&lower);
                    }
                    body_err_msg = lower;
                }
            }
        }

        (error_code, combined, message_str, body_err_msg)
    });

    let prov = Some(provider.to_string());
    let mdl = Some(model.to_string());

    let set = |r: Py<FailoverReason>| build_result(py, r, status_code, prov.clone(), mdl.clone(), message_str.clone(), true, false, false, false);

    // ── 1. Provider-specific patterns ────────────────────────────────

    if any_match(&error_msg, CONTENT_POLICY) {
        let mut r = set(make_reason(py, "content_policy_blocked"));
        r.retryable = false;
        r.should_fallback = true;
        return Ok(r);
    }

    if status_code == Some(400) && error_msg.contains("signature") && error_msg.contains("thinking") {
        return Ok(set(make_reason(py, "thinking_signature")));
    }

    if status_code == Some(429) && error_msg.contains("extra usage") && error_msg.contains("long context") {
        let mut r = set(make_reason(py, "long_context_tier"));
        r.should_compress = true;
        return Ok(r);
    }

    if status_code == Some(400) && error_msg.contains("long context beta") && error_msg.contains("not yet available") {
        return Ok(set(make_reason(py, "oauth_long_context_beta_forbidden")));
    }

    if status_code == Some(400)
        && (error_msg.contains("error parsing grammar") || error_msg.contains("json-schema-to-grammar")
            || (error_msg.contains("unable to generate parser") && error_msg.contains("template")))
    {
        return Ok(set(make_reason(py, "llama_cpp_grammar_pattern")));
    }

    if error_msg.contains("do not have an active grok subscription")
        || (error_msg.contains("out of available resources") && error_msg.contains("grok"))
    {
        let mut r = set(make_reason(py, "auth"));
        r.retryable = false;
        r.should_fallback = true;
        return Ok(r);
    }

    // ── 2. HTTP status code ───────────────────────────────────────

    if let Some(sc) = status_code {
        let result = match sc {
            401 => Some((make_reason(py, "auth"), false, false, true, true)),
            403 => {
                if error_msg.contains("key limit exceeded") || error_msg.contains("spending limit") || any_match(&error_msg, BILLING) {
                    Some((make_reason(py, "billing"), false, false, true, true))
                } else {
                    Some((make_reason(py, "auth"), false, false, false, true))
                }
            }
            402 => Some(if any_match(&error_msg, USAGE_LIMIT) && any_match(&error_msg, USAGE_TRANSIENT) {
                (make_reason(py, "rate_limit"), true, false, true, true)
            } else {
                (make_reason(py, "billing"), false, false, true, true)
            }),
            404 => {
                if any_match(&error_msg, BILLING) {
                    Some((make_reason(py, "billing"), false, false, true, true))
                } else if any_match(&error_msg, PROVIDER_POLICY) {
                    Some((make_reason(py, "provider_policy_blocked"), false, false, false, false))
                } else if any_match(&error_msg, MODEL_NOT_FOUND) {
                    Some((make_reason(py, "model_not_found"), false, false, false, true))
                } else {
                    Some((make_reason(py, "unknown"), true, false, false, false))
                }
            }
            413 => Some((make_reason(py, "payload_too_large"), true, true, false, false)),
            429 => Some((make_reason(py, "rate_limit"), true, false, true, true)),
            400 => {
                let c = classify_400(&error_msg, &error_code, &body_msg, py, approx_tokens, context_length, num_messages);
                let mut c = c;
                c.status_code = status_code;
                c.provider = prov;
                c.model = mdl;
                c.message = message_str;
                return Ok(c);
            }
            500 | 502 => {
                let code_lower = error_code.to_lowercase();
                if any_match(&error_msg, REQUEST_VALIDATION)
                    || code_lower == "invalid_request_error"
                    || code_lower == "unknown_parameter"
                    || code_lower == "unsupported_parameter"
                {
                    Some((make_reason(py, "format_error"), false, false, false, true))
                } else {
                    Some((make_reason(py, "server_error"), true, false, false, false))
                }
            }
            503 | 529 => Some((make_reason(py, "overloaded"), true, false, false, false)),
            c if (400..500).contains(&c) => Some((make_reason(py, "format_error"), false, false, false, true)),
            c if (500..600).contains(&c) => Some((make_reason(py, "server_error"), true, false, false, false)),
            _ => None,
        };

        if let Some((reason, retryable, compress, rotate, fallback)) = result {
            let r = build_result(py, reason, status_code, prov, mdl, message_str, retryable, compress, rotate, fallback);
            return Ok(r);
        }
    }

    // ── 3. Error code ──────────────────────────────────────────────

    if !error_code.is_empty() {
        if let Some(mut c) = classify_by_error_code(&error_code, py) {
            c.status_code = status_code;
            c.provider = prov.clone();
            c.model = mdl.clone();
            c.message = message_str.clone();
            return Ok(c);
        }
    }

    // ── 4. Message pattern ─────────────────────────────────────────

    // Build message-pattern classifier inline
    if any_match(&error_msg, PAYLOAD_TOO_LARGE) {
        let mut r = set(make_reason(py, "payload_too_large"));
        r.should_compress = true;
        return Ok(r);
    }
    if any_match(&error_msg, MULTIMODAL_TOOL) {
        return Ok(set(make_reason(py, "multimodal_tool_content_unsupported")));
    }
    if any_match(&error_msg, IMAGE_TOO_LARGE) {
        return Ok(set(make_reason(py, "image_too_large")));
    }
    if any_match(&error_msg, USAGE_LIMIT) {
        let reason = if any_match(&error_msg, USAGE_TRANSIENT) { make_reason(py, "rate_limit") } else { make_reason(py, "billing") };
        let retryable = any_match(&error_msg, USAGE_TRANSIENT);
        let r = build_result(py, reason, status_code, prov.clone(), mdl.clone(), message_str.clone(), retryable, false, true, true);
        return Ok(r);
    }
    if any_match(&error_msg, BILLING) {
        let mut r = set(make_reason(py, "billing"));
        r.retryable = false;
        r.should_rotate_credential = true;
        r.should_fallback = true;
        return Ok(r);
    }
    if any_match(&error_msg, RATE_LIMIT) {
        let mut r = set(make_reason(py, "rate_limit"));
        r.should_rotate_credential = true;
        r.should_fallback = true;
        return Ok(r);
    }
    if any_match(&error_msg, CONTEXT_OVERFLOW) {
        let mut r = set(make_reason(py, "context_overflow"));
        r.should_compress = true;
        return Ok(r);
    }
    if any_match(&error_msg, AUTH) {
        let mut r = set(make_reason(py, "auth"));
        r.retryable = false;
        r.should_rotate_credential = true;
        r.should_fallback = true;
        return Ok(r);
    }
    if any_match(&error_msg, PROVIDER_POLICY) {
        let mut r = set(make_reason(py, "provider_policy_blocked"));
        r.retryable = false;
        return Ok(r);
    }
    if any_match(&error_msg, MODEL_NOT_FOUND) {
        let mut r = set(make_reason(py, "model_not_found"));
        r.retryable = false;
        r.should_fallback = true;
        return Ok(r);
    }
    if any_match(&error_msg, TIMEOUT_MSG) {
        return Ok(set(make_reason(py, "timeout")));
    }

    // ── 5. SSL transient → timeout ─────────────────────────────────

    if any_match(&error_msg, SSL_TRANSIENT) {
        return Ok(set(make_reason(py, "timeout")));
    }

    // ── 6. Server disconnect + large session → context overflow ───

    let is_disconnect = any_match(&error_msg, SERVER_DISCONNECT);
    if is_disconnect && status_code.is_none() {
        let is_large = approx_tokens > context_length * 6 / 10
            || (context_length <= 256_000 && (approx_tokens > 120_000 || num_messages > 200));
        if is_large {
            let mut r = set(make_reason(py, "context_overflow"));
            r.should_compress = true;
            return Ok(r);
        }
        return Ok(set(make_reason(py, "timeout")));
    }

    // ── 7. Transport heuristics ────────────────────────────────────

    // Check type names
    if TRANSPORT_TYPES.contains(&error_type.as_str()) {
        return Ok(set(make_reason(py, "timeout")));
    }
    // Check isinstance for common transport base classes (including subclasses)
    let is_transport_base = error.is_instance_of::<pyo3::exceptions::PyTimeoutError>()
        || error.is_instance_of::<pyo3::exceptions::PyConnectionError>()
        || error.is_instance_of::<pyo3::exceptions::PyOSError>();
    if is_transport_base {
        return Ok(set(make_reason(py, "timeout")));
    }

    // ── 8. Unknown ─────────────────────────────────────────────────

    Ok(set(make_reason(py, "unknown")))
}

// ── Tests ─────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_pattern_arrays_not_empty() {
        assert!(!BILLING.is_empty());
        assert!(!RATE_LIMIT.is_empty());
        assert!(!CONTEXT_OVERFLOW.is_empty());
    }

    #[test]
    fn test_any_match() {
        assert!(any_match("rate limit exceeded", RATE_LIMIT));
        assert!(!any_match("normal response", RATE_LIMIT));
    }
}
