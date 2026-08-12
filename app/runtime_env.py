"""
Process environment setup for FAISS, PyTorch, and Hugging Face tokenizers.

Must be imported before any faiss/torch/transformers import. On macOS, both FAISS
and PyTorch link OpenMP; loading CLIP after FAISS indices can deadlock or segfault
without these settings (often looks like a hang at startup).
"""

from __future__ import annotations

import os
import sys

_configured = False


def configure_runtime_env() -> None:
    """Apply platform-specific defaults once per process."""
    global _configured
    if _configured:
        return

    # Avoid tokenizer fork warnings and occasional deadlocks on all platforms.
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

    if sys.platform == "darwin":
        # macOS (Intel + Apple Silicon): duplicate OpenMP runtimes from FAISS + PyTorch.
        os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
        os.environ.setdefault("OMP_NUM_THREADS", "1")
        os.environ.setdefault("MKL_NUM_THREADS", "1")
        os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

    _configured = True


# Run as soon as this module is imported.
configure_runtime_env()
