import json
import os
import re
from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional

SKILLS_DIR = os.path.join(".agents", "skills")
SKILL_FILE = "SKILL.md"
STARTUP_CWD = os.getcwd()
MAX_FILE_BYTES = 200000

_KEY_RE = re.compile(r"^([A-Za-z0-9_.-]+)\s*:\s*(.*)$")
_FRONTMATTER_RE = re.compile(r"\A\ufeff?---[ \t]*\r?\n.*?\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: str
    skill_file: str

    def load_content(self) -> str:
        """按需读取 SKILL.md 全文。"""
        with open(self.skill_file, "r", encoding="utf-8-sig", errors="replace") as f:
            return f.read()

    def load_body(self) -> str:
        """按需读取 SKILL.md，并去掉 YAML Frontmatter。"""
        return _FRONTMATTER_RE.sub("", self.load_content(), count=1).lstrip("\r\n")


def _read_frontmatter_block(skill_file: str) -> Optional[str]:
    """只读到 Frontmatter 结束行，不加载 Skill 正文。"""
    with open(skill_file, "r", encoding="utf-8-sig", errors="replace") as f:
        if f.readline().strip() != "---":
            return None
        lines: List[str] = []
        for line in f:
            if line.strip() == "---":
                return "".join(lines)
            lines.append(line)
    return None


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        if value[0] == "'":
            return value[1:-1].replace("''", "'")
        try:
            return json.loads(value)
        except Exception:
            return value[1:-1]
    return re.sub(r"\s+#.*$", "", value).strip()


def _fold_block(lines: List[str], style: str) -> str:
    if style == "|":
        return "\n".join(lines).rstrip("\n")
    return " ".join(line.strip() for line in lines if line.strip()).strip()


def _parse_frontmatter(block: str) -> Dict[str, str]:
    data: Dict[str, str] = {}
    key: Optional[str] = None
    style: Optional[str] = None
    block_lines: List[str] = []

    def flush() -> None:
        nonlocal key, style, block_lines
        if key is not None and style is not None:
            data[key] = _fold_block(block_lines, style)
        key = None
        style = None
        block_lines = []

    for raw in block.splitlines():
        if style is not None:
            if not raw.strip() or raw.startswith((" ", "\t")):
                block_lines.append(raw)
                continue
            flush()

        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _KEY_RE.match(line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if value in ("|", ">", "|-", ">-", "|+", ">+"):
            style = value[0]
            block_lines = []
            continue
        data[key] = _unquote(value)
        key = None
    flush()
    return data


class SkillRegistry:
    """扫描 .agents/skills 并登记 Skill 元数据，正文按需加载。"""

    def __init__(self, project_root: Optional[str] = None):
        self.project_root = os.path.abspath(project_root or STARTUP_CWD)
        self.skills_dir = os.path.join(self.project_root, SKILLS_DIR)
        self._skills: Dict[str, Skill] = {}
        self.warnings: List[str] = []

    def discover(self) -> "SkillRegistry":
        self._skills.clear()
        self.warnings.clear()
        if not os.path.isdir(self.skills_dir):
            return self

        try:
            entries = sorted(os.scandir(self.skills_dir), key=lambda e: e.name.lower())
        except OSError as exc:
            self.warnings.append(f"无法读取 Skill 目录 {self.skills_dir}：{exc}")
            return self

        for entry in entries:
            if not entry.is_dir(follow_symlinks=False):
                continue
            skill_file = os.path.join(entry.path, SKILL_FILE)
            if not os.path.isfile(skill_file):
                self.warnings.append(f"跳过 {entry.path}：缺少 {SKILL_FILE}")
                continue
            try:
                skill = self._load_metadata(entry.path, skill_file)
            except Exception as exc:
                self.warnings.append(f"跳过 {entry.path}：读取 {SKILL_FILE} 失败（{exc}）")
                continue
            if skill is None:
                continue
            if skill.name in self._skills:
                existing = self._skills[skill.name]
                self.warnings.append(
                    f"跳过 {entry.path}：Skill 名称 {skill.name!r} 与 {existing.path} 重复"
                )
                continue
            self._skills[skill.name] = skill
        return self

    def _load_metadata(self, skill_dir: str, skill_file: str) -> Optional[Skill]:
        block = _read_frontmatter_block(skill_file)
        if block is None:
            self.warnings.append(f"跳过 {skill_dir}：{SKILL_FILE} 缺少 YAML Frontmatter")
            return None
        meta = _parse_frontmatter(block)
        name = str(meta.get("name") or "").strip()
        description = str(meta.get("description") or "").strip()
        missing = [key for key, value in (("name", name), ("description", description)) if not value]
        if missing:
            self.warnings.append(
                f"跳过 {skill_dir}：Frontmatter 缺少必填字段 {', '.join(missing)}"
            )
            return None
        return Skill(name=name, description=description, path=skill_dir, skill_file=skill_file)

    def system_prompt(self) -> str:
        """把已登记 Skill 的名称和描述整理成 System Prompt 片段。"""
        if not self._skills:
            return ""
        lines = ["You have access to the following skills:"]
        for skill in self._skills.values():
            description = " ".join(skill.description.split())
            lines.append(f"- `{skill.name}`: {description}")
        lines.extend(
            [
                "",
                "Skill instructions are not loaded yet. When the user's request matches a "
                "skill description, or the user asks you to use/load a skill, you MUST call "
                "the `loadSkill` tool with that skill's name before answering. Do not answer "
                "based only on the description.",
                "Use the `loadSkill` tool with the `filename` parameter to load additional "
                "files referenced by a skill. Skills work together with the other available "
                "tools, such as `RunCli` and the file operation tools.",
            ]
        )
        return "\n".join(lines)

    def load_skill(self, name: str, filename: Optional[str] = None) -> str:
        """返回 SKILL.md 全文，或 Skill 目录内指定文件的文本内容。"""
        skill = self.get(name)
        if skill is None:
            available = ", ".join(self.names()) or "无"
            return f"未找到 Skill：{name}。当前可用的 Skill：{available}"

        if not filename:
            return skill.load_content()

        base = os.path.realpath(skill.path)
        target, outside = self._resolve_exact(base, str(filename))
        if target is None and self._is_bare_filename(str(filename)):
            matches = self._search_by_filename(base, str(filename))
            if len(matches) == 1:
                target = matches[0]
            elif len(matches) > 1:
                candidates = "\n".join(
                    f"- {os.path.relpath(path, base).replace(os.sep, '/')}"
                    for path in matches
                )
                return (
                    f"找到多个名为 {filename!r} 的文件，请通过 filename 指定其一：\n"
                    f"{candidates}"
                )
        if target is None:
            if outside:
                return f"拒绝加载 Skill 目录之外的文件：{filename}"
            return f"文件不存在：{filename}（Skill：{name}）"
        return self._read_text(target, str(filename))

    @staticmethod
    def _is_inside(base: str, path: str) -> bool:
        try:
            return os.path.commonpath(
                [os.path.normcase(base), os.path.normcase(path)]
            ) == os.path.normcase(base)
        except ValueError:
            return False

    def _resolve_exact(self, base: str, filename: str) -> tuple:
        if os.path.isabs(filename) or os.path.splitdrive(filename)[0]:
            candidates = [filename]
        else:
            candidates = [
                os.path.join(base, filename),
                os.path.join(self.project_root, filename),
            ]
        outside = False
        seen = set()
        for candidate in candidates:
            target = os.path.realpath(candidate)
            key = os.path.normcase(target)
            if key in seen:
                continue
            seen.add(key)
            if not os.path.isfile(target):
                continue
            if self._is_inside(base, target):
                return target, outside
            outside = True
        return None, outside

    @staticmethod
    def _is_bare_filename(filename: str) -> bool:
        return (
            not os.path.splitdrive(filename)[0]
            and os.path.basename(filename) == filename
        )

    def _search_by_filename(self, base: str, filename: str) -> List[str]:
        wanted = os.path.normcase(filename)
        matches = []
        for root, _dirs, files in os.walk(base):
            for entry in files:
                if os.path.normcase(entry) != wanted:
                    continue
                target = os.path.realpath(os.path.join(root, entry))
                if self._is_inside(base, target):
                    matches.append(target)
        return sorted(set(matches))

    @staticmethod
    def _read_text(path: str, label: str) -> str:
        if os.path.getsize(path) > MAX_FILE_BYTES:
            return f"文件过大（超过 {MAX_FILE_BYTES} 字节）：{label}"
        with open(path, "rb") as f:
            raw = f.read(MAX_FILE_BYTES + 1)
        try:
            return raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                return raw.decode("gbk")
            except UnicodeDecodeError:
                return f"无法按文本读取文件：{label}"

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def names(self) -> List[str]:
        return list(self._skills)

    def all(self) -> List[Skill]:
        return list(self._skills.values())

    def __len__(self) -> int:
        return len(self._skills)

    def __iter__(self) -> Iterator[Skill]:
        return iter(self._skills.values())
