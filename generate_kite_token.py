#!/usr/bin/env python3
"""
generate_kite_token.py
----------------------
Headless Kite login using user ID, password, TOTP and API secret.
Reads credentials from ~/.kite_secrets
Updates ~/trading/Bollinger/.env.kite with the new KITE_ACCESS_TOKEN.

Usage:
    python3 generate_kite_token.py
"""

import os
import re
import sys
import time
import pyotp
import requests

# =========================
# PATHS
# =========================
SECRETS_FILE = os.path.expanduser("~/.kite_secrets")
ENV_FILE = os.path.expanduser("~/trading/Bollinger/.env.kite")


# =========================
# LOAD SECRETS
# =========================
def load_secrets(path: str) -> dict:
    secrets = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, _, val = line.partition("=")
            secrets[key.strip()] = val.strip()
    return secrets


# =========================
# KITE LOGIN
# =========================
def get_access_token(secrets: dict) -> str:
    user_id    = secrets["KITE_USER_ID"]
    password   = secrets["KITE_PASSWORD"]
    api_key    = secrets["KITE_API_KEY"]
    api_secret = secrets["KITE_API_SECRET"]
    totp_secret = secrets["KITE_TOTP_SECRET"]

    session = requests.Session()

    # Step 1 — Login with user ID + password
    print("🔐 Step 1: Logging in with user ID + password...")
    login_url = "https://kite.zerodha.com/api/login"
    r = session.post(login_url, data={
        "user_id": user_id,
        "password": password,
    })
    r.raise_for_status()
    data = r.json()
    if data.get("status") != "success":
        raise RuntimeError(f"Login failed: {data}")

    request_id = data["data"]["request_id"]
    print(f"✅ Login successful | request_id={request_id}")

    # Step 2 — Submit TOTP
    print("🔐 Step 2: Submitting TOTP...")
    totp = pyotp.TOTP(totp_secret)
    otp = totp.now()
    print(f"   OTP generated: {otp}")

    twofa_url = "https://kite.zerodha.com/api/twofa"
    r = session.post(twofa_url, data={
        "user_id":    user_id,
        "request_id": request_id,
        "twofa_value": otp,
        "twofa_type": "totp",
        "skip_session": "",
    })
    r.raise_for_status()
    data = r.json()
    if data.get("status") != "success":
        raise RuntimeError(f"2FA failed: {data}")
    print("✅ 2FA successful")

    # Step 3 — Get request token from redirect
    print("🔐 Step 3: Fetching request token...")
    login_page = f"https://kite.zerodha.com/connect/login?api_key={api_key}&v=3"

    # Do NOT follow redirects — the request_token is in the Location header
    r = session.get(login_page, allow_redirects=False)

    # Follow redirects manually, stopping when we see request_token
    match = None
    for _ in range(10):
        location = r.headers.get("Location", "")
        match = re.search(r"request_token=([^&]+)", location)
        if match:
            break
        if r.status_code in (301, 302, 303, 307, 308) and location:
            r = session.get(location, allow_redirects=False)
        else:
            break

    if not match:
        # Last fallback — check response body
        match = re.search(r"request_token=([^&\"]+)", r.text)

    if not match:
        raise RuntimeError(
            f"Could not find request_token in redirect.\n"
            f"Last URL: {r.headers.get('Location', 'none')}\n"
            f"Check your Kite app redirect URL is set to https://127.0.0.1 in the developer console."
        )

    request_token = match.group(1)
    print(f"✅ request_token={request_token}")

    # Step 4 — Generate access token
    print("🔐 Step 4: Generating access token...")
    import hashlib
    checksum = hashlib.sha256(f"{api_key}{request_token}{api_secret}".encode()).hexdigest()

    token_url = "https://api.kite.trade/session/token"
    r = session.post(token_url, data={
        "api_key":       api_key,
        "request_token": request_token,
        "checksum":      checksum,
    }, headers={"X-Kite-Version": "3"})
    r.raise_for_status()
    data = r.json()
    if data.get("status") != "success":
        raise RuntimeError(f"Token generation failed: {data}")

    access_token = data["data"]["access_token"]
    print(f"✅ Access token generated: {access_token[:6]}...{access_token[-4:]}")
    return access_token


# =========================
# UPDATE .env.kite
# =========================
def update_env_file(env_path: str, new_token: str, api_key: str):
    """
    Reads the existing .env.kite and updates only KITE_ACCESS_TOKEN.
    All other lines (PG_HOST, PG_USER, etc.) are preserved exactly.
    """
    if not os.path.exists(env_path):
        raise FileNotFoundError(f".env.kite not found at {env_path}")

    with open(env_path) as f:
        lines = f.readlines()

    updated_token = False
    updated_key = False
    new_lines = []

    for line in lines:
        if line.startswith("export KITE_ACCESS_TOKEN="):
            new_lines.append(f"export KITE_ACCESS_TOKEN={new_token}\n")
            updated_token = True
        elif line.startswith("export KITE_API_KEY="):
            new_lines.append(f"export KITE_API_KEY={api_key}\n")
            updated_key = True
        else:
            new_lines.append(line)

    # Append if not found
    if not updated_token:
        new_lines.append(f"export KITE_ACCESS_TOKEN={new_token}\n")
    if not updated_key:
        new_lines.append(f"export KITE_API_KEY={api_key}\n")

    # Atomic write
    tmp_path = env_path + ".tmp"
    with open(tmp_path, "w") as f:
        f.writelines(new_lines)
    os.replace(tmp_path, env_path)

    print(f"✅ {env_path} updated successfully")


# =========================
# MAIN
# =========================
def main():
    if not os.path.exists(SECRETS_FILE):
        print(f"❌ Secrets file not found: {SECRETS_FILE}")
        print("   Create it using the provided kite_secrets.template")
        sys.exit(1)

    print(f"📂 Loading secrets from {SECRETS_FILE}")
    secrets = load_secrets(SECRETS_FILE)

    required = ["KITE_USER_ID", "KITE_PASSWORD", "KITE_API_KEY", "KITE_API_SECRET", "KITE_TOTP_SECRET"]
    missing = [k for k in required if k not in secrets]
    if missing:
        print(f"❌ Missing keys in secrets file: {missing}")
        sys.exit(1)

    try:
        access_token = get_access_token(secrets)
        update_env_file(ENV_FILE, access_token, secrets["KITE_API_KEY"])
        print("\n🎉 Done! Token updated in .env.kite")
        print(f"   Source it with: source {ENV_FILE}")
    except Exception as e:
        print(f"\n❌ Failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
