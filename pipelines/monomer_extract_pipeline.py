import os
import sys
import ctypes
import pandas as pd
import time
import re
import threading
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor
import requests

def _ensure_local_libstdcpp():
    try:
        if os.environ.get("LCC_LIBSTDCPP_READY") == "1":
            return
        candidates = []
        prefix = os.path.dirname(os.path.dirname(sys.executable))
        candidates.append(os.path.join(prefix, "lib", "libstdc++.so.6"))
        conda_prefix = os.environ.get("CONDA_PREFIX")
        if conda_prefix:
            candidates.append(os.path.join(conda_prefix, "lib", "libstdc++.so.6"))
        candidates.append("/opt/mamba/envs/lcc/lib/libstdc++.so.6")
        candidate = None
        for c in candidates:
            if os.path.exists(c):
                candidate = c
                break
        if not candidate:
            return
        lib_dir = os.path.dirname(candidate)
        cur_ld = os.environ.get("LD_LIBRARY_PATH", "")
        parts = [p for p in cur_ld.split(":") if p]
        if lib_dir not in parts:
            os.environ["LD_LIBRARY_PATH"] = lib_dir + (":" + cur_ld if cur_ld else "")
        cur = os.environ.get("LD_PRELOAD", "").strip()
        parts = [p for p in cur.split() if p]
        if candidate not in parts:
            os.environ["LD_PRELOAD"] = candidate + (" " + cur if cur else "")
        os.environ["LCC_LIBSTDCPP_READY"] = "1"
        try:
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception:
            try:
                ctypes.CDLL(candidate, mode=ctypes.RTLD_GLOBAL)
            except Exception:
                pass
    except Exception:
        pass


_ensure_local_libstdcpp()
def _load_env_from_setup_env(path=None):
    try:
        if path is None:
            path = os.getenv("LCC_SETUP_ENV_PATH", "/share/lcc/setup_env.sh")
        if not os.path.exists(path):
            return
        wanted = {
            "GOOGLE_APPLICATION_CREDENTIALS",
            "GCP_PROJECT_ID",
            "GOOGLE_CLOUD_PROJECT",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "http_proxy",
            "https_proxy",
            "no_proxy",
        }
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line.startswith("export "):
                    continue
                _, rest = line.split("export ", 1)
                if "=" not in rest:
                    continue
                key, value = rest.split("=", 1)
                key = key.strip()
                if key.startswith("MONOMER_") or key in wanted:
                    pass
                else:
                    continue
                if os.environ.get(key):
                    continue
                value = value.strip()
                if len(value) >= 2 and ((value[0] == value[-1] == '"') or (value[0] == value[-1] == "'")):
                    value = value[1:-1]
                os.environ[key] = value
        for upper, lower in [("HTTP_PROXY", "http_proxy"), ("HTTPS_PROXY", "https_proxy")]:
            if upper in os.environ and lower not in os.environ:
                os.environ[lower] = os.environ[upper]
    except Exception:
        pass

_load_env_from_setup_env()
from rdkit import Chem

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from operators.general.chunked_generator import ChunkedPromptedGenerator
from dataflow.operators.core_text import PandasOperator
from prompts.monomer import MonomerNameExtractPrompt

from dataflow.serving.api_google_vertexai_serving import APIGoogleVertexAIServing
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
        iupac_name = m.get("iupac_name")
        cas_no = m.get("cas_no") or []
        smiles = m.get("smiles") or ""
        
        return {
            "doi": doi,
            "abbreviation": [self._clean_text(x) for x in abbreviation if self._clean_text(x)],
            "full_name": [self._clean_text(x) for x in full_name if self._clean_text(x)],
            "iupac_name": (self._clean_text(iupac_name) if iupac_name else None),
            "cas_no": [self._clean_text(x) for x in cas_no if self._clean_text(x)],
            "smiles": self._clean_text(smiles),
        }

    def _merge_one(self, existing, incoming):
        existing["abbreviation"] = list(set(existing.get("abbreviation", []) + incoming.get("abbreviation", [])))
        existing["full_name"] = list(set(existing.get("full_name", []) + incoming.get("full_name", [])))
        existing["cas_no"] = list(set(existing.get("cas_no", []) + incoming.get("cas_no", [])))
        if not existing.get("iupac_name") and incoming.get("iupac_name"):
            existing["iupac_name"] = incoming.get("iupac_name")
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
                "iupac_name": None,
                "cas_no": [],
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
    def __init__(self, llm_serving, list_processor, max_chunk_len=24000):
        self.prompt = MonomerNameExtractPrompt()
        self.prompt_generator = ChunkedPromptedGenerator(
            llm_serving=llm_serving,
            prompt_template=self.prompt,
            json_schema=self.prompt.build_json_schema(),
            max_chunk_len=max_chunk_len
        )
        self.process_seed_monomers = PandasOperator([
            lambda df: df.assign(
                monomers_seed=df["monomers_seed_raw"].apply(list_processor.process_monomer_list_chunks)
            )
        ])

    def run(self, storage):
        self.prompt_generator.run(
            storage=storage.step(),
            input_key="content",
            output_key="monomers_seed_raw"
        )
        self.process_seed_monomers.run(storage=storage.step())


class MonomerSmilesEnrichStage:
    def __init__(self, timeout=10, sleep_every=500, sleep_seconds=0.1, api_workers=100, row_workers=10):
        self.timeout = timeout
        self.sleep_every = sleep_every
        self.sleep_seconds = sleep_seconds
        self.api_workers = max(1, int(api_workers or 1))
        self.row_workers = max(1, int(row_workers or 1))
        self._request_count = 0
        self._lock = threading.Lock()
        self._session_local = threading.local()
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

    def _get_session(self):
        session = getattr(self._session_local, "session", None)
        if session is None:
            session = requests.Session()
            self._session_local.session = session
        return session

    def _get(self, url, as_json=False):
        try:
            self._throttle()
            session = self._get_session()
            res = session.get(url, timeout=self.timeout)
            if res.status_code != 200:
                return None
            return res.json() if as_json else res.text
        except Exception:
            return None

    def _query_pubchem(self, name):
        name = self._clean_text(name)
        if not name:
            return ""
        url = (
            "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
            f"{quote(name)}/property/"
            "IsomericSMILES,CanonicalSMILES,ConnectivitySMILES/JSON"
        )
        data = self._get(url, as_json=True) or {}
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
            f"{quote(smiles)}/property/"
            "IsomericSMILES,CanonicalSMILES,ConnectivitySMILES/JSON"
        )
        data = self._get(url, as_json=True) or {}
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
        url = f"https://opsin.ch.cam.ac.uk/opsin/{quote(name)}.json"
        data = self._get(url, as_json=True) or {}
        if data.get("status") != "SUCCESS":
            return ""
        return data.get("smiles", "") or ""

    def _query_cactus(self, name):
        name = self._clean_text(name)
        if not name:
            return ""
        url = f"https://cactus.nci.nih.gov/chemical/structure/{quote(name)}/smiles"
        text = self._get(url, as_json=False) or ""
        text = text.strip()
        return text

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
        # 如果 API 结果一致，取该结果
        # 如果不一致但其中有一个与 text_can 一致，取 text_can
        # 否则取第一个非空结果作为参考（或者置空）
        if len(uniq_api) == 1:
            monomer["smiles_api_can"] = uniq_api[0]
        elif text_can and text_can in uniq_api:
            monomer["smiles_api_can"] = text_can
        else:
            monomer["smiles_api_can"] = uniq_api[0] if uniq_api else ""

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
        p = self._query_pubchem(name)
        o = self._query_opsin(name)
        c = ""
        final = self._pick_final_smiles(p, o, c)
        if not final:
            c = self._query_cactus(name)
            final = self._pick_final_smiles(p, o, c)
        result = (p, o, c, final)
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
        api_sleep_every=None,
        api_sleep_seconds=None,
        api_row_workers=None,
        llm_max_workers=None,
        llm_max_tokens=None,
        library_output_path=None
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
        llm_max_workers = llm_max_workers if llm_max_workers is not None else _env_int("MONOMER_LLM_MAX_WORKERS", 100)
        llm_max_tokens = llm_max_tokens if llm_max_tokens is not None else _env_int("MONOMER_LLM_MAX_TOKENS", 12800)
        if llm_max_tokens < 1:
            llm_max_tokens = 1
        if llm_max_tokens >= 65537:
            llm_max_tokens = 65535

        self.storage = FileStorage(
            first_entry_file_name=entry_file_name,
            cache_path="/share/lcc/dataflow-dp/outputs/monomer_demo",
            cache_type="json",
        )
        self.llm_serving = APIGoogleVertexAIServing(
            project=os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT_ID"),
            location='us-central1',
            model_name="gemini-2.5-pro",
            max_workers=llm_max_workers,
            max_tokens=llm_max_tokens,
        )
        self.list_processor = MonomerListProcessor()
        self.seed_stage = MonomerSeedStage(
            llm_serving=self.llm_serving,
            list_processor=self.list_processor,
            max_chunk_len=max_chunk_len
        )
        self.smiles_stage = MonomerSmilesEnrichStage(
            timeout=api_timeout,
            sleep_every=api_sleep_every,
            sleep_seconds=api_sleep_seconds,
            api_workers=api_workers,
            row_workers=api_row_workers,
        )
        
        # 使用传入的 library_output_path，如果未传入则使用默认值
        default_library_path = "/share/lcc/dataflow-dp/data/monomer_library.csv"
        lib_path = library_output_path if library_output_path else default_library_path
        
        self.library_stage = MonomerLibrarySaveStage(library_path=lib_path)

    def compile(self):
        pass

    def forward(self, batch_size=10, resume_from_last=False):
        self.seed_stage.run(self.storage)
        self.smiles_stage.run(self.storage)
        # self.library_stage.run(self.storage)


class MonomerLibrarySaveStage:
    def __init__(self, library_path="/share/lcc/dataflow-dp/data/monomer_library.csv"):
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
