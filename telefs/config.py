"""Configuration handling for TeleFS."""
import json
import os
import base64
from pathlib import Path
from typing import Any, Dict, Optional
from cryptography.fernet import Fernet

DEFAULT_CONFIG = {
    "api_id": None,
    "api_hash": None,
    "phone_number": None,
    "session_name": "telefs_session",
    "encryption": {
        "enabled": False,
        "key": None  # Base64 encoded Fernet key
    },
    "cwd": "/"
}

CONFIG_DIR = Path.home() / ".config" / "telefs"
CONFIG_FILE = CONFIG_DIR / "config.json"


def get_config_dir() -> Path:
    """Ensure config directory exists with safe permissions."""
    if not CONFIG_DIR.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        # Set directory permissions to 700 (drwx------)
        os.chmod(CONFIG_DIR, 0o700)
    return CONFIG_DIR


def load_config() -> Dict[str, Any]:
    """Load configuration from file or create default."""
    if CONFIG_FILE.exists():
        try:
            # Check permissions
            mode = os.stat(CONFIG_FILE).st_mode
            if mode & 0o077: # If group or others have any permissions
                os.chmod(CONFIG_FILE, 0o600)
                
            with open(CONFIG_FILE, "r") as f:
                config = json.load(f)
            # Merge with defaults for missing keys
            for key, value in DEFAULT_CONFIG.items():
                if key not in config:
                    config[key] = value
                elif isinstance(value, dict) and isinstance(config[key], dict):
                    for subkey, subvalue in value.items():
                        if subkey not in config[key]:
                            config[key][subkey] = subvalue
            return config
        except Exception:
            return DEFAULT_CONFIG.copy()
    else:
        return DEFAULT_CONFIG.copy()


def save_config(config: Dict[str, Any]) -> None:
    """Save configuration to file with safe permissions."""
    get_config_dir()
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)
    # Set file permissions to 600 (-rw-------)
    os.chmod(CONFIG_FILE, 0o600)


def get_api_credentials() -> tuple:
    """Return (api_id, api_hash) from config."""
    config = load_config()
    api_id = config.get("api_id")
    api_hash = config.get("api_hash")
    return api_id, api_hash


def is_configured() -> bool:
    """Check if API credentials are set."""
    api_id, api_hash = get_api_credentials()
    return bool(api_id and api_hash)


def get_phone_number() -> Optional[str]:
    """Return phone_number from config."""
    config = load_config()
    return config.get("phone_number")


def get_encryption_key() -> Optional[bytes]:
    """Return encryption key if enabled, using keyring as primary storage."""
    config = load_config()
    enc_cfg = config.get("encryption", {})
    if not enc_cfg.get("enabled"):
        return None

    # Try to get from keyring first
    try:
        import keyring
        key_str = keyring.get_password("telefs", "encryption_key")
        if key_str:
            return key_str.encode('utf-8')
    except Exception:
        pass # Handle cases where keyring is not available

    # Fallback to config file
    if enc_cfg.get("key"):
        key_bytes = enc_cfg["key"].encode('utf-8')
        # Try to migrate to keyring if possible
        try:
            import keyring
            keyring.set_password("telefs", "encryption_key", enc_cfg["key"])
        except Exception:
            pass
        return key_bytes
    
    # Generate new key if enabled but missing
    key = Fernet.generate_key()
    key_str = key.decode('utf-8')
    
    # Save to keyring
    try:
        import keyring
        keyring.set_password("telefs", "encryption_key", key_str)
    except Exception:
        # If keyring fails, we MUST save to config file
        enc_cfg["key"] = key_str
        config["encryption"] = enc_cfg
        save_config(config)
        print(f"Warning: Keyring unavailable. Key saved to config file: {CONFIG_FILE}")
        
    return key

def get_cwd() -> str:
    """Get the persistent current working directory."""
    config = load_config()
    return config.get("cwd", "/")

def save_cwd(path: str):
    """Save the current working directory to config."""
    config = load_config()
    config["cwd"] = path
    save_config(config)
