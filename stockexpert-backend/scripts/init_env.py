"""Generate new installation credentials without printing secrets."""

import argparse
import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def generate_values():
    return {
        "SECRET_KEY": secrets.token_hex(32),
        "POSTGRES_PASSWORD": secrets.token_hex(20),
        "MINIO_ACCESS_KEY": secrets.token_hex(10),
        "MINIO_SECRET_KEY": secrets.token_hex(20),
        "ADMIN_USERNAME": "markov_f",
        "ADMIN_PASSWORD": secrets.token_urlsafe(24),
        "LLM_DEFAULT_PROFILE": "qwen3-8b",
        "LLM_PROVIDER": "ollama",
        "LLM_MODEL": "qwen3:8b-q4_K_M",
        "DEEPSEEK_API_KEY": "",
        "DEEPSEEK_MODEL": "",
        "DEEPSEEK_THINKING": "true",
        "DEEPSEEK_REASONING_EFFORT": "high",
        "LLM_CONCURRENCY": "4",
        "NORMALIZATION_MAX_ATTEMPTS": "3",
        "TAXONOMY_MAX_ATTEMPTS": "3",
        "OLLAMA_LLM_CONCURRENCY": "1",
        "OLLAMA_LLM_NUM_CTX": "16384",
        "OLLAMA_LLM_MAX_TOKENS": "4096",
        "OLLAMA_LLM_TIMEOUT": "600",
        "EMBEDDING_MODEL": "bge-m3",
        "RERANKER_ENABLED": "true",
    }


def write_env(path, values):
    # O_EXCL protects existing credentials; permissions apply before the first byte.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        output.write("".join(f"{key}={value}\n" for key, value in values.items()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".env")
    args = parser.parse_args()
    try:
        write_env(args.output, generate_values())
    except FileExistsError:
        raise SystemExit(
            ".env already exists; not overwritten. Use reset_stack.py for a full reset."
        )
    print(
        "Created .env (0600). Read ADMIN_USERNAME / ADMIN_PASSWORD locally for login. "
        "Default model: local Qwen3-8B. No passwords printed."
    )


if __name__ == "__main__":
    main()
