import os
import shutil
from typing import Callable, List


class TransactionError(Exception):
    """Raised when a transaction fails verification or commit."""
    pass


class Transaction:
    def __init__(self, *, tmp_suffix=".dl.tmp", prev_suffix=".prev"):
        self.tmp_suffix = tmp_suffix
        self.prev_suffix = prev_suffix
        self._staged = {}  # path -> tmp_path
        self._committed = []  # list of paths that have been committed
        self._rolled_back = False

    def stage(self, path: str, content: str) -> None:
        """Stage a file for commit. Writes content to path + tmp_suffix."""
        # Ensure parent directory exists
        parent_dir = os.path.dirname(path)
        if parent_dir and not os.path.exists(parent_dir):
            raise TransactionError(f"Parent directory does not exist: {parent_dir}")
        
        tmp_path = path + self.tmp_suffix
        with open(tmp_path, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        self._staged[path] = tmp_path

    def verify(self, path: str, validator: Callable[[str], bool | str]) -> None:
        """Verify the staged content of a file using the provided validator."""
        if path not in self._staged:
            raise TransactionError(f"Cannot verify unstaged path: {path}")
        
        tmp_path = self._staged[path]
        with open(tmp_path, "r", encoding="utf-8", newline="") as f:
            content = f.read()
        
        try:
            result = validator(content)
        except Exception as e:
            raise TransactionError(f"Validator for {path} raised exception: {e}")
        
        if result is False or (isinstance(result, str) and result):
            reason = result if isinstance(result, str) else "validation failed"
            raise TransactionError(f"Verification failed for {path}: {reason}")

    def commit(self) -> List[str]:
        """Commit all staged files. Returns list of committed paths in sorted order."""
        if self._rolled_back:
            raise TransactionError("Cannot commit after rollback")
        
        # Sort paths for deterministic ordering
        sorted_paths = sorted(self._staged.keys())
        
        try:
            # Snapshot live files to .prev and replace with staged content
            for path in sorted_paths:
                tmp_path = self._staged[path]
                
                # Snapshot the live file if it exists
                if os.path.exists(path):
                    prev_path = path + self.prev_suffix
                    shutil.copy2(path, prev_path)
                
                # Atomic replace: move tmp to live
                os.replace(tmp_path, path)
                self._committed.append(path)
            
            # Clear staged files after successful commit
            self._staged.clear()
            return sorted_paths
            
        except Exception as e:
            # Roll back any already committed files on failure
            self.rollback()
            raise TransactionError(f"Commit failed: {e}")

    def rollback(self) -> List[str]:
        """Rollback all committed files from .prev and remove staged tmp files."""
        if self._rolled_back:
            return []  # Already rolled back
        
        restored_paths = []
        
        # Restore committed files from .prev
        for path in self._committed:
            prev_path = path + self.prev_suffix
            if os.path.exists(prev_path):
                # Restore from .prev
                shutil.copy2(prev_path, path)
                restored_paths.append(path)
            else:
                # No .prev means the file didn't exist before; remove it
                if os.path.exists(path):
                    os.remove(path)
                restored_paths.append(path)
        
        # Remove all tmp files
        for tmp_path in self._staged.values():
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        
        # Clear state
        self._staged.clear()
        self._committed.clear()
        self._rolled_back = True
        
        return restored_paths

    def __enter__(self) -> "Transaction":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        # Rollback if an exception is propagating OR if commit was never called
        if exc_type is not None or not self._committed:
            self.rollback()
        return False  # Never swallow exceptions
