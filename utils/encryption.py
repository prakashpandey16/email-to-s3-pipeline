import os
import base64
from cryptography.fernet import Fernet
import logging
from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

class SecretManager:
    """
    Handles encryption and decryption of sensitive configuration values
    using Fernet (symmetric encryption).
    
    Priority:
    1. KEYFILEPATH (File content)
    2. SECRET_KEY (Env Var)
    """
    
    def __init__(self):
        self.key = None
        
        key_file_path = os.environ.get("KEYFILEPATH")
        if key_file_path:
            try:
                if os.path.exists(key_file_path):
                    with open(key_file_path, "r") as kf:
                        self.key = kf.read().strip()
                else:
                    logger.warning(f"Encryption key file not found: {key_file_path}")
            except Exception as e:
                logger.error(f"Error reading encryption key file: {e}")

        if not self.key:
            self.key = os.environ.get("SECRET_KEY")

        # Fallback: Check for 'secrect.key' in the project root
        if not self.key:
            local_key_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "secrect.key")
            if os.path.exists(local_key_path):
                try:
                    with open(local_key_path, "r") as kf:
                        self.key = kf.read().strip()
                        logger.info(f"Using encryption key from local file: {local_key_path}")
                except Exception as e:
                    logger.error(f"Error reading local key file: {e}")


        
        self.cipher = None
        
        if self.key:
            try:
                # Ensure key is bytes
                if isinstance(self.key, str):
                    self.key = self.key.encode()
                self.cipher = Fernet(self.key)
            except Exception as e:
                logger.error(f"Invalid Encryption Key: {e}")
                self.cipher = None
        else:
            logger.warning("SECRET_KEY not set. Encryption/Decryption disabled.")

    @staticmethod
    def generate_key() -> str:
        """Generates a new valid Fernet key."""
        return Fernet.generate_key().decode()

    def encrypt(self, raw_value: str) -> str:
        """
        Encrypts a raw string value.
        Returns the encrypted token as a string.
        """
        if not raw_value:
            return raw_value
            
        if not self.cipher:
            raise ValueError("Cannot encrypt: Encryption key not configured.")
            
        return self.cipher.encrypt(raw_value.encode()).decode()

    def decrypt(self, encrypted_value: str) -> str:
        """
        Decrypts an encrypted token.
        Returns the original raw string.
        
        If decryption fails (e.g. value is not encrypted or key matching fails),
        returns the original value to support safe migration/backward compatibility
        (unless strictly enforced).
        """
        if not encrypted_value or not self.cipher:
            return encrypted_value
            
        try:
            return self.cipher.decrypt(encrypted_value.encode()).decode()
        except Exception:
            # Fallback: Assume value might be unencrypted raw text
            # In a strict mode, we might want to log a warning
            # logger.debug(f"Decryption failed for value, returning raw.")
            return encrypted_value
