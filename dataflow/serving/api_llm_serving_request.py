import json
import os
import re
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from requests.adapters import HTTPAdapter
from tqdm import tqdm

from .. import get_logger
from ..core import LLMServingABC


class APILLMServing_request(LLMServingABC):
    def start_serving(self) -> None:
        self.logger.info("APILLMServing_request: no local service to start.")
        return

    def __init__(
        self,
        api_url: str = "https://api.openai.com/v1/chat/completions",
        key_name_of_api_key: str = "DF_API_KEY",
        model_name: str = "gpt-4o",
        temperature: float = 0.0,
        max_workers: int = 10,
        max_retries: int = 5,
        connect_timeout: float = 180.0,
        read_timeout: float = 1000.0,
        **configs: dict,
    ):
        self.api_url = api_url
        self.model_name = model_name
        self.max_workers = max_workers
        self.max_retries = max_retries

        self.timeout = (connect_timeout, read_timeout)
        if "timeout" in configs:
            warnings.warn(
                "The `timeout` parameter is deprecated. Please use `connect_timeout` and `read_timeout` instead.",
                DeprecationWarning,
            )
            self.timeout = (connect_timeout, configs["timeout"])
            configs.pop("timeout")

        self.configs = configs
        self.configs.update({"temperature": temperature})

        self.logger = get_logger()

        self.api_key = os.environ.get(key_name_of_api_key)
        if self.api_key is None:
            error_msg = (
                f"Lack of `{key_name_of_api_key}` in environment variables. "
                f"Please set `{key_name_of_api_key}` as your api-key to {api_url} before using APILLMServing_request."
            )
            self.logger.error(error_msg)
            raise ValueError(error_msg)

        self.session = requests.Session()
        adapter = HTTPAdapter(
            pool_connections=self.max_workers,
            pool_maxsize=self.max_workers,
            max_retries=0,
            pool_block=True,
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self._cooldown_lock = threading.Lock()
        self._cooldown_until = 0.0

        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Apifox/1.0.0 (https://apifox.com)",
        }

    def _wait_cooldown(self):
        with self._cooldown_lock:
            until = float(self._cooldown_until or 0.0)
        now = time.time()
        if until > now:
            time.sleep(until - now)

    def _set_cooldown(self, seconds: float):
        try:
            s = float(seconds or 0.0)
        except Exception:
            s = 0.0
        if s <= 0:
            return
        with self._cooldown_lock:
            self._cooldown_until = max(float(self._cooldown_until or 0.0), time.time() + s)

    def format_response(self, response: dict, is_embedding: bool = False):
        if is_embedding:
            return response.get("data", [{}])[0].get("embedding", [])

        message = response.get("choices", [{}])[0].get("message", {})
        content = message.get("content", "")

        if re.search(r"<think>.*?</think>.*?<answer>.*?</answer>", content, re.DOTALL):
            return content

        reasoning_content = message.get("reasoning_content")
        if reasoning_content:
            return f"<think>{reasoning_content}</think>\n<answer>{content}</answer>"

        return content

    def _api_chat_with_id(
        self,
        id: int,
        payload,
        model: str,
        is_embedding: bool = False,
        json_schema: dict = None,
    ):
        start = time.time()
        try:
            self._wait_cooldown()
            if is_embedding:
                payload = {"model": model, "input": payload}
            elif json_schema is None:
                payload = {"model": model, "messages": payload}
            else:
                payload = {
                    "model": model,
                    "messages": payload,
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "custom_response",
                            "strict": True,
                            "schema": json_schema,
                        },
                    },
                }

            payload.update(self.configs)
            payload = json.dumps(payload)
            response = self.session.post(
                self.api_url, headers=self.headers, data=payload, timeout=self.timeout
            )
            cost = time.time() - start
            if response.status_code == 200:
                response_data = response.json()
                return id, self.format_response(response_data, is_embedding)
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                try:
                    retry_after_s = float(retry_after) if retry_after else 60.0
                except Exception:
                    retry_after_s = 60.0
                self._set_cooldown(retry_after_s)
                self.logger.warning(
                    f"API rate limited id={id} status=429 cost={cost:.2f}s retry_after={retry_after_s}s body={response.text[:200]}"
                )
                return id, None
            self.logger.error(
                f"API request failed id={id} status={response.status_code} cost={cost:.2f}s body={response.text[:500]}"
            )
            return id, None

        except requests.exceptions.ConnectTimeout as e:
            cost = time.time() - start
            self.logger.error(f"API connect timeout (id={id}) cost={cost:.2f}s: {e}")
            raise RuntimeError(f"Cannot connect to LLM server (connect timeout): {e}") from e

        except requests.exceptions.ReadTimeout as e:
            cost = time.time() - start
            warnings.warn(f"API read timeout (id={id}) cost={cost:.2f}s: {e}", RuntimeWarning)
            return id, None

        except requests.exceptions.Timeout as e:
            cost = time.time() - start
            warnings.warn(f"API timeout (id={id}) cost={cost:.2f}s: {e}", RuntimeWarning)
            return id, None

        except requests.exceptions.ConnectionError as e:
            cost = time.time() - start
            msg = str(e).lower()
            if "read timed out" in msg:
                warnings.warn(f"API read timeout (id={id}) cost={cost:.2f}s: {e}", RuntimeWarning)
                return id, None
            if "connect timeout" in msg or ("timed out" in msg and "connect" in msg):
                self.logger.error(f"API connect timeout (id={id}) cost={cost:.2f}s: {e}")
                raise RuntimeError(
                    f"Cannot connect to LLM server (connect timeout): {e}"
                ) from e
            self.logger.error(f"API connection error (id={id}) cost={cost:.2f}s: {e}")
            raise RuntimeError(f"Cannot connect to LLM server: {e}") from e

        except Exception as e:
            cost = time.time() - start
            self.logger.exception(f"API request error (id = {id}) cost={cost:.2f}s: {e}")
            return id, None

    def _api_chat_id_retry(self, id, payload, model, is_embedding: bool = False, json_schema: dict = None):
        for i in range(self.max_retries):
            id, response = self._api_chat_with_id(id, payload, model, is_embedding, json_schema)
            if response is not None:
                return id, response
            time.sleep(2**i)
        return id, None

    def _run_threadpool(self, task_args_list: list[dict], desc: str) -> list:
        responses = [None] * len(task_args_list)
        executor = ThreadPoolExecutor(max_workers=self.max_workers)
        futures = []
        interrupted = False
        try:
            futures = [
                executor.submit(self._api_chat_id_retry, **task_args)
                for task_args in task_args_list
            ]
            for future in tqdm(as_completed(futures), total=len(futures), desc=desc):
                try:
                    response = future.result()
                    responses[response[0]] = response[1]
                except KeyboardInterrupt:
                    interrupted = True
                    raise
                except Exception:
                    self.logger.exception("Worker crashed unexpectedly in threadpool")
        except KeyboardInterrupt:
            self.logger.warning("KeyboardInterrupt received: cancelling outstanding LLM requests")
            for f in futures:
                try:
                    f.cancel()
                except Exception:
                    pass
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                executor.shutdown(wait=False)
            raise
        finally:
            if not interrupted:
                executor.shutdown(wait=True)
        return responses

    def generate_from_input(
        self,
        user_inputs: list[str],
        system_prompt: str = "You are a helpful assistant",
        json_schema: dict = None,
    ) -> list[str]:
        task_args_list = [
            dict(
                id=idx,
                payload=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question},
                ],
                model=self.model_name,
                json_schema=json_schema,
            )
            for idx, question in enumerate(user_inputs)
        ]
        return self._run_threadpool(task_args_list, desc="Generating responses from prompts......")

    def generate_from_conversations(self, conversations: list[list[dict]]) -> list[str]:
        task_args_list = [
            dict(
                id=idx,
                payload=dialogue,
                model=self.model_name,
            )
            for idx, dialogue in enumerate(conversations)
        ]
        return self._run_threadpool(task_args_list, desc="Generating responses from conversations......")

    def generate_embedding_from_input(self, texts: list[str]) -> list[list[float]]:
        task_args_list = [
            dict(
                id=idx,
                payload=txt,
                model=self.model_name,
                is_embedding=True,
            )
            for idx, txt in enumerate(texts)
        ]
        return self._run_threadpool(task_args_list, desc="Generating embedding......")

    def cleanup(self):
        self.logger.info("Cleaning up resources in APILLMServing_request")
        try:
            if hasattr(self, "session") and self.session:
                self.session.close()
        except Exception:
            self.logger.exception("Failed to close requests session")
