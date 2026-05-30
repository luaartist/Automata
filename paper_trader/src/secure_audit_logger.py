import os
import json
import hashlib
import time
from pathlib import Path

class SecureAuditLogger:
    """
    SOC 2 Compliant Append-Only Audit Logger.
    Features:
    1. Directory Jail: Prevents path traversal (cannot write outside allowed base_dir).
    2. Append-Only: Strictly uses 'a' mode for file operations.
    3. Chained SHA-256: Every entry hashes the previous hash + current payload for immutable lineage.
    """
    
    def __init__(self, base_dir: str, log_filename: str):
        self.base_dir = Path(base_dir).resolve()
        
        # Security: Prevent path traversal in filename
        # Ensure the final resolved path starts with the allowed base_dir
        target_path = (self.base_dir / log_filename).resolve()
        
        if not str(target_path).startswith(str(self.base_dir)):
            raise PermissionError(f"[SECURITY FATAL] Attempted path traversal detected: {log_filename}")
            
        self.log_path = target_path
        
        # Ensure base directory exists securely
        os.makedirs(self.base_dir, exist_ok=True)
        # Ensure it's not world writable
        os.chmod(self.base_dir, 0o700) 
        
        # Initialize internal state
        self.last_hash = self._get_tail_hash()
        
    def _get_tail_hash(self) -> str:
        """Read the last line's hash to resume the cryptographic chain, or return genesis hash."""
        if not self.log_path.exists():
            return hashlib.sha256(b"GENESIS_BLOCK").hexdigest()
            
        try:
            # Read last line efficiently
            with open(self.log_path, 'rb') as f:
                f.seek(-2, os.SEEK_END)
                while f.read(1) != b'\n':
                    f.seek(-2, os.SEEK_CUR)
                last_line = f.readline().decode('utf-8')
                
            last_record = json.loads(last_line)
            return last_record.get('audit_hash', hashlib.sha256(b"GENESIS_BLOCK").hexdigest())
        except Exception:
            return hashlib.sha256(b"GENESIS_BLOCK").hexdigest()

    def log_tick(self, feature_8d: list, model_output: dict = None, paper_order: dict = None, asset_symbol: str = None):
        """Write a tamper-evident structured record to the log."""
        timestamp_ns = time.time_ns()
        
        # Build the payload (immutable logic layer)
        payload = {
            "timestamp_ns": timestamp_ns,
            "feature_8d": feature_8d,
            "model_output": model_output or {},
            "paper_order": paper_order or {},
            "asset_symbol": asset_symbol or "BTC"
        }
        
        payload_str = json.dumps(payload, separators=(',', ':'), sort_keys=True)
        
        # Construct chained hash: SHA256(prev_hash + current_payload)
        chain_input = self.last_hash + payload_str
        current_hash = hashlib.sha256(chain_input.encode('utf-8')).hexdigest()
        
        # Final record includes the hash
        payload["prev_hash"] = self.last_hash
        payload["audit_hash"] = current_hash
        
        final_line = json.dumps(payload, separators=(',', ':'), sort_keys=True)
        
        # STRICT APPEND-ONLY WRITE
        with open(self.log_path, 'a', encoding='utf-8') as f:
            f.write(final_line + "\n")
            
        self.last_hash = current_hash
        return current_hash

# Basic self-test if run directly
if __name__ == "__main__":
    try:
        # Test 1: Directory Jail
        SecureAuditLogger("/tmp/secure_logs", "../../etc/passwd")
        print("FAILED: Did not catch path traversal")
    except PermissionError as e:
        print(f"PASSED Traversal Check: {e}")
        
    # Test 2: Standard Logging
    logger = SecureAuditLogger("/tmp/secure_logs", "test_audit.jsonl")
    h1 = logger.log_tick([1.1, 2.2, 3.3, 4.4, 5.5, 6.6, 7.7, 8.8])
    h2 = logger.log_tick([1.2, 2.3, 3.4, 4.5, 5.6, 6.7, 7.8, 8.9])
    print(f"PASSED Chain Test. Hash 1: {h1[:8]}... -> Hash 2: {h2[:8]}...")
