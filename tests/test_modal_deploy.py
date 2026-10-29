"""Load deployment configuration with a fake Modal module: never authenticate or create cloud resources."""
import runpy
import sys
import pytest
from pathlib import Path
from types import SimpleNamespace


@pytest.mark.parametrize("use_hf", [False, True])
def test_modal_server_requires_proxy_auth_and_optional_narrow_hf_secret(monkeypatch, use_hf):
    if use_hf:
        monkeypatch.setenv("RECEIPTS_MODAL_USE_HF_SECRET", "1")
    else:
        monkeypatch.delenv("RECEIPTS_MODAL_USE_HF_SECRET", raising=False)
    captured = {}

    class Image:
        @classmethod
        def from_registry(cls, registry, **kwargs):
            captured["registry"] = registry
            captured["registry_options"] = kwargs
            return cls()

        def entrypoint(self, argv):
            captured["entrypoint"] = argv
            return self

        def env(self, env):
            captured["env"] = env
            return self

    class App:
        def __init__(self, name):
            captured["name"] = name

        def server(self, **kwargs):
            captured["server"] = kwargs
            return lambda cls: cls

    def secret(name, **kwargs):
        captured["secret"] = (name, kwargs)
        return name

    fake = SimpleNamespace(Image=Image, App=App,
                           Volume=SimpleNamespace(from_name=lambda name, **kw: name),
                           Secret=SimpleNamespace(from_name=secret),
                           enter=lambda: lambda fn: fn, exit=lambda: lambda fn: fn)
    monkeypatch.setitem(sys.modules, "modal", fake)
    monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("importing deployment code must not start a process")))
    module = runpy.run_path(str(Path(__file__).resolve().parents[1] / "deploy" / "modal_glimmer.py"))
    config = captured["server"]
    assert config["unauthenticated"] is False
    assert config["min_containers"] == 0 and config["max_containers"] == 1 and config["gpu"] == "H100"
    assert config["scaledown_window"] == 60
    assert config["cpu"] == (4, 4) and config["memory"] == (16384, 16384)
    assert config["exit_grace_period"] == 0 and config["max_concurrency"] == 4
    if use_hf:
        assert captured["secret"] == ("receipts-hf", {"required_keys": ["HF_TOKEN"]})
        assert config["secrets"] == ["receipts-hf"]
    else:
        assert "secret" not in captured and config["secrets"] == []
    assert captured["entrypoint"] == []  # do not run the registry image's vLLM entrypoint twice
    assert captured["registry"] == "vllm/vllm-openai:v0.28.0"
    assert captured["registry_options"] == {"add_python": "3.12"}
    command = module["server_command"]()
    assert command[:3] == ["vllm", "serve", "meta-models/Muse-Glimmer-30B"]
    assert command[command.index("--served-model-name") + 1] == "muse-glimmer"
    for flag in ("--reasoning-parser", "--tool-call-parser"):
        assert command[command.index(flag) + 1] == "muse_glimmer"
    assert command[command.index("--generation-config") + 1] == "auto"
    assert "--disable-log-requests" not in command  # removed by vLLM 0.28
    assert "--no-enable-log-requests" in command and "--no-enable-log-outputs" in command
    assert "--disable-uvicorn-access-log" in command and "--trust-remote-code" not in command
