# Verifying Release Artifacts

All Intellect Agent releases are signed with GPG. Verify integrity in two steps.

## 1. Import the public key

```bash
curl -sL https://raw.githubusercontent.com/ontoweb-cn/intellect-agent/main/gpg-public.asc | gpg --import
```

Expected output:
```
gpg: key 7770F3E587EFAA74: public key "Intellect Agent CI <ci@intellect-agent.dev>" imported
```

## 2. Verify the release

```bash
# Download the release files
curl -sLO https://github.com/ontoweb-cn/intellect-agent/releases/download/v0.6.6/SHA256SUMS
curl -sLO https://github.com/ontoweb-cn/intellect-agent/releases/download/v0.6.6/SHA256SUMS.asc
curl -sLO https://github.com/ontoweb-cn/intellect-agent/releases/download/v0.6.6/intellect-agent-v0.6.6-darwin-universal2.tar.gz

# Verify GPG signature
gpg --verify SHA256SUMS.asc SHA256SUMS
# Output: Good signature from "Intellect Agent CI <ci@intellect-agent.dev>"

# Verify file integrity
sha256sum -c SHA256SUMS --ignore-missing
# Output: intellect-agent-v0.6.6-darwin-universal2.tar.gz: OK
```

## Key fingerprint

```
46CD 203F BA8D 60CF 18C4  5ED1 7770 F3E5 87EF AA74
```

## Trust on first use (TOFU)

Add the key to your trusted keyring:

```bash
echo "7770F3E587EFAA74:6:" | gpg --import-ownertrust
```

## Installing from China (domestic mirrors)

If `pypi.org` is slow from mainland China, use a domestic mirror:

```bash
# Tsinghua TUNA mirror (recommended)
pip install intellect-agent -i https://pypi.tuna.tsinghua.edu.cn/simple/

# Alibaba Cloud mirror
pip install intellect-agent -i https://mirrors.aliyun.com/pypi/simple/

# USTC mirror
pip install intellect-agent -i https://pypi.mirrors.ustc.edu.cn/simple/
```

Or set it permanently:

```bash
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple/
```

Mirrors sync from pypi.org daily. New releases may take up to 24 hours to appear.
