import json
import os
import sys
import pandas as pd
import time
import re
import threading
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor
import requests
from rdkit import Chem

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from dataflow.serving.api_google_vertexai_serving import APIGoogleVertexAIServing
from dataflow.serving.api_llm_serving_request import APILLMServing_request
from operators.general.chunked_generator import ChunkedPromptedGenerator
from dataflow.operators.core_text import PandasOperator
from prompts.monomer import MonomerNameExtractPrompt
from dataflow.utils.storage import LazyFileStorage, FileStorage
from utils.format_utils import safe_parse_json

class MonomerListProcessor:
    def _clean_text(self, text):
        if not text:
            return ""
        s = str(text).strip()
        
        # 1. 移除类似 <sep><r>0:NH2</r> 的后缀 (LLM 生成噪音)
        if "<sep>" in s:
            s = s.split("<sep>")[0]
            
        # 2. 移除 Python list 字符串表示的残留 (如 "['c1...")
        if s.startswith("['") and "']" in s:
            # 简单提取第一个元素
            s = s[2:].split("',")[0].split("']")[0]
        elif s.startswith('["') and '"]' in s:
            s = s[2:].split('",')[0].split('"]')[0]
            
        # 3. 移除 | 和 < 字符 (LLM 分隔符或 HTML 标签残留)
        if "|" in s:
            s = s.split("|")[0]
        if "<" in s:
            s = s.split("<")[0]
            
        # 4. 移除 Markdown 代码块标记残留
        s = s.replace("```json", "").replace("```csv", "").replace("```", "")
        
        # 5. 移除常见的行内干扰符号 (如冒号后跟随的无意义内容，需谨慎，仅移除特定模式)
        # 例如 "SMILES: C=C..." -> "C=C..."
        if s.lower().startswith("smiles:"):
            s = s[7:].strip()
        if s.lower().startswith("name:"):
            s = s[5:].strip()
            
        # 6. 移除首尾引号 (如果未被之前的列表解析覆盖)
        s = s.strip("'").strip('"')
            
        return s.strip()

    def _normalize_monomer(self, m):
        if not isinstance(m, dict):
            return None
        doi = m.get("doi")
        abbreviation = m.get("abbreviation") or []
        full_name = m.get("full_name") or []
        smiles = m.get("smiles") or ""
        
        return {
            "doi": doi,
            "abbreviation": [self._clean_text(x) for x in abbreviation if self._clean_text(x)],
            "full_name": [self._clean_text(x) for x in full_name if self._clean_text(x)],
            "smiles": self._clean_text(smiles),
        }

    def _merge_one(self, existing, incoming):
        existing["abbreviation"] = list(set(existing.get("abbreviation", []) + incoming.get("abbreviation", [])))
        existing["full_name"] = list(set(existing.get("full_name", []) + incoming.get("full_name", [])))
        if not existing.get("doi") and incoming.get("doi"):
            existing["doi"] = incoming.get("doi")
        if not existing.get("smiles") and incoming.get("smiles"):
            existing["smiles"] = incoming.get("smiles")
        return existing

    def _dedupe_and_merge(self, monomers):
        unique = {}
        global_doi = None
        for item in monomers:
            monomer = self._normalize_monomer(item)
            if not monomer:
                continue
            if not global_doi and monomer.get("doi"):
                global_doi = monomer["doi"]
            key = monomer.get("smiles") or (monomer["full_name"][0] if monomer["full_name"] else (monomer["abbreviation"][0] if monomer["abbreviation"] else ""))
            if not key:
                continue
            if key not in unique:
                unique[key] = monomer
            else:
                unique[key] = self._merge_one(unique[key], monomer)
        if global_doi:
            for m in unique.values():
                if not m.get("doi"):
                    m["doi"] = global_doi
        return list(unique.values())

    def _from_monomer_names(self, names):
        items = []
        for item in (names or []):
            name = ""
            if isinstance(item, dict):
                name = item.get("monomer_name", "")
            else:
                name = item
            name = str(name).strip()
            if not name:
                continue
            items.append({
                "doi": None,
                "abbreviation": [],
                "full_name": [name],
                "smiles": "",
            })
        return items

    def process_monomer_list_chunks(self, raw_list):
        all_monomers = []
        for chunk_res in (raw_list or []):
            parsed = safe_parse_json(chunk_res, [])
            if isinstance(parsed, dict):
                if isinstance(parsed.get("monomers_info"), list):
                    parsed = parsed.get("monomers_info")
                elif isinstance(parsed.get("monomer_names"), list):
                    parsed = self._from_monomer_names(parsed.get("monomer_names"))
                else:
                    parsed = []
            if not isinstance(parsed, list):
                parsed = []
            all_monomers.extend(parsed)
        return self._dedupe_and_merge(all_monomers)


class MonomerSeedStage:
    def __init__(self, llm_serving, list_processor, max_chunk_len=24000, use_schema=True):
        self.prompt = MonomerNameExtractPrompt()
        self.prompt_generator = ChunkedPromptedGenerator(
            llm_serving=llm_serving,
            prompt_template=self.prompt,
            json_schema=self.prompt.build_json_schema() if use_schema else None,
            max_chunk_len=max_chunk_len,
            disable_chunking=False
        )
        self.process_seed_monomers = PandasOperator([
            lambda df: df.assign(
                monomers_seed=df["monomers_seed_raw"].apply(list_processor.process_monomer_list_chunks)
            ).drop(columns=["monomers_seed_raw", "content"], errors="ignore")
        ])

    def run(self, storage):
        self.prompt_generator.run(
            storage=storage.step(),
            input_key="content",
            output_key="monomers_seed_raw"
        )
        self.process_seed_monomers.run(storage=storage.step())


class MonomerSmilesEnrichStage:
    class _TransientNetworkError(Exception):
        pass

    class _RateLimiter:
        def __init__(self, min_interval_seconds: float):
            self._min_interval = max(0.0, float(min_interval_seconds or 0.0))
            self._lock = threading.Lock()
            self._next_time = 0.0

        def wait(self):
            if self._min_interval <= 0:
                return
            with self._lock:
                now = time.time()
                wait_s = self._next_time - now
                if wait_s > 0:
                    time.sleep(wait_s)
                self._next_time = max(self._next_time, time.time()) + self._min_interval

    def __init__(
        self,
        timeout=10,
        sleep_every=500,
        sleep_seconds=0.1,
        api_workers=100,
        row_workers=10,
        http_max_retries=3,
        http_backoff_factor=1.0,
        http_max_backoff=20.0,
        pubchem_min_interval=0.5,
        opsin_min_interval=0.5,
        cactus_min_interval=0.5,
        http_max_inflight=15,
        parallel_services=True,
        pubchem_disable_proxy=True,
        opsin_disable_proxy=False,
        cactus_disable_proxy=False,
    ):
        self.timeout = timeout
        self.sleep_every = sleep_every
        self.sleep_seconds = sleep_seconds
        self.api_workers = max(1, int(api_workers or 1))
        self.row_workers = max(1, int(row_workers or 1))
        self.http_max_retries = max(0, int(http_max_retries or 0))
        self.http_backoff_factor = float(http_backoff_factor or 0.0)
        self.http_max_backoff = float(http_max_backoff or 0.0)
        self._request_count = 0
        self._lock = threading.Lock()
        self._session_local = threading.local()
        self._http_sem = threading.Semaphore(max(1, int(http_max_inflight or 1)))
        self._rate_pubchem = self._RateLimiter(pubchem_min_interval)
        self._rate_opsin = self._RateLimiter(opsin_min_interval)
        self._rate_cactus = self._RateLimiter(cactus_min_interval)
        self.parallel_services = bool(parallel_services)
        self._svc_executor = ThreadPoolExecutor(max_workers=max(3, int(http_max_inflight or 1)))
        self.pubchem_disable_proxy = bool(pubchem_disable_proxy)
        self.opsin_disable_proxy = bool(opsin_disable_proxy)
        self.cactus_disable_proxy = bool(cactus_disable_proxy)
        self._name_cache = {}
        self._name_cache_lock = threading.Lock()
        self._smiles_canon_cache = {}
        self._smiles_canon_cache_lock = threading.Lock()
        self.enrich_operator = PandasOperator([
            lambda df: df.assign(
                monomers_info=df.apply(self._enrich_from_row, axis=1)
            )
        ])

    def _to_mol(self, s):
        s = self._normalize_smiles(s)
        if not s:
            return None
        try:
            mol = Chem.MolFromSmiles(s, sanitize=True)
            if mol:
                return mol
        except Exception:
            pass
        try:
            mol = Chem.MolFromSmiles(s, sanitize=True)
            if mol:
                res = Chem.SanitizeMol(mol, catchErrors=True)
                if res == Chem.SanitizeFlags.SANITIZE_NONE:
                    return mol
        except Exception:
            pass
        return None

    def _clean_text(self, text):
        if not text:
            return ""
        s = str(text).strip()
        # 移除类似 <sep><r>0:NH2</r> 的后缀 (LLM 生成噪音)
        if "<sep>" in s:
            s = s.split("<sep>")[0]
        return s.strip()

    def _normalize_smiles(self, s):
        return self._clean_text(s)
        
    # 控制请求频率
    def _throttle(self):
        if not self.sleep_every or self.sleep_every <= 0:
            return
        should_sleep = False
        with self._lock:
            self._request_count += 1
            if self._request_count % self.sleep_every == 0:
                should_sleep = True
        if should_sleep:
            time.sleep(self.sleep_seconds)

   # 获取 HTTP Session (线程安全)
    def _get_session(self, trust_env: bool = True):
        attr = "session_env" if trust_env else "session_noenv"
        session = getattr(self._session_local, attr, None)
        if session is None:
            session = requests.Session()
            session.trust_env = bool(trust_env)
            try:
                from requests.adapters import HTTPAdapter
                adapter = HTTPAdapter(max_retries=0, pool_connections=50, pool_maxsize=50, pool_block=True)
                session.mount("http://", adapter)
                session.mount("https://", adapter)
            except Exception:
                pass
            setattr(self._session_local, attr, session)
        return session

    # 指数退避策略
    def _sleep_backoff(self, attempt: int, retry_after: float | None = None):
        if retry_after is not None and retry_after > 0:
            time.sleep(min(float(retry_after), self.http_max_backoff or float(retry_after)))
            return
        if attempt <= 0 or self.http_backoff_factor <= 0:
            return
        base = min(self.http_max_backoff, self.http_backoff_factor * (2 ** (attempt - 1)))
        jitter = (time.time() % 1.0) * 0.25
        time.sleep(max(0.0, base + jitter))

    def _get(self, url, as_json=False, service: str | None = None):
        logger = None
        try:
            from dataflow import get_logger
            logger = get_logger()
        except ImportError:
            pass
            
        rate = None
        if service == "pubchem":
            rate = self._rate_pubchem
        elif service == "opsin":
            rate = self._rate_opsin
        elif service == "cactus":
            rate = self._rate_cactus

        disable_proxy = False
        if service == "pubchem":
            disable_proxy = self.pubchem_disable_proxy
        elif service == "opsin":
            disable_proxy = self.opsin_disable_proxy
        elif service == "cactus":
            disable_proxy = self.cactus_disable_proxy

        last_exc = None
        last_status = None

        for attempt in range(0, max(1, self.http_max_retries + 1)):
            try:
                if rate:
                    rate.wait()
                self._throttle()
                with self._http_sem:
                    session = self._get_session(trust_env=not disable_proxy)
                    if logger:
                        logger.debug(f"Requesting URL: {url}")
                    res = session.get(url, timeout=self.timeout, allow_redirects=False)
                last_status = res.status_code

                if res.status_code in (301, 302, 303, 307, 308):
                    loc = res.headers.get("Location") or ""
                    if "misuse.ncbi.nlm.nih.gov" in loc:
                        raise self._TransientNetworkError(f"ncbi_abuse_block redirect={loc}")
                    return None

                if res.status_code == 200:
                    if not as_json:
                        return res.text

                    # 优先通过 Content-Type 判断是否真的是 JSON
                    ct = (res.headers.get("Content-Type") or "").lower()
                    if "json" not in ct:
                        # 对于 PubChem 这种 name 查询，如果 Content-Type 不是 JSON，
                        # 通常是“not match”等 HTML 文本，直接视为无结果，不再重试
                        if service == "pubchem":
                            return None
                        # 其他服务也直接当作无结果处理
                        return None

                    try:
                        return res.json()
                    except Exception as e:
                        # Content-Type 声称是 JSON 但解析失败，视为瞬态错误，可重试
                        last_exc = e
                        self._sleep_backoff(attempt + 1)
                        continue

                if res.status_code == 404:
                    return None

                retry_after = None
                if res.status_code == 429:
                    ra = res.headers.get("Retry-After")
                    if ra:
                        try:
                            retry_after = float(ra)
                        except Exception:
                            retry_after = None

                if res.status_code in (429, 500, 502, 503, 504):
                    self._sleep_backoff(attempt + 1, retry_after=retry_after)
                    continue

                return None

            except self._TransientNetworkError:
                raise
            except requests.exceptions.RequestException as e:
                last_exc = e
                self._sleep_backoff(attempt + 1)
                continue
            except Exception as e:
                last_exc = e
                self._sleep_backoff(attempt + 1)
                continue

        if logger:
            if last_status is not None:
                logger.warning(f"Request failed after retries status={last_status}: {url}")
            elif last_exc is not None:
                logger.warning(f"Request failed after retries error={str(last_exc)}: {url}")
        raise self._TransientNetworkError(str(last_exc) if last_exc else f"status={last_status}")

    def _query_pubchem(self, name):
        name = self._clean_text(name)
        if not name:
            return ""
        # 针对复杂名称中的特殊字符进行处理，例如引号
        # 1. 先尝试完全 unquote，如果已经是 URL encoded
        # 2. 对引号进行转义或替换
        # 但最稳妥的是直接 quote，Python quote 会处理大部分特殊字符
        # 注意：PubChem 可能不喜欢 name 中的某些字符，如换行符等，这里先 strip
        
        # 尝试 1: 直接 quote
        encoded_name = quote(name, safe='')
        
        url = (
            "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
            f"{encoded_name}/property/"
            "IsomericSMILES,CanonicalSMILES,ConnectivitySMILES/JSON"
        )
        data = self._get(url, as_json=True, service="pubchem")
        if not data:
             # 尝试 2: 如果名字里有引号，尝试替换或特殊处理
             # 有些时候 PubChem 对 URL 里的引号比较敏感，虽然 quote 了
             # 这里可以加一些 fallback 逻辑，比如去掉括号里的内容等，但暂时先只做标准 quote
             return ""
             
        props = data.get("PropertyTable", {}).get("Properties", [])
        if not props:
            return ""
        prop = props[0]
        for key in ("IsomericSMILES", "CanonicalSMILES", "ConnectivitySMILES"):
            value = prop.get(key)
            if value:
                return value
        return ""

    def _query_pubchem_smiles(self, smiles):
        url = (
            "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/smiles/"
            f"{quote(smiles, safe='')}/property/"
            "IsomericSMILES,CanonicalSMILES,ConnectivitySMILES/JSON"
        )
        data = self._get(url, as_json=True, service="pubchem") or {}
        props = data.get("PropertyTable", {}).get("Properties", [])
        if not props:
            return ""
        prop = props[0]
        for key in ("IsomericSMILES", "CanonicalSMILES", "ConnectivitySMILES"):
            value = prop.get(key)
            if value:
                return value
        return ""

    def _query_opsin(self, name):
        name = self._clean_text(name)
        if not name:
            return ""
        url = f"https://opsin.ch.cam.ac.uk/opsin/{quote(name, safe='')}.smi"
        text = self._get(url, as_json=False, service="opsin") or ""
        return str(text).strip()

    def _query_cactus(self, name):
        name = self._clean_text(name)
        if not name:
            return ""
        urls = [
            f"https://cactus.nci.nih.gov/chemical/structure/{quote(name, safe='')}/smiles",
            f"http://cactus.nci.nih.gov/chemical/structure/{quote(name, safe='')}/smiles",
        ]
        for u in urls:
            try:
                text = self._get(u, as_json=False, service="cactus") or ""
            except self._TransientNetworkError:
                continue
            text = str(text).strip()
            if text:
                return text
        return ""

    def _pick_final_smiles(self, pubchem_smiles, opsin_smiles, cactus_smiles):
        cp = self._canon_smiles(pubchem_smiles)
        co = self._canon_smiles(opsin_smiles)
        cc = self._canon_smiles(cactus_smiles)
        if cp and co and cp == co:
            return cp
        if cp and cc and cp == cc:
            return cp
        if co and cc and co == cc:
            return co
        if cp:
            return cp
        if co:
            return co
        if cc:
            return cc
        return ""

    def _is_valid_smiles(self, s):
        return self._to_mol(s) is not None

    def _canon_smiles(self, s):
        s = self._normalize_smiles(s)
        if not s:
            return ""
        mol = self._to_mol(s)
        if not mol:
            return ""
        try:
            return Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True)
        except Exception:
            return ""

    # 设置 SMILES 相关标志位
    def _set_smiles_flags(self, monomer):
        pubchem = str(monomer.get("smiles_pubchem", "")).strip()
        opsin = str(monomer.get("smiles_opsin", "")).strip()
        cactus = str(monomer.get("smiles_cactus", "")).strip()
        text_smiles = str(monomer.get("smiles", "")).strip()

        text_can = self._canon_smiles(text_smiles) if text_smiles else ""
        pubchem_can = self._canon_smiles(pubchem) if pubchem else ""
        opsin_can = self._canon_smiles(opsin) if opsin else ""
        cactus_can = self._canon_smiles(cactus) if cactus else ""

        monomer["smiles_can"] = text_can
        monomer["smiles_pubchem_can"] = pubchem_can
        monomer["smiles_opsin_can"] = opsin_can
        monomer["smiles_cactus_can"] = cactus_can

        api_candidates = [x for x in [pubchem_can, opsin_can, cactus_can] if x]
        uniq_api = list(dict.fromkeys(api_candidates))
        
        # smiles_api_can 填充逻辑：
        # 仅当三库 canonical 结果达成“唯一一致意见”时才赋值：
        # - 如果只有一个非空 canonical 结果，直接使用该结果；
        # - 如果多个结果但经去重后只剩一个（即三库 canonical 完全一致），使用该结果；
        # - 其它情况下（例如不同库 canonical 不一致），视为 API 无共识，不填 smiles_api_can。
        if not uniq_api:
            monomer["smiles_api_can"] = ""
        elif len(uniq_api) == 1:
            monomer["smiles_api_can"] = uniq_api[0]
        else:
            monomer["smiles_api_can"] = ""

        api_can = str(monomer.get("smiles_api_can", "")).strip()

        # 根据用户新规则重构判定逻辑
        # Invalid 条件 (满足任一):
        # 1. 正文 smiles 为空
        # 2. PubChem/OPSIN/CACTUS 三个 API 结果全为空
        # 3. 正文 SMILES / API SMILES 任一无法被 RDKit 正则化出 canonical (等价于 canonical 为空)
        # Valid 条件 (同时满足):
        # 1. 正文 smiles 不为空
        # 2. 三库 API 至少有一个不为空
        # 3. 正文 canonical smiles 与任意一个“非空 API 结果”的 canonical smiles 完全一致

        # text_can 为空 -> Invalid (规则1 & 3)
        if not text_can:
            monomer["smiles_final"] = ""
            monomer["smiles_valid"] = "invalid"
            return monomer

        # API 全为空 (canonical 后全空) -> Invalid (规则2)
        if not api_candidates:
            monomer["smiles_final"] = ""
            monomer["smiles_valid"] = "invalid"
            return monomer
            
        # 规则 3: 正文 canonical 与任意一个非空 API canonical 一致 -> Valid
        # 且仅当 smiles_can 与 smiles_api_can 一致时才生成 smiles_final
        # smiles_api_can 已经在前面被填充为 api_candidates 中的一致项或首选项
        if text_can and text_can == api_can:
            monomer["smiles_final"] = text_can
            monomer["smiles_valid"] = "valid"
        else:
            monomer["smiles_final"] = ""
            monomer["smiles_valid"] = "invalid"

        return monomer

    def _split_query_names(self, monomer):
        full_name = monomer.get("full_name", []) or []
        abbreviation = monomer.get("abbreviation", []) or []
        def norm_list(lst):
            names = []
            for x in lst:
                s = str(x).strip()
                if s:
                    names.append(s)
            return names
        return norm_list(full_name), norm_list(abbreviation)

    def _normalize_name(self, name):
        s = str(name)
        s = s.replace("′", "'").replace("’", "'").replace("–", "-").replace("—", "-")
        s = s.replace("−", "-").replace(" ", " ")
        s = " ".join(s.split())
        s = s.strip(" ;,")
        return s

    def _derive_variants(self, name):
        s = self._normalize_name(name)
        variants = [s]
        low = s.lower()
        if "hexa-" in low or "hexa " in low:
            base = low.replace("hexa-", "hexa").replace("hexa ", "hexa")
            if base != low:
                variants.append(base)
        if "fluoropropane" in low and "fluoroisopropylidene" not in low:
            variants.append(re.sub(r"fluoropropane", "fluoroisopropylidene", low))
        if "diphthalic dianhydride" in low:
            variants.append(low.replace("diphthalic dianhydride", "diphthalic anhydride"))
        if "diphthalic anhydride" in low:
            variants.append(low.replace("diphthalic anhydride", "diphthalic dianhydride"))
        if "hexafluoroisopropylidene" in low and "diphthalic" in low:
            variants.append("4,4'-(hexafluoroisopropylidene)diphthalic anhydride")
            variants.append("6FDA")
        if "diphenyl methane" in s:
            variants.append(s.replace("diphenyl methane", "diphenylmethane"))
        if "diphenylmethane" in s:
            variants.append(s.replace("diphenylmethane", "diphenyl methane"))
        if "diamino" in s and "diphenylmethane" in s:
            variants.append(s.replace("diphenylmethane", "methylenedianiline"))
        if "diamino" in s and "diphenyl methane" in s:
            variants.append(s.replace("diphenyl methane", "methylenedianiline"))
        return list(dict.fromkeys([str(v).strip() for v in variants if str(v).strip()]))

    def _query_candidate(self, name):
        with self._name_cache_lock:
            cached = self._name_cache.get(name)
        if cached is not None:
            return cached
            
        # 按照用户要求：PubChem, OPSIN, CACTUS 独立查询，互不干扰
        # 可以并行查询以提高速度，也可以串行
        # 这里为了逻辑清晰和避免过度并发，使用串行但确保每个都尝试
        
        transient = False

        if self.parallel_services:
            futures = {
                "pubchem": self._svc_executor.submit(self._query_pubchem, name),
                "opsin": self._svc_executor.submit(self._query_opsin, name),
                "cactus": self._svc_executor.submit(self._query_cactus, name),
            }
            results = {"pubchem": "", "opsin": "", "cactus": ""}
            for k, fut in futures.items():
                try:
                    results[k] = fut.result() or ""
                except self._TransientNetworkError:
                    transient = True
                    results[k] = ""
                except Exception:
                    results[k] = ""
            p, o, c = results["pubchem"], results["opsin"], results["cactus"]
        else:
            try:
                p = self._query_pubchem(name)
            except self._TransientNetworkError:
                p = ""
                transient = True
            except Exception:
                p = ""

            try:
                o = self._query_opsin(name)
            except self._TransientNetworkError:
                o = ""
                transient = True
            except Exception:
                o = ""

            try:
                c = self._query_cactus(name)
            except self._TransientNetworkError:
                c = ""
                transient = True
            except Exception:
                c = ""
        
        final = self._pick_final_smiles(p, o, c)
        
        result = (p, o, c, final)
        if final or p or o or c or not transient:
            with self._name_cache_lock:
                self._name_cache[name] = result
        return result

    def _enrich_single(self, monomer):
        if not isinstance(monomer, dict):
            return monomer
        full_names, abbreviations = self._split_query_names(monomer)
        if not full_names and not abbreviations:
            monomer["smiles_pubchem"] = ""
            monomer["smiles_opsin"] = ""
            monomer["smiles_cactus"] = ""
            monomer["smiles_final"] = ""
            return self._set_smiles_flags(monomer)
        seen = set()
        pubchem_smiles = ""
        opsin_smiles = ""
        cactus_smiles = ""
        def process_names(names):
            nonlocal pubchem_smiles, opsin_smiles, cactus_smiles
            candidates = []
            for n in names:
                parts = re.split(r"[;；]", str(n))
                for part in parts:
                    part = part.strip()
                    if not part:
                        continue
                    candidates.extend(self._derive_variants(part))
            for q in candidates:
                if q in seen:
                    continue
                seen.add(q)
                p, o, c, final = self._query_candidate(q)
                cp = str(p or "").strip()
                co = str(o or "").strip()
                cc = str(c or "").strip()
                if not pubchem_smiles and cp:
                    pubchem_smiles = cp
                if not opsin_smiles and co:
                    opsin_smiles = co
                if not cactus_smiles and cc:
                    cactus_smiles = cc
                if final:
                    monomer["smiles_pubchem"] = cp
                    monomer["smiles_opsin"] = co
                    monomer["smiles_cactus"] = cc
                    monomer["smiles_final"] = final
                    self._set_smiles_flags(monomer)
                    return True
            return False
        if process_names(full_names):
            return monomer
        if process_names(abbreviations):
            return monomer
        monomer["smiles_pubchem"] = pubchem_smiles
        monomer["smiles_opsin"] = opsin_smiles
        monomer["smiles_cactus"] = cactus_smiles
        monomer["smiles_final"] = self._pick_final_smiles(pubchem_smiles, opsin_smiles, cactus_smiles)
        return self._set_smiles_flags(monomer)

    def _enrich_monomer_list(self, monomers):
        if not monomers:
            return []
        if self.api_workers <= 1 or len(monomers) <= 1:
            return [self._enrich_single(m) for m in monomers]
        with ThreadPoolExecutor(max_workers=self.api_workers) as executor:
            return list(executor.map(self._enrich_single, monomers))

    def _enrich_from_row(self, row):
        monomers = row.get("monomers_info")
        if monomers:
            return self._enrich_monomer_list(monomers)
        seed = row.get("monomers_seed") or []
        return self._enrich_monomer_list(seed)

    def run(self, storage):
        step_storage = storage.step()
        df = step_storage.read("dataframe")
        records = df.to_dict(orient="records")
        logger = None
        try:
            from dataflow import get_logger
            logger = get_logger()
        except Exception:
            logger = None
        total = len(records)
        if logger:
            logger.info(f"Running MonomerSmilesEnrichStage rows {total} api_workers {self.api_workers} row_workers {self.row_workers}")
        try:
            progress_every = int(os.getenv("MONOMER_ENRICH_PROGRESS_EVERY") or "50")
        except Exception:
            progress_every = 50
        if progress_every <= 0:
            progress_every = 0

        if self.row_workers <= 1 or total <= 1:
            monomers_info = []
            for i, r in enumerate(records, 1):
                monomers_info.append(self._enrich_from_row(r))
                if logger and progress_every and i % progress_every == 0:
                    logger.info(f"MonomerSmilesEnrichStage progress {i}/{total}")
        else:
            with ThreadPoolExecutor(max_workers=self.row_workers) as executor:
                monomers_info = list(executor.map(self._enrich_from_row, records))
        df["monomers_info"] = monomers_info
        step_storage.write(df)
        if logger:
            logger.info("MonomerSmilesEnrichStage complete")

 

class ExtractMonomer:
    def __init__(
        self,
        entry_file_name: str,
        max_chunk_len=24000,
        api_workers=None,
        api_timeout=None,
        api_sleep_every=100,
        api_sleep_seconds=1,
        api_row_workers=None,
        llm_max_workers=None,
        llm_max_tokens=None,
        library_output_path=None,
        use_batch=False
    ):
        def _env_int(name, fallback):
            val = os.getenv(name)
            if val is None or val == "":
                return fallback
            try:
                return int(val)
            except Exception:
                return fallback

        def _env_float(name, fallback):
            val = os.getenv(name)
            if val is None or val == "":
                return fallback
            try:
                return float(val)
            except Exception:
                return fallback

        api_workers = api_workers if api_workers is not None else _env_int("MONOMER_API_WORKERS", 4)
        api_timeout = api_timeout if api_timeout is not None else _env_int("MONOMER_API_TIMEOUT", 10)
        api_sleep_every = api_sleep_every if api_sleep_every is not None else _env_int("MONOMER_API_SLEEP_EVERY", 50)
        api_sleep_seconds = api_sleep_seconds if api_sleep_seconds is not None else _env_float("MONOMER_API_SLEEP_SECONDS", 0.5)
        api_row_workers = api_row_workers if api_row_workers is not None else _env_int("MONOMER_API_ROW_WORKERS", 4)
        pubchem_min_interval = _env_float("MONOMER_PUBCHEM_MIN_INTERVAL", 0.5)
        opsin_min_interval = _env_float("MONOMER_OPSIN_MIN_INTERVAL", 0.5)
        cactus_min_interval = _env_float("MONOMER_CACTUS_MIN_INTERVAL", 0.5)
        http_max_inflight = _env_int("MONOMER_HTTP_MAX_INFLIGHT", 15)
        http_max_retries = _env_int("MONOMER_HTTP_MAX_RETRIES", 3)
        http_backoff_factor = _env_float("MONOMER_HTTP_BACKOFF_FACTOR", 1.0)
        http_max_backoff = _env_float("MONOMER_HTTP_MAX_BACKOFF", 20.0)
        parallel_services = _env_int("MONOMER_PARALLEL_SERVICES", 1)
        pubchem_disable_proxy = _env_int("MONOMER_PUBCHEM_DISABLE_PROXY", 1)
        opsin_disable_proxy = _env_int("MONOMER_OPSIN_DISABLE_PROXY", 0)
        cactus_disable_proxy = _env_int("MONOMER_CACTUS_DISABLE_PROXY", 0)
        llm_max_workers = llm_max_workers if llm_max_workers is not None else _env_int("MONOMER_LLM_MAX_WORKERS", 100)
        llm_max_tokens = llm_max_tokens if llm_max_tokens is not None else _env_int("MONOMER_LLM_MAX_TOKENS", 12800)
        llm_max_tokens_cap = _env_int("MONOMER_LLM_MAX_TOKENS_CAP", 32768)
        llm_max_retries = _env_int("MONOMER_LLM_MAX_RETRIES", 5)
        llm_connect_timeout = _env_float("MONOMER_LLM_CONNECT_TIMEOUT", 60.0)
        llm_read_timeout = _env_float("MONOMER_LLM_READ_TIMEOUT", 600.0)
        if llm_max_tokens < 1:
            llm_max_tokens = 1
        if llm_max_tokens_cap and llm_max_tokens_cap > 0 and llm_max_tokens > llm_max_tokens_cap:
            llm_max_tokens = llm_max_tokens_cap
        if llm_max_tokens >= 65537:
            llm_max_tokens = 65535

        self.storage = FileStorage(
            first_entry_file_name=entry_file_name,
            cache_path="./outputs/monomer",
            cache_type="json",
        )
        self.llm_serving = APIGoogleVertexAIServing(
            project=os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT_ID"),
            location='us-central1',
            model_name="gemini-2.5-flash",
            max_workers=llm_max_workers,
            max_tokens=llm_max_tokens,
            use_batch=use_batch,
        )
        # self.llm_serving = APILLMServing_request(
        #     api_url="https://ai-gateway-internal.dp.tech/v1/chat/completions",
        #     model_name="ksyun/gemini-2.5-flash",
        #     max_workers=llm_max_workers,
        #     max_retries=llm_max_retries,
        #     connect_timeout=llm_connect_timeout,
        #     read_timeout=llm_read_timeout,
        #     max_tokens=llm_max_tokens,
        # )
        self.list_processor = MonomerListProcessor()
        self.seed_stage = MonomerSeedStage(
            llm_serving=self.llm_serving,
            list_processor=self.list_processor,
            max_chunk_len=max_chunk_len,
            use_schema=True
        )
        self.smiles_stage = MonomerSmilesEnrichStage(
            timeout=api_timeout,
            sleep_every=api_sleep_every,
            sleep_seconds=api_sleep_seconds,
            api_workers=api_workers,
            row_workers=api_row_workers,
            pubchem_min_interval=pubchem_min_interval,
            opsin_min_interval=opsin_min_interval,
            cactus_min_interval=cactus_min_interval,
            http_max_inflight=http_max_inflight,
            http_max_retries=http_max_retries,
            http_backoff_factor=http_backoff_factor,
            http_max_backoff=http_max_backoff,
            parallel_services=bool(parallel_services),
            pubchem_disable_proxy=bool(pubchem_disable_proxy),
            opsin_disable_proxy=bool(opsin_disable_proxy),
            cactus_disable_proxy=bool(cactus_disable_proxy),
        )
        
        # 使用传入的 library_output_path，如果未传入则使用默认值
        default_library_path = "./data/monomer_library.csv"
        lib_path = library_output_path if library_output_path else default_library_path
        
        self.library_stage = MonomerLibrarySaveStage(library_path=lib_path)

    def compile(self):
        pass

    def submit_batch(self, input_jsonl):
        """
        Submits a batch of jobs to Vertex AI.
        Returns (job_ids, row_mapping).
        """
        rows = []
        with open(input_jsonl, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except:
                    continue
        
        all_prompts = []
        row_mapping = []
        
        for row in rows:
            content = row.get("content", "")
            if not content:
                continue
            
            # Using private method _split_recursive
            chunks = self.seed_stage.prompt_generator._split_recursive(content)
            
            prompt_kwargs = {
                "file_path": row.get("file_path"),
                "doi_hint": row.get("doi_hint"),
                "extracted_doi": row.get("extracted_doi"),
            }
            
            system_prompts = self.seed_stage.prompt.build_prompt(**prompt_kwargs)
            if not isinstance(system_prompts, list):
                system_prompts = [system_prompts] * len(chunks)
            
            llm_inputs = [sys_p + chunk for chunk, sys_p in zip(chunks, system_prompts)]
            all_prompts.extend(llm_inputs)
            
            row_info = prompt_kwargs.copy()
            row_info["num_chunks"] = len(chunks)
            row_mapping.append(row_info)
            
        if not all_prompts:
            return [], []

        # Submit using self.llm_serving
        job_ids = self.llm_serving.generate_from_input(
            all_prompts,
            json_schema=self.seed_stage.prompt.build_json_schema(),
            use_function_call=False,
            use_batch=True,
            batch_wait=False
        )
        
        if isinstance(job_ids, str):
            job_ids = [job_ids] if job_ids else []
            
        return job_ids, row_mapping

    def process_batch_result(self, job_ids, row_mapping, output_dir=None):
        """
        Polls jobs (if needed), retrieves results, and runs the rest of the pipeline.
        """
        runner = self.llm_serving.batch_runner
        if not runner:
             raise RuntimeError("Batch runner not available")
             
        full_result_map = {}
        for jid in job_ids:
             # wait_for_job returns URI immediately if done
             uri = runner.wait_for_job(jid)
             full_result_map.update(runner.retrieve_results(uri))
             
        reconstructed_rows = []
        current_idx = 0
        
        for row_info in row_mapping:
            num_chunks = row_info.get("num_chunks", 1)
            chunks = []
            for _ in range(num_chunks):
                key = f"req-{current_idx}"
                resp = full_result_map.get(key, "")
                chunks.append(resp)
                current_idx += 1
            
            monomers = self.list_processor.process_monomer_list_chunks(chunks)
            
            new_row = row_info.copy()
            new_row["monomers_seed"] = monomers
            reconstructed_rows.append(new_row)
            
        df = pd.DataFrame(reconstructed_rows)
        
        temp_cache_path = os.path.join(output_dir or "./outputs/monomer_batch", f"batch_{job_ids[0] if job_ids else 'empty'}")
        batch_storage = FileStorage(
            first_entry_file_name="dummy.json",
            cache_path=temp_cache_path,
            cache_type="json",
        )
        batch_storage.write(df) 

        batch_storage.operator_step = 0
        
        self.smiles_stage.run(batch_storage)
        
        self.library_stage.run(batch_storage)
        class DummyPipeline:
            def __init__(self, storage):
                self.storage = storage
        
        return DummyPipeline(batch_storage)

    def forward(self, batch_size=10, resume_from_last=False):
        self.seed_stage.run(self.storage)
        self.smiles_stage.run(self.storage)
        # self.library_stage.run(self.storage)


class MonomerLibrarySaveStage:
    def __init__(self, library_path="./data/monomer_library.csv"):
        self.library_path = library_path

    def _normalize_list(self, lst):
        return [str(x).strip() for x in (lst or []) if str(x).strip()]

    def _key_for(self, m):
        for k in ["smiles_final", "smiles_pubchem", "smiles_opsin", "smiles_cactus"]:
            v = str(m.get(k, "")).strip()
            if v:
                return v
        full_name = self._normalize_list(m.get("full_name", []))
        if full_name:
            return full_name[0]
        abbreviation = self._normalize_list(m.get("abbreviation", []))
        if abbreviation:
            return abbreviation[0]
        return ""

    def _pick_final(self, m):
        def norm(s):
            s = str(s).strip()
            if not s:
                return ""
            try:
                mol = Chem.MolFromSmiles(s)
            except Exception:
                mol = None
            if not mol:
                try:
                    mol = Chem.MolFromSmiles(s, sanitize=False)
                except Exception:
                    mol = None
                if mol:
                    try:
                        Chem.SanitizeMol(mol)
                    except Exception:
                        mol = None
            if not mol:
                return ""
            try:
                return Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True)
            except Exception:
                return ""
        p = norm(m.get("smiles_pubchem", ""))
        o = norm(m.get("smiles_opsin", ""))
        c = norm(m.get("smiles_cactus", ""))
        f = norm(m.get("smiles_final", ""))
        if p and o and p == o:
            return p
        if p and c and p == c:
            return p
        if o and c and o == c:
            return o
        if p:
            return p
        if o:
            return o
        if c:
            return c
        return f

    def _merge_record(self, existing, incoming):
        ab = list(set(self._normalize_list(existing.get("abbreviation", [])) + self._normalize_list(incoming.get("abbreviation", []))))
        fn = list(set(self._normalize_list(existing.get("full_name", [])) + self._normalize_list(incoming.get("full_name", []))))
        p = existing.get("smiles_pubchem", "") or incoming.get("smiles_pubchem", "") or ""
        o = existing.get("smiles_opsin", "") or incoming.get("smiles_opsin", "") or ""
        c = existing.get("smiles_cactus", "") or incoming.get("smiles_cactus", "") or ""
        
        # 新字段合并
        sc = existing.get("smiles_can", "") or incoming.get("smiles_can", "") or ""
        pc = existing.get("smiles_pubchem_can", "") or incoming.get("smiles_pubchem_can", "") or ""
        oc = existing.get("smiles_opsin_can", "") or incoming.get("smiles_opsin_can", "") or ""
        cc = existing.get("smiles_cactus_can", "") or incoming.get("smiles_cactus_can", "") or ""
        ac = existing.get("smiles_api_can", "") or incoming.get("smiles_api_can", "") or ""
        
        # 合并 DOI
        existing_doi = set(self._normalize_list(existing.get("doi", [])))
        incoming_doi = set(self._normalize_list(incoming.get("doi", [])))
        doi_list = list(existing_doi | incoming_doi)

        merged = {
            "abbreviation": ab,
            "full_name": fn,
            "smiles_pubchem": p,
            "smiles_opsin": o,
            "smiles_cactus": c,
            "smiles_can": sc,
            "smiles_pubchem_can": pc,
            "smiles_opsin_can": oc,
            "smiles_cactus_can": cc,
            "smiles_api_can": ac,
            "doi": doi_list, 
        }
        # 优先使用 existing 的 smiles_final（人工校正保护），如果 existing 没有才用 incoming
        merged["smiles_final"] = existing.get("smiles_final", "") or incoming.get("smiles_final", "") or ""
        return merged

    def _load_library(self):
        if not os.path.exists(self.library_path):
            return []
        try:
            df = pd.read_csv(self.library_path)
        except Exception:
            return []
        rows = []
        for _, row in df.iterrows():
            rows.append({
                "abbreviation": [x for x in str(row.get("abbreviation", "")).split(";") if x.strip()],
                "full_name": [x for x in str(row.get("full_name", "")).split(";") if x.strip()],
                "smiles_pubchem": str(row.get("smiles_pubchem", "")).strip(),
                "smiles_opsin": str(row.get("smiles_opsin", "")).strip(),
                "smiles_cactus": str(row.get("smiles_cactus", "")).strip(),
                "smiles_can": str(row.get("smiles_can", "")).strip(),
                "smiles_pubchem_can": str(row.get("smiles_pubchem_can", "")).strip(),
                "smiles_opsin_can": str(row.get("smiles_opsin_can", "")).strip(),
                "smiles_cactus_can": str(row.get("smiles_cactus_can", "")).strip(),
                "smiles_api_can": str(row.get("smiles_api_can", "")).strip(),
                "smiles_final": str(row.get("smiles_final", "")).strip(),
                "doi": [x for x in str(row.get("doi", "")).split(";") if x.strip()], 
            })
        return rows

    def _save_library(self, rows):
        normalized = []
        for v in rows:
            normalized.append({
                "abbreviation": ";".join(self._normalize_list(v.get("abbreviation", []))),
                "full_name": ";".join(self._normalize_list(v.get("full_name", []))),
                "smiles_pubchem": v.get("smiles_pubchem", ""),
                "smiles_opsin": v.get("smiles_opsin", ""),
                "smiles_cactus": v.get("smiles_cactus", ""),
                "smiles_can": v.get("smiles_can", ""),
                "smiles_pubchem_can": v.get("smiles_pubchem_can", ""),
                "smiles_opsin_can": v.get("smiles_opsin_can", ""),
                "smiles_cactus_can": v.get("smiles_cactus_can", ""),
                "smiles_api_can": v.get("smiles_api_can", ""),
                "smiles_final": v.get("smiles_final", ""),
                "doi": ";".join(self._normalize_list(v.get("doi", []))), 
            })
        os.makedirs(os.path.dirname(self.library_path), exist_ok=True)
        pd.DataFrame(normalized).to_csv(self.library_path, index=False)

    def run(self, storage):
        step_storage = storage.step()
        df = step_storage.read("dataframe")
        logger = None
        try:
            from dataflow import get_logger
            logger = get_logger()
        except Exception:
            logger = None
        rows = self._load_library()
        if logger:
            logger.info(f"Running MonomerLibrarySaveStage df_rows {len(df)} library_rows {len(rows)}")
        added = 0
        for _, row in df.iterrows():
            extracted_doi = str(getattr(row, "extracted_doi", "")).strip() 
            monomers = row.get("monomers_info", []) or []
            for m in monomers:
                # 至少命中一个 API 才入库 (用户原话: "且三库 API 至少有一个不为空" 是 Valid 条件，入库条件可能宽松些，但通常我们只存有信息的)
                # 这里沿用原逻辑：如果所有 API 都为空则不入库
                # 注意：Valid 规则里 "三库 API 全为空" 是 Invalid。
                if not (
                    str(m.get("smiles_pubchem", "")).strip()
                    or str(m.get("smiles_opsin", "")).strip()
                    or str(m.get("smiles_cactus", "")).strip()
                ):
                    continue
                
                # 尝试获取 monomer 内部的 doi，或者使用 extracted_doi
                m_doi = m.get("doi")
                current_dois = []
                if m_doi:
                    if isinstance(m_doi, list):
                        current_dois.extend(m_doi)
                    else:
                        current_dois.append(str(m_doi))
                
                if extracted_doi:
                    current_dois.append(extracted_doi)
                
                # 去重
                current_dois = list(set([str(d).strip() for d in current_dois if str(d).strip()]))

                rows.append({
                    "abbreviation": self._normalize_list(m.get("abbreviation", [])),
                    "full_name": self._normalize_list(m.get("full_name", [])),
                    "smiles_pubchem": str(m.get("smiles_pubchem", "")).strip(),
                    "smiles_opsin": str(m.get("smiles_opsin", "")).strip(),
                    "smiles_cactus": str(m.get("smiles_cactus", "")).strip(),
                    "smiles_can": str(m.get("smiles_can", "")).strip(),
                    "smiles_pubchem_can": str(m.get("smiles_pubchem_can", "")).strip(),
                    "smiles_opsin_can": str(m.get("smiles_opsin_can", "")).strip(),
                    "smiles_cactus_can": str(m.get("smiles_cactus_can", "")).strip(),
                    "smiles_api_can": str(m.get("smiles_api_can", "")).strip(),
                    "smiles_final": str(m.get("smiles_final", "")).strip(),
                    "doi": current_dois, 
                })
                added += 1
        self._save_library(rows)
        if logger:
            logger.info(f"MonomerLibrarySaveStage complete added {added} library_rows {len(rows)}")


def main(argv=None):
    from pipelines.monomer_extract_cli import main as _cli_main
    return _cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
