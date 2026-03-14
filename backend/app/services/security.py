import os
from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

# In production, this key would be stored in a Secret Manager
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")

if not ENCRYPTION_KEY:
    # Generate a key for development if not present
    ENCRYPTION_KEY = Fernet.generate_key().decode()
    print(f"WARNING: ENCRYPTION_KEY not found. Using transient key: {ENCRYPTION_KEY}")

fernet = Fernet(ENCRYPTION_KEY.encode())

def encrypt_token(token: str) -> str:
    if not token:
        return ""
    return fernet.encrypt(token.encode()).decode()

def decrypt_token(token: str) -> str:
    if not token:
        return ""
    return fernet.decrypt(token.encode()).decode()
