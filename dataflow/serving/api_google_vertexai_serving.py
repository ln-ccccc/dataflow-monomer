import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..core import LLMServingABC

try:
    import vertexai
    try:
        from vertexai.generative_models import GenerativeModel, GenerationConfig
    except Exception:
        from vertexai.preview.generative_models import GenerativeModel, GenerationConfig
except Exception:
    vertexai = None
    GenerativeModel = None
    GenerationConfig = None


class APIGoogleVertexAIServing(LLMServingABC):
    def __init__(
        self,
        project=None,
        location="us-central1",
        model_name="gemini-2.5-flash",
        max_workers=10,
        max_tokens=8192,
        timeout=60,
        temperature=0.0,
        **kwargs,
    ):
        self.project = project or os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT_ID") or ""
        self.location = location
        self.model_name = model_name
        self.max_workers = max(1, int(max_workers or 1))
        self.max_tokens = int(max_tokens or 8192)
        self.timeout = int(timeout or 60)
        self.temperature = float(temperature or 0.0)
        self.kwargs = kwargs or {}
        self._model = None
        # 初始化全局线程池，避免每次 batch 重新创建
        self.executor = ThreadPoolExecutor(max_workers=self.max_workers)

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        if vertexai is None or GenerativeModel is None:
            raise RuntimeError("vertexai not available")
        if not self.project:
            raise RuntimeError("missing GOOGLE_CLOUD_PROJECT/GCP_PROJECT_ID")
        # 只初始化一次
        vertexai.init(project=self.project, location=self.location)
        self._model = GenerativeModel(self.model_name)
        return self._model
    
    def __del__(self):
        if hasattr(self, "executor"):
            self.executor.shutdown(wait=False)

    def _one(self, text):
        try:
            model = self._ensure_model()
            cfg = None
            if GenerationConfig is not None:
                cfg = GenerationConfig(
                    max_output_tokens=self.max_tokens,
                    temperature=self.temperature,
                )
            resp = model.generate_content(text, generation_config=cfg)
            out = getattr(resp, "text", None)
            if isinstance(out, str) and out.strip():
                return out
            candidates = getattr(resp, "candidates", None) or []
            if candidates:
                content = getattr(candidates[0], "content", None)
                parts = getattr(content, "parts", None) or []
                if parts:
                    t = getattr(parts[0], "text", "") or ""
                    return str(t)
            return ""
        except Exception:
            return ""

    def generate_from_input(self, inputs):
        if not inputs:
            return []
        results = [""] * len(inputs)
        # 使用复用的 executor
        futs = {self.executor.submit(self._one, text): idx for idx, text in enumerate(inputs)}
        for fut in as_completed(futs):
            idx = futs[fut]
            try:
                results[idx] = fut.result()
            except Exception:
                results[idx] = ""
        return results

