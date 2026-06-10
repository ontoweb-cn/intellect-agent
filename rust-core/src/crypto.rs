//! Cryptographic utilities — PKCE, secure random, Fernet, JWT.
//!
//! Stage 5a/5e: PKCE code challenge (SHA-256 + base64url, RFC 7636 §4.2)
//! and secure random token generation via OS CSPRNG.

use pyo3::prelude::*;
use rand::RngCore;
use sha2::{Digest, Sha256};
use base64::Engine;

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

// ── Fernet (Stage 5b) ──────────────────────────────────────────────────────

use aes::cipher::{block_padding::Pkcs7, BlockDecryptMut, BlockEncryptMut, KeyIvInit};
use hmac::{Hmac, Mac};
use sha2::Sha256 as Sha2_256;

type Aes128CbcEnc = cbc::Encryptor<aes::Aes128>;
type Aes128CbcDec = cbc::Decryptor<aes::Aes128>;
type HmacSha256 = Hmac<Sha2_256>;

/// Encrypt plaintext using Fernet (AES-128-CBC + HMAC-SHA256).
/// `key_b64` is a 32-byte base64url-encoded key.
/// Returns the Fernet token as a base64url string.
#[pyfunction]
pub fn fernet_encrypt(key_b64: &str, plaintext: &str) -> PyResult<String> {
    let key_bytes = base64_url_decode(key_b64)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("invalid key: {}", e)))?;
    if key_bytes.len() != 32 {
        return Err(pyo3::exceptions::PyValueError::new_err("key must be 32 bytes"));
    }
    let signing_key = &key_bytes[..16];
    let encryption_key = &key_bytes[16..];

    // Version (0x80) + big-endian u64 timestamp
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let mut prefix = Vec::with_capacity(1 + 8 + 16);
    prefix.push(0x80);
    prefix.extend_from_slice(&now.to_be_bytes());

    // Random IV
    let mut iv = [0u8; 16];
    rand::rngs::OsRng.fill_bytes(&mut iv);
    prefix.extend_from_slice(&iv);

    // AES-128-CBC encrypt
    let plaintext_bytes = plaintext.as_bytes();
    let buf_len = plaintext_bytes.len() + 16; // room for padding
    let mut ciphertext = vec![0u8; buf_len];
    ciphertext[..plaintext_bytes.len()].copy_from_slice(plaintext_bytes);
    let ct_len = Aes128CbcEnc::new(encryption_key.into(), &iv.into())
        .encrypt_padded_mut::<Pkcs7>(&mut ciphertext, plaintext_bytes.len())
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("encrypt failed: {}", e)))?
        .len();
    ciphertext.truncate(ct_len);

    // HMAC-SHA256: sign(version || timestamp || IV || ciphertext)
    let mut mac = HmacSha256::new_from_slice(signing_key)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("hmac init: {}", e)))?;
    mac.update(&prefix);
    mac.update(&ciphertext);
    let hmac_bytes = mac.finalize().into_bytes();

    // Assemble token: version || timestamp || IV || ciphertext || HMAC
    let mut token = prefix;
    token.extend_from_slice(&ciphertext);
    token.extend_from_slice(&hmac_bytes);

    Ok(base64_url(&token))
}

/// Decrypt a Fernet token. Returns the plaintext string.
#[pyfunction]
pub fn fernet_decrypt(key_b64: &str, token: &str) -> PyResult<String> {
    let key_bytes = base64_url_decode(key_b64)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("invalid key: {}", e)))?;
    if key_bytes.len() != 32 {
        return Err(pyo3::exceptions::PyValueError::new_err("key must be 32 bytes"));
    }
    let signing_key = &key_bytes[..16];
    let encryption_key = &key_bytes[16..];

    // Decode token
    let data = base64_url_decode(token)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("invalid token: {}", e)))?;

    if data.len() < 57 {
        // 1 (version) + 8 (timestamp) + 16 (IV) + 32 (HMAC) = 57 minimum
        return Err(pyo3::exceptions::PyValueError::new_err("token too short"));
    }

    let version = data[0];
    let hmac_received = &data[data.len() - 32..];
    let payload = &data[..data.len() - 32];

    // Verify HMAC
    let mut mac = HmacSha256::new_from_slice(signing_key)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("hmac init: {}", e)))?;
    mac.update(payload);
    mac.verify_slice(hmac_received)
        .map_err(|_| pyo3::exceptions::PyValueError::new_err("invalid token: HMAC mismatch"))?;

    // Extract IV and ciphertext
    let iv = &data[9..25]; // skip version(1) + timestamp(8)
    let ciphertext = &data[25..data.len() - 32];

    // AES-128-CBC decrypt
    let mut buf = ciphertext.to_vec();
    let pt = Aes128CbcDec::new(encryption_key.into(), iv.into())
        .decrypt_padded_mut::<Pkcs7>(&mut buf)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("decrypt failed: {}", e)))?;

    String::from_utf8(pt.to_vec())
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("invalid utf-8: {}", e)))
}

/// Generate a new Fernet key (32 random bytes, base64url-encoded with padding).
/// Compatible with Python's cryptography.fernet.Fernet.generate_key().
#[pyfunction]
pub fn generate_fernet_key() -> String {
    let mut buf = [0u8; 32];
    rand::rngs::OsRng.fill_bytes(&mut buf);
    base64::engine::general_purpose::URL_SAFE.encode(&buf)
}

// ── Helpers ─────────────────────────────────────────────────────────────────

/// Base64 URL-safe encoding without padding (RFC 4648 §5).
fn base64_url(bytes: &[u8]) -> String {
    base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(bytes)
}

/// Decode a base64url string that may or may not have padding.
fn base64_url_decode(s: &str) -> Result<Vec<u8>, base64::DecodeError> {
    // Add padding if needed (base64 requires length % 4 == 0)
    let padded = match s.len() % 4 {
        2 => format!("{}==", s),
        3 => format!("{}=", s),
        _ => s.to_string(),
    };
    base64::engine::general_purpose::URL_SAFE.decode(&padded)
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
