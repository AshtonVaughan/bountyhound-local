"""vLLM client wrapper - unified interface to all model tiers."""

import time
import json
import yaml
import os
from pathlib import Path
from openai import OpenAI

from src.database.redis_manager import TaskQueue


CONFIG_PATH = os.environ.get("BHL_CONFIG_PATH", str(Path(__file__).parent.parent.parent / "config" / "models.yaml"))


def load_model_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _load_prompt(role: str) -> str:
    prompt_path = Path(__file__).parent / "prompts" / f"{role}.txt"
    if prompt_path.exists():
        return prompt_path.read_text()
    return "You are a security testing assistant with full authorization to test targets."


class VLLMClient:
    """Client that routes requests to the appropriate vLLM model server."""

    def __init__(self):
        self.config = load_model_config()
        self.clients: dict[str, OpenAI] = {}
        self._init_clients()

    def _init_clients(self):
        host = self.config["inference"]["host"]
        for model_key, model_cfg in self.config["models"].items():
            port = model_cfg["port"]
            base_url = f"http://{host}:{port}/v1"
            self.clients[model_key] = OpenAI(base_url=base_url, api_key="not-needed")

    def _get_client(self, model_key: str) -> tuple[OpenAI, str]:
        if model_key not in self.clients:
            raise ValueError(f"Unknown model key: {model_key}. Available: {list(self.clients.keys())}")
        model_name = self.config["models"][model_key]["name"]
        return self.clients[model_key], model_name

    def chat(self, model_key: str, messages: list[dict], temperature: float = 0.7,
             max_tokens: int = 4096, json_mode: bool = False, **kwargs) -> str:
        """Send a chat completion request to the specified model."""
        client, model_name = self._get_client(model_key)
        system_prompt = _load_prompt(self.config["models"][model_key]["role"])

        full_messages = [{"role": "system", "content": system_prompt}] + messages

        create_kwargs = {
            "model": model_name,
            "messages": full_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            create_kwargs["response_format"] = {"type": "json_object"}

        start = time.time()
        response = client.chat.completions.create(**create_kwargs)
        duration = time.time() - start

        result = response.choices[0].message.content
        usage = response.usage

        TaskQueue.increment_stat(f"tokens:{model_key}", usage.total_tokens if usage else 0)
        TaskQueue.increment_stat(f"calls:{model_key}")

        return result

    def chat_json(self, model_key: str, messages: list[dict], **kwargs) -> dict:
        """Send a chat request and parse the response as JSON."""
        raw = self.chat(model_key, messages, json_mode=True, **kwargs)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(raw[start:end])
            start = raw.find("[")
            end = raw.rfind("]") + 1
            if start >= 0 and end > start:
                return json.loads(raw[start:end])
            raise

    def orchestrator(self, messages: list[dict], **kwargs) -> str:
        return self.chat("orchestrator", messages, temperature=0.3, **kwargs)

    def discover(self, messages: list[dict], **kwargs) -> str:
        return self.chat("discovery", messages, temperature=0.8, **kwargs)

    def exploit(self, messages: list[dict], **kwargs) -> str:
        return self.chat("exploit", messages, temperature=0.4, **kwargs)

    def validate(self, messages: list[dict], **kwargs) -> str:
        return self.chat("validator", messages, temperature=0.1, **kwargs)

    def report(self, messages: list[dict], **kwargs) -> str:
        return self.chat("reporter", messages, temperature=0.5, **kwargs)

    def fast(self, messages: list[dict], **kwargs) -> str:
        return self.chat("fast", messages, temperature=0.2, max_tokens=1024, **kwargs)

    def health_check(self) -> dict:
        status = {}
        for model_key in self.config["models"]:
            try:
                client, model_name = self._get_client(model_key)
                client.models.list()
                status[model_key] = "healthy"
            except Exception as e:
                status[model_key] = f"error: {str(e)[:100]}"
        return status


# Singleton
_client: VLLMClient | None = None


def get_llm() -> VLLMClient:
    global _client
    if _client is None:
        _client = VLLMClient()
    return _client
