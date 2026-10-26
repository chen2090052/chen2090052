import configparser
import json
import os
from typing import Any, Dict, Optional

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 同时存在多个配置文件时按此顺序加载，后者覆盖前者
CONFIG_FILES = ("config.toml", "config.ini", "config.json", "config.yaml", "config.yml")


class Config:
    def __init__(self, config_path: Optional[str] = None):
        explicit = config_path or os.environ.get("CAGENT_CONFIG")
        if explicit:
            self.config_paths = [explicit]
        else:
            self.config_paths = [
                path for path in (os.path.join(ROOT_DIR, name) for name in CONFIG_FILES)
                if os.path.exists(path)
            ]
        self.config_path = self.config_paths[0] if self.config_paths else os.path.join(ROOT_DIR, "config.toml")
        self._data: Dict[str, Any] = {}
        self._load()

    def _load(self):
        for path in self.config_paths:
            data = self._read_file(path)
            if isinstance(data, dict):
                self._merge(self._data, data)

        # 配置文件里缺失的 LLM 项，用环境变量/默认值补齐，保证 Agent 总能拿到完整配置
        llm = self._data.setdefault("llm", {})
        if isinstance(llm, dict):
            llm.setdefault("base_url", os.environ.get("CAGENT_LLM_BASE_URL") or os.environ.get("OLLAMA_BASE_URL") or "http://localhost:11434/v1")
            llm.setdefault("api_key", os.environ.get("CAGENT_LLM_API_KEY") or os.environ.get("OLLAMA_API_KEY") or "ollama")
            llm.setdefault("model", os.environ.get("CAGENT_MODEL") or os.environ.get("OLLAMA_MODEL") or "llama3.1")
            llm.setdefault("api_type", os.environ.get("CAGENT_LLM_API_TYPE") or "openai_v1")  # openai_v1 or ollama_native

    @staticmethod
    def _read_file(path: str) -> Dict[str, Any]:
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext == ".ini":
                parser = configparser.ConfigParser()
                parser.read(path, encoding="utf-8")
                return {section: dict(parser.items(section)) for section in parser.sections()}
            if ext == ".json":
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            if ext == ".toml":
                try:
                    import tomllib  # type: ignore
                    with open(path, "rb") as f:  # tomllib 只接受二进制模式
                        return tomllib.load(f)
                except ImportError:
                    import toml as tomllib_fallback  # type: ignore
                    with open(path, "r", encoding="utf-8") as f:
                        return tomllib_fallback.load(f)
            if ext in (".yaml", ".yml"):
                import yaml  # type: ignore
                with open(path, "r", encoding="utf-8") as f:
                    return yaml.safe_load(f) or {}
        except Exception:
            return {}
        return {}

    @staticmethod
    def _merge(target: Dict[str, Any], incoming: Dict[str, Any]) -> None:
        for key, value in incoming.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                Config._merge(target[key], value)
            else:
                target[key] = value

    def get(self, *keys, default=None):
        cur = self._data
        for k in keys:
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            else:
                return default
        return cur


def get_config() -> Config:
    return Config()
