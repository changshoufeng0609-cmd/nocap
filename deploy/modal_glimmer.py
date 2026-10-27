"""Private Muse Glimmer server. Preparing this file does not deploy it.

After Modal authentication, secret setup and explicit deployment approval:
    modal deploy deploy/modal_glimmer.py

See docs/open-weights.md. Never load the repository's .env into the image.
The vLLM flags follow https://recipes.vllm.ai/meta-models/Muse-Glimmer-30B,
checked 2026-10-27. GPU startup and extraction remain unverified until deployment.
"""
import subprocess

import modal

MODEL_ID = "meta-models/Muse-Glimmer-30B"
SERVED_MODEL = "muse-glimmer"
PORT = 8000


def server_command() -> list[str]:
    """Text-only extraction with bounded concurrency; retain the checkpoint's sampling and stop tokens."""
    return [
        "vllm", "serve", MODEL_ID,
        "--served-model-name", SERVED_MODEL,
        "--host", "0.0.0.0", "--port", str(PORT),
        "--tensor-parallel-size", "1",
        "--max-model-len", "32768",
        "--max-num-seqs", "4",
        "--gpu-memory-utilization", "0.9",
        "--enforce-eager",
        "--enable-auto-tool-choice", "--tool-call-parser", "muse_glimmer",
        "--reasoning-parser", "muse_glimmer",
        "--generation-config", "auto",
        "--limit-mm-per-prompt", '{"image":0}',
        "--disable-log-requests",
    ]


image = (
    modal.Image.from_registry("vllm/vllm-openai:v0.28.0")
    .entrypoint([])
    .env({"HF_HOME": "/cache/huggingface", "VLLM_CACHE_ROOT": "/cache/vllm",
          "HF_HUB_DISABLE_TELEMETRY": "1"})
)
app = modal.App("receipts-glimmer")
weights = modal.Volume.from_name("receipts-glimmer-hf", create_if_missing=True)
runtime_cache = modal.Volume.from_name("receipts-glimmer-vllm", create_if_missing=True)


@app.server(
    image=image,
    gpu="H100",
    cpu=4,
    memory=16384,
    secrets=[modal.Secret.from_name("receipts-hf", required_keys=["HF_TOKEN"])],
    volumes={"/cache/huggingface": weights, "/cache/vllm": runtime_cache},
    port=PORT,
    unauthenticated=False,
    min_containers=0,
    max_containers=1,
    scaledown_window=60,
    startup_timeout=900,
)
class Glimmer:
    @modal.enter()
    def start(self):
        # Argument list, no shell, no credentials on the command line or in logs.
        self.process = subprocess.Popen(server_command())

    @modal.exit()
    def stop(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
