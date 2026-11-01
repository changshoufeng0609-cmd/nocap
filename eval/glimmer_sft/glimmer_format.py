"""Muse Glimmer chat format for text-only system/user/assistant chats, with no ML framework.

A port of the fw-ai/cookbook renderer (training/renderer/muse_glimmer.py at 57968a2) restricted to the messages this
experiment uses: no tools, images or reasoning_content. The cookbook renderer imports torch and loads the tokenizer
through transformers; this one needs only the `tokenizers` package and the model's tokenizer.json. It encodes the
same pieces in the same order as the cookbook (header and body separately), and selfcheck.py verifies it against
the official chat_template.jinja and the workshop's rendered example.

    <|begin_of_text|><|start|>system<|message|>{system}\\n\\nReasoning strength: low.\\n\\n# Valid recipients:
    "self", "user".<|eot|><|start|>user<|message|>{user}<|eot|><|start|>assistant to=user<|message|>{answer}<|eot|>

Training weights cover the last assistant message's body (" to=user<|message|>" + answer + "<|eot|>"), not its
"<|start|>assistant" header: the cookbook's TrainOnWhat.LAST_ASSISTANT_TURN.
"""
from __future__ import annotations

from typing import Any, Optional, Sequence

BOS, START, MESSAGE, EOT, EOM = "<|begin_of_text|>", "<|start|>", "<|message|>", "<|eot|>", "<|eom|>"
RECIPIENTS = '# Valid recipients: "self", "user".'
_EFFORT_CASINGS = (("Reasoning effort", "Reasoning strength"), ("Reasoning Effort", "Reasoning Strength"),
                   ("reasoning effort", "reasoning strength"), ("REASONING EFFORT", "REASONING STRENGTH"))


def system_body(content: str, reasoning_strength: str) -> str:
    for old, new in _EFFORT_CASINGS:
        content = content.replace(old, new)
    suffix = "" if "reasoning strength" in content.lower() else "\n\nReasoning strength: " + reasoning_strength + "."
    return content + suffix + "\n\n" + RECIPIENTS + EOT


def pieces(messages: Sequence[dict[str, Any]], reasoning_strength: str,
           add_generation_prompt: bool) -> list[tuple[str, bool]]:
    """(text, trained) pieces in cookbook encoding order. Only the last assistant body is trained."""
    if not messages or messages[0].get("role") != "system":
        raise ValueError("this experiment always sends a system message first")
    last_assistant = max((i for i, m in enumerate(messages) if m["role"] == "assistant"), default=-1)
    out: list[tuple[str, bool]] = [(BOS, False)]
    for i, m in enumerate(messages):
        role, content = m["role"], m.get("content")
        if not isinstance(content, str):
            raise ValueError("text content only")
        if role == "system":
            out += [(START + "system" + MESSAGE, False), (system_body(content, reasoning_strength), False)]
        elif role == "user":
            out += [(START + "user" + MESSAGE, False), (content + EOT, False)]
        elif role == "assistant":
            if m.get("tool_calls") or m.get("reasoning_content") or m.get("recipient") not in (None, "user"):
                raise ValueError("plain user-facing assistant replies only")
            out += [(START + "assistant", False), (" to=user" + MESSAGE + content + EOT, i == last_assistant)]
        else:
            raise ValueError(f"unsupported role {role!r}")
    if add_generation_prompt:
        out.append((START + "assistant", False))
    return out


def render_text(messages: Sequence[dict[str, Any]], reasoning_strength: str = "low",
                add_generation_prompt: bool = False) -> str:
    return "".join(text for text, _ in pieces(messages, reasoning_strength, add_generation_prompt))


class GlimmerFormat:
    """Token-level rendering and response parsing with a `tokenizers.Tokenizer`."""

    def __init__(self, tokenizer_json: str, reasoning_strength: str = "low"):
        from tokenizers import Tokenizer

        self.tok = Tokenizer.from_file(tokenizer_json)
        self.reasoning_strength = reasoning_strength
        for special in (BOS, START, MESSAGE, EOT, EOM):
            if len(self.encode(special)) != 1:
                raise RuntimeError(f"{special!r} is not a single token in this tokenizer")
        self.eot_id = self.encode(EOT)[0]

    def encode(self, text: str) -> list[int]:
        return list(self.tok.encode(text, add_special_tokens=False).ids)

    def decode(self, ids: Sequence[int]) -> str:
        return self.tok.decode(list(ids), skip_special_tokens=False)

    def supervised(self, messages: Sequence[dict[str, Any]]) -> tuple[list[int], list[float]]:
        tokens: list[int] = []
        weights: list[float] = []
        for text, trained in pieces(messages, self.reasoning_strength, False):
            ids = self.encode(text)
            tokens += ids
            weights += [1.0 if trained else 0.0] * len(ids)
        return tokens, weights

    def prompt(self, messages: Sequence[dict[str, Any]]) -> list[int]:
        """Generation prompt for the conversation without its final assistant reply."""
        if messages and messages[-1]["role"] == "assistant":
            messages = messages[:-1]
        return [t for text, _ in pieces(messages, self.reasoning_strength, True) for t in self.encode(text)]

    def parse(self, completion_ids: Sequence[int], finish_reason: Optional[str] = None) -> dict[str, Any]:
        """The cookbook's parse_response: reasoning (to=self) and visible (to=user) text of one completion.

        terminated: the model ended its turn (<|eot|> sampled, or the server stopped on it); a completion cut off
        by max_tokens is not terminated."""
        text = self.decode(completion_ids)
        stop_at = text.find(EOT)
        terminated = stop_at >= 0 or finish_reason == "stop"
        if stop_at >= 0:
            text = text[:stop_at]
        text = text.removeprefix(START + "assistant")
        reasoning, visible, other = "", "", []
        for segment in text.split(EOM + START + "assistant"):
            if MESSAGE not in segment:
                continue
            recipient, payload = segment.split(MESSAGE, 1)
            recipient = recipient.removeprefix(" to=")
            if recipient == "self":
                reasoning += payload
            elif recipient == "user":
                visible += payload
            else:
                other.append(recipient)
        return {"reasoning": reasoning, "visible": visible, "terminated": terminated,
                "other_recipients": other, "raw_head": text[:200]}
