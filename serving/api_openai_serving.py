import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional
import requests

from dataflow.core import LLMServingABC


class APIOpenAICompatServing(LLMServingABC):
    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model_name: str = "gemini-2.5-pro",
        max_workers: int = 10,
        max_tokens: int = 12800,
        timeout: int = 60,
        temperature: float = 0.0,
    ):
        self.base_url = (base_url or os.getenv("LLM_OPENAI_BASE_URL") or "https://ai-gateway-internal.dp.tech").rstrip("/")
        if not self.base_url.endswith("/v1"):
            self.base_url = self.base_url + "/v1"
        self.api_key = api_key or os.getenv("LLM_OPENAI_API_KEY") or ""
        self.model_name = model_name
        self.max_workers = max(1, int(max_workers or 1))
        self.max_tokens = int(max_tokens or 12800)
        self.timeout = int(timeout or 60)
        self.temperature = float(temperature or 0.0)
        self.session = requests.Session()

    # Required by LLMServingABC
    def start_serving(self):
        return self

    # Required by LLMServingABC
    def cleanup(self):
        try:
            self.session.close()
        except Exception:
            pass

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _endpoint(self):
        return f"{self.base_url}/chat/completions"

    def _one(self, text: str) -> str:
        try:
            payload = {
                "model": self.model_name,
                "messages": [
                    {"role": "user", "content": text}
                ],
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
            }
            resp = self.session.post(self._endpoint(), json=payload, headers=self._headers(), timeout=self.timeout)
            if resp.status_code != 200:
                return ""
            data = resp.json()
            choices = data.get("choices") or []
            if not choices:
                return ""
            msg = choices[0].get("message") or {}
            content = msg.get("content") or ""
            return content
        except Exception:
            return ""

    def generate_from_input(self, inputs: List[str]) -> List[str]:
        if not inputs:
            return []
        results = [""] * len(inputs)
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(inputs))) as ex:
            futs = {ex.submit(self._one, text): idx for idx, text in enumerate(inputs)}
            for fut in as_completed(futs):
                idx = futs[fut]
                try:
                    results[idx] = fut.result()
                except Exception:
                    results[idx] = ""
        return results

