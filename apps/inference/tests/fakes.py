from __future__ import annotations

import re


class FakeTokenizer:
    def apply_chat_template(self, messages, add_generation_prompt=True, extra_context=None):
        return messages[-1]["content"]


class FakeBatch:
    def __init__(self, texts):
        self.texts = texts


class FakeLLM:
    """Echoes numbered lines as '[vi] text'. ``drop`` removes given line numbers once."""

    def __init__(self, drop: set[int] | None = None, drop_always: bool = False) -> None:
        self.prompts: list[str] = []
        self.drop = set(drop or ())
        self.drop_always = drop_always
        self.batched_calls = 0

    def get_tokenizer(self) -> FakeTokenizer:
        return FakeTokenizer()

    def generate(self, prompt, config=None, **_kwargs):
        if isinstance(prompt, list):
            self.batched_calls += 1
            return FakeBatch([self._one(p) for p in prompt])
        return self._one(prompt)

    def _one(self, prompt):
        self.prompts.append(prompt)
        body = prompt.split("Translate to", 1)[1]
        lines = re.findall(r"^(\d+)\. (.*)$", body, flags=re.M)
        out = ["<think>\n\n</think>"]
        for number, text in lines:
            if int(number) in self.drop and (self.drop_always or len(lines) > 1):
                continue
            out.append(f"{number}. [vi] {text}")
        return "\n".join(out)
