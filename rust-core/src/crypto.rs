//! Cryptographic utilities — PKCE, secure random, Fernet, JWT.
//!
//! Stage 5a/5e: PKCE code challenge (SHA-256 + base64url, RFC 7636 §4.2)
//! and secure random token generation via OS CSPRNG.

use pyo3::prelude::*;
use rand::RngCore;
use sha2::{Digest, Sha256};

// ── Secure random (Stage 5e) ────────────────────────────────────────────────

/// Generate `nbytes` of cryptographically secure random bytes.
/// Uses the OS CSPRNG (getrandom → /dev/urandom or equivalent).
#[pyfunction]
pub fn secure_random_bytes(nbytes: usize) -> Vec<u8> {
    let mut buf = vec![0u8; nbytes];
    rand::rngs::OsRng.fill_bytes(&mut buf);
    buf
}

/// Generate a URL-safe base64 random token with `nbytes` of entropy.
#[pyfunction]
pub fn secure_token_urlsafe(nbytes: usize) -> String {
    let mut buf = vec![0u8; nbytes];
    rand::rngs::OsRng.fill_bytes(&mut buf);
    base64_url(&buf)
}

/// Generate a hex-encoded random token with `nbytes` of entropy.
#[pyfunction]
pub fn secure_token_hex(nbytes: usize) -> String {
    let mut buf = vec![0u8; nbytes];
    rand::rngs::OsRng.fill_bytes(&mut buf);
    hex::encode(&buf)
}

// ── PKCE (Stage 5a) ─────────────────────────────────────────────────────────

/// Generate a PKCE code verifier and S256 challenge per RFC 7636.
/// Returns (code_verifier, code_challenge).
/// verifier: 32 random bytes, base64url-encoded (43 chars).
/// challenge: SHA-256 digest of verifier, base64url-encoded, no padding.
#[pyfunction]
pub fn pkce_challenge() -> (String, String) {
    let mut verifier_bytes = [0u8; 32];
    rand::rngs::OsRng.fill_bytes(&mut verifier_bytes);
    let verifier = base64_url(&verifier_bytes);

    let mut hasher = Sha256::new();
    hasher.update(verifier.as_bytes());
    let digest = hasher.finalize();
    let challenge = base64_url(&digest);

    (verifier, challenge)
}

/// Compute the S256 code challenge from an existing verifier string.
/// Equivalent to: base64url(sha256(verifier)).
#[pyfunction]
pub fn pkce_challenge_from_verifier(verifier: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(verifier.as_bytes());
    let digest = hasher.finalize();
    base64_url(&digest)
}

// ── Helpers ─────────────────────────────────────────────────────────────────

/// Base64 URL-safe encoding without padding (RFC 4648 §5).
fn base64_url(bytes: &[u8]) -> String {
    use base64::Engine;
    base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(bytes)
}

// ── Rust unit tests ─────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_secure_random_bytes_length() {
        let b = secure_random_bytes(32);
        assert_eq!(b.len(), 32);
    }

    #[test]
    fn test_secure_random_bytes_unique() {
        let a = secure_random_bytes(16);
        let b = secure_random_bytes(16);
        assert_ne!(a, b); // astronomically unlikely to collide
    }

    #[test]
    fn test_token_urlsafe_length() {
        // 32 bytes → 43 base64url chars (no padding)
        let t = secure_token_urlsafe(32);
        assert_eq!(t.len(), 43);
        assert!(!t.contains('='));
        assert!(!t.contains('+'));
        assert!(!t.contains('/'));
    }

    #[test]
    fn test_pkce_challenge_format() {
        let (verifier, challenge) = pkce_challenge();
        // Verifier: 43 chars (32 bytes → base64url no pad)
        assert_eq!(verifier.len(), 43);
        // Challenge: 43 chars (SHA-256 → 32 bytes → base64url no pad)
        assert_eq!(challenge.len(), 43);
        // No padding, no non-URL-safe chars
        assert!(!verifier.contains('='));
        assert!(!challenge.contains('='));
    }

    #[test]
    fn test_pkce_challenge_deterministic() {
        let verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk";
        let challenge = pkce_challenge_from_verifier(verifier);
        // Known test vector from RFC 7636 Appendix B
        assert_eq!(challenge, "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM");
    }

    #[test]
    fn test_secure_token_hex_length() {
        let t = secure_token_hex(16);
        assert_eq!(t.len(), 32); // 16 bytes → 32 hex chars
    }
}
