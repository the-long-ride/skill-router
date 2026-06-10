"""Analyze package source links and infer Skills Router manifests.

This layer deliberately treats remote repositories and package metadata as
untrusted evidence. It never executes package code; it only reads bounded
metadata, documentation, and manifest-like files, then produces a conservative
manifest draft for the normal Skills Router review pipeline.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import tarfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from packaging.version import InvalidVersion, Version

from skills_router.config import SkillsRouterConfig


DEFAULT_REF = "main"
MAX_SOURCE_BYTES = 2_000_000
MAX_TEXT_CHARS = 24_000
MAX_DOCUMENTS = 12
MAX_TREE_FILES = 2_000

PROMPT_INJECTION_MARKERS = (
    "ignore previous instructions",
    "ignore all previous instructions",
    "system prompt",
    "developer message",
    "do not follow",
    "jailbreak",
    "act as",
)

GITHUB_DOC_NAMES = {
    "readme",
    "readme.md",
    "skill.md",
    "agents.md",
    "claude.md",
    "guideline.md",
    "copilot-instructions.md",
}
GITHUB_MANIFEST_NAMES = {
    "skills-router.json",
    "skills_router.json",
    "manifest.json",
    "package.json",
    "pyproject.toml",
}


class SourceAnalysisError(ValueError):
    """Raised when a source link cannot be analyzed."""


@dataclass(frozen=True)
class SourceRef:
    """Normalized package source reference."""

    source_type: str
    identifier: str
    url: str
    version: str | None = None
    owner: str | None = None
    repo: str | None = None
    path: str = ""
    direct_file: bool = False


def is_supported_source_ref(value: str) -> bool:
    """Return true when value is an npm/GitHub source reference."""
    try:
        parse_source_ref(value)
        return True
    except SourceAnalysisError:
        return False


def parse_source_ref(value: str) -> SourceRef:
    """Parse npm/GitHub source references from CLI, MCP, or chat input."""
    raw = value.strip()
    if not raw:
        raise SourceAnalysisError("Source reference is required")
    if raw.startswith("github:"):
        return _parse_github_shorthand(raw)
    if raw.startswith("npm:"):
        return _parse_npm_shorthand(raw)

    parsed = urllib.parse.urlsplit(raw)
    host = (parsed.netloc or "").lower()
    if host in {"github.com", "www.github.com"}:
        return _parse_github_url(raw, parsed)
    if host in {"npmjs.com", "www.npmjs.com"}:
        return _parse_npm_url(raw, parsed)

    raise SourceAnalysisError(
        "Unsupported source reference. Use a GitHub URL, npm package URL, "
        "github:owner/repo, or npm:<package>."
    )


class SourceAnalyzer:
    """Collect source evidence and infer a Skills Router manifest."""

    def __init__(self, config: SkillsRouterConfig):
        self.config = config
        self.max_source_bytes = int(getattr(config, "source_max_bytes", MAX_SOURCE_BYTES))
        self.max_text_chars = int(getattr(config, "source_max_text_chars", MAX_TEXT_CHARS))
        self.max_documents = int(getattr(config, "source_max_documents", MAX_DOCUMENTS))

    def analyze(self, source_ref: str) -> dict[str, Any]:
        """Analyze a supported source reference and return evidence + manifest."""
        source = parse_source_ref(source_ref)
        if source.source_type == "github":
            evidence = self._analyze_github(source)
        elif source.source_type == "npm":
            evidence = self._analyze_npm(source)
        else:
            raise SourceAnalysisError(f"Unsupported source type: {source.source_type}")

        manifest = _declared_manifest(evidence) or self._infer_manifest(evidence)
        manifest.setdefault("layer_meta", {})
        manifest["layer_meta"].setdefault("source_analysis", _analysis_meta(evidence))
        confidence = manifest["layer_meta"]["source_analysis"]["confidence"]
        return {
            "status": "OK",
            "source_ref": source_ref,
            "source": evidence["source"],
            "evidence": evidence,
            "manifest": manifest,
            "warnings": evidence.get("warnings", []),
            "human_summary": (
                f"Analyzed {source.identifier}; inferred {manifest['tool_id']} "
                f"with {confidence} confidence."
            ),
        }

    def _analyze_github(self, source: SourceRef) -> dict[str, Any]:
        owner = source.owner or ""
        repo = source.repo or ""
        warnings: list[str] = []
        ref = source.version or self._github_default_branch(owner, repo, warnings)
        evidence = _base_evidence(source, ref)
        evidence["name"] = repo
        evidence["layer_meta"] = {"repository": f"{owner}/{repo}", "ref": ref}

        candidate_paths = self._github_candidate_paths(owner, repo, ref, source, warnings)
        for path in candidate_paths:
            if len(evidence["documents"]) >= self.max_documents:
                break
            try:
                text = self._fetch_github_raw(owner, repo, ref, path)
            except SourceAnalysisError as exc:
                warnings.append(str(exc))
                continue
            _record_text_file(evidence, path, text, self.max_text_chars)
            _merge_file_metadata(evidence, path, text)

        _finalize_evidence(evidence)
        return evidence

    def _analyze_npm(self, source: SourceRef) -> dict[str, Any]:
        package_name = source.identifier
        warnings: list[str] = []
        metadata_url = (
            "https://registry.npmjs.org/"
            + urllib.parse.quote(package_name, safe="")
        )
        metadata = self._fetch_json(metadata_url)
        versions = metadata.get("versions") if isinstance(metadata.get("versions"), dict) else {}
        version = source.version or metadata.get("dist-tags", {}).get("latest")
        package = versions.get(version) if version else None
        if package is None and versions:
            version = sorted(versions)[-1]
            package = versions[version]
            warnings.append(f"Requested npm version not found; using {version}.")
        if package is None:
            raise SourceAnalysisError(f"npm package metadata has no versions: {package_name}")

        evidence = _base_evidence(source, version)
        evidence["name"] = str(package.get("name") or metadata.get("name") or package_name)
        evidence["version"] = str(package.get("version") or version or "")
        evidence["description"] = str(
            package.get("description") or metadata.get("description") or ""
        )
        evidence["keywords"] = _listify(package.get("keywords") or metadata.get("keywords"))
        evidence["package_managers"].append("npm")
        evidence["layer_meta"] = {
            "npm_package": package_name,
            "npm_version": evidence["version"],
            "repository": _repository_url(package.get("repository") or metadata.get("repository")),
        }

        _record_entrypoints(evidence, package.get("bin"), "npm bin")
        _record_entrypoints(evidence, package.get("scripts"), "npm script")
        _record_dependencies(evidence, package)

        readme = metadata.get("readme") or package.get("readme")
        if isinstance(readme, str) and readme.strip():
            _record_text_file(evidence, "README.md", readme, self.max_text_chars)

        tarball = package.get("dist", {}).get("tarball")
        if isinstance(tarball, str) and tarball.startswith(("https://", "http://")):
            try:
                self._inspect_npm_tarball(evidence, tarball)
            except SourceAnalysisError as exc:
                warnings.append(str(exc))

        _finalize_evidence(evidence)
        return evidence

    def _github_default_branch(
        self,
        owner: str,
        repo: str,
        warnings: list[str],
    ) -> str:
        url = f"https://api.github.com/repos/{owner}/{repo}"
        try:
            data = self._fetch_json(url)
        except SourceAnalysisError as exc:
            warnings.append(f"Could not read GitHub default branch: {exc}")
            return DEFAULT_REF
        branch = data.get("default_branch")
        return str(branch) if branch else DEFAULT_REF

    def _github_candidate_paths(
        self,
        owner: str,
        repo: str,
        ref: str,
        source: SourceRef,
        warnings: list[str],
    ) -> list[str]:
        if source.direct_file and source.path:
            return [source.path]

        try:
            tree = self._fetch_json(
                "https://api.github.com/repos/"
                f"{owner}/{repo}/git/trees/"
                f"{urllib.parse.quote(ref, safe='')}?recursive=1"
            )
        except SourceAnalysisError as exc:
            warnings.append(f"Could not read GitHub file tree: {exc}")
            return _fallback_github_paths(source.path)

        raw_items = tree.get("tree") if isinstance(tree.get("tree"), list) else []
        paths = [
            str(item.get("path"))
            for item in raw_items[:MAX_TREE_FILES]
            if item.get("type") == "blob" and item.get("path")
        ]
        return _select_github_paths(paths, source.path, self.max_documents)

    def _fetch_github_raw(self, owner: str, repo: str, ref: str, path: str) -> str:
        quoted_path = urllib.parse.quote(path, safe="/")
        url = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{quoted_path}"
        return self._fetch_text(url)

    def _inspect_npm_tarball(self, evidence: dict[str, Any], tarball_url: str) -> None:
        raw = self._fetch_bytes(tarball_url, self.max_source_bytes)
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
            for member in archive.getmembers()[:MAX_TREE_FILES]:
                if len(evidence["documents"]) >= self.max_documents:
                    break
                if not member.isfile() or member.size > self.max_text_chars:
                    continue
                path = _strip_tar_package_prefix(member.name)
                if not _is_relevant_package_file(path):
                    continue
                handle = archive.extractfile(member)
                if handle is None:
                    continue
                content = handle.read(self.max_text_chars + 1)
                text = content[: self.max_text_chars].decode("utf-8", errors="replace")
                _record_text_file(evidence, path, text, self.max_text_chars)
                _merge_file_metadata(evidence, path, text)

    def _fetch_json(self, url: str) -> dict[str, Any]:
        try:
            data = json.loads(self._fetch_text(url))
        except json.JSONDecodeError as exc:
            raise SourceAnalysisError(f"Remote source returned invalid JSON: {url}") from exc
        if not isinstance(data, dict):
            raise SourceAnalysisError(f"Remote source JSON is not an object: {url}")
        return data

    def _fetch_text(self, url: str) -> str:
        return self._fetch_bytes(url, self.max_source_bytes).decode(
            "utf-8",
            errors="replace",
        )

    def _fetch_bytes(self, url: str, max_bytes: int) -> bytes:
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/json,text/plain,*/*",
                    "User-Agent": "skills-router-source-analyzer",
                },
            )
            with urllib.request.urlopen(
                req,
                timeout=self.config.registry_fetch_timeout_seconds,
            ) as response:
                data = response.read(max_bytes + 1)
        except urllib.error.HTTPError as exc:
            raise SourceAnalysisError(f"HTTP {exc.code} for {url}") from exc
        except urllib.error.URLError as exc:
            raise SourceAnalysisError(f"Could not connect to {url}: {exc.reason}") from exc
        if len(data) > max_bytes:
            raise SourceAnalysisError(f"Remote source exceeds {max_bytes} bytes: {url}")
        return data

    def _infer_manifest(self, evidence: dict[str, Any]) -> dict[str, Any]:
        confidence = _confidence(evidence)
        source = evidence["source"]
        source_type = source["type"]
        name = evidence.get("name") or source.get("identifier") or "Inferred Package"
        version, original_version = _normalize_version(evidence.get("version"))
        text = _combined_text(evidence)
        package_type = _infer_package_type(text, evidence)
        tool_id = _slug(name)
        domain_tags = _domain_tags(text, evidence)
        permissions = _permissions(text, evidence)
        skillsets = _skillsets(evidence, name, text)
        install_source = "github" if source_type == "github" else "third-party"
        description = _description(evidence, name)
        manifest = {
            "tool_id": tool_id,
            "name": str(name),
            "version": version,
            "dependencies": {},
            "layer_1_domain_tags": domain_tags,
            "layer_3_capabilities": {
                "inputs": _inputs(text, evidence),
                "outputs": _outputs(text, evidence),
                "permissions": permissions,
                "extensible": bool(skillsets),
            },
            "layer_4_telemetry": {
                "virtual_env_isolated": False,
                "average_execution_ms": 0,
                "last_known_stable_state_hash": _stable_hash(evidence),
                "health_check_endpoint": "/healthz",
                "last_health_check_passed": None,
            },
            "layer_5_provenance": {
                "publisher_id": _publisher_id(evidence),
                "signature_hash": "",
                "signature_verified": False,
                "trust_score": 0.55 if source_type == "github" else 0.45,
                "trust_factors": {
                    "publisher_known": bool(_publisher_id(evidence)),
                    "open_critical_cves": 0,
                    "last_commit_days_ago": 180,
                    "community_sentiment_score": 0.5,
                    "evidence_file_count": len(evidence.get("files_seen", [])),
                    "inference_confidence": confidence,
                },
                "install_source": install_source,
                "published_at": "",
                "trust_score_last_evaluated": "",
            },
            "layer_6_behavior_spec": {
                "tool_type": package_type,
                "declared_behaviors": [
                    description,
                    "Inferred from public package metadata and documentation.",
                    "Skills Router analysis did not execute package code.",
                ],
                "known_nondeterminism": (
                    "Behavior is inferred and may be incomplete until human review."
                ),
                "behavioral_embedding": [],
                "embedding_confidence": f"inferred-{confidence}",
                "spec_superseded_by": None,
                "tested_input_output_pairs": [],
            },
            "agent_package": {
                "type": package_type,
                "skillsets": skillsets,
            },
            "layer_meta": {
                "dependent_workflows": [],
                "install_scope": "global",
                "agent_id": None,
                "installed_at": "",
                "version_pin_strategy": "minor",
                "inferred_from_source": True,
                "source_analysis": {
                    **_analysis_meta(evidence),
                    "confidence": confidence,
                    "original_version": original_version,
                },
            },
        }
        return manifest


def _parse_github_shorthand(value: str) -> SourceRef:
    match = re.match(
        r"^github:(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)"
        r"(?:@(?P<ref>[A-Za-z0-9_./-]+))?$",
        value,
    )
    if not match:
        raise SourceAnalysisError("Invalid GitHub source. Use github:<owner>/<repo>.")
    owner = match.group("owner")
    repo = _clean_repo_name(match.group("repo"))
    ref = match.group("ref")
    return SourceRef(
        source_type="github",
        identifier=f"{owner}/{repo}",
        url=f"https://github.com/{owner}/{repo}",
        version=ref,
        owner=owner,
        repo=repo,
    )


def _parse_github_url(raw: str, parsed: urllib.parse.SplitResult) -> SourceRef:
    parts = [urllib.parse.unquote(part) for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        raise SourceAnalysisError("GitHub URL must include owner and repo")
    owner = parts[0]
    repo = _clean_repo_name(parts[1])
    ref = None
    path = ""
    direct_file = False
    if len(parts) >= 4 and parts[2] in {"tree", "blob"}:
        ref = parts[3]
        path = "/".join(parts[4:])
        direct_file = parts[2] == "blob"
    return SourceRef(
        source_type="github",
        identifier=f"{owner}/{repo}",
        url=raw,
        version=ref,
        owner=owner,
        repo=repo,
        path=path,
        direct_file=direct_file,
    )


def _parse_npm_url(raw: str, parsed: urllib.parse.SplitResult) -> SourceRef:
    parts = [urllib.parse.unquote(part) for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2 or parts[0] != "package":
        raise SourceAnalysisError("npm URL must look like https://www.npmjs.com/package/<pkg>")
    if parts[1].startswith("@") and len(parts) >= 3:
        name = f"{parts[1]}/{parts[2]}"
    else:
        name = parts[1]
    return SourceRef(source_type="npm", identifier=name, url=raw)


def _parse_npm_shorthand(value: str) -> SourceRef:
    spec = value[len("npm:") :].strip()
    if not spec:
        raise SourceAnalysisError("npm source requires a package name")
    name, version = _split_npm_spec(spec)
    return SourceRef(
        source_type="npm",
        identifier=name,
        url=f"https://www.npmjs.com/package/{name}",
        version=version,
    )


def _split_npm_spec(spec: str) -> tuple[str, str | None]:
    if spec.startswith("@"):
        slash = spec.find("/")
        if slash < 0:
            raise SourceAnalysisError("Scoped npm package must look like @scope/name")
        version_at = spec.find("@", slash + 1)
    else:
        version_at = spec.rfind("@")
    if version_at > 0:
        return spec[:version_at], spec[version_at + 1 :]
    return spec, None


def _clean_repo_name(repo: str) -> str:
    return repo[:-4] if repo.endswith(".git") else repo


def _base_evidence(source: SourceRef, resolved_version: str | None) -> dict[str, Any]:
    return {
        "source": {
            "type": source.source_type,
            "identifier": source.identifier,
            "url": source.url,
            "requested_version": source.version,
            "resolved_version": resolved_version,
            "path": source.path,
        },
        "name": source.identifier,
        "version": resolved_version or "",
        "description": "",
        "keywords": [],
        "package_managers": [],
        "candidate_entrypoints": [],
        "dependencies": {},
        "files_seen": [],
        "documents": [],
        "declared_manifest": None,
        "warnings": [],
        "layer_meta": {},
    }


def _record_text_file(
    evidence: dict[str, Any],
    path: str,
    text: str,
    max_text_chars: int,
) -> None:
    clipped = text[:max_text_chars]
    evidence["files_seen"].append({
        "path": path,
        "size": len(text.encode("utf-8", errors="ignore")),
        "truncated": len(text) > len(clipped),
    })
    if _is_document(path):
        evidence["documents"].append({
            "path": path,
            "kind": _document_kind(path),
            "text": _clean_text(clipped),
        })
    lower = clipped.lower()
    if any(marker in lower for marker in PROMPT_INJECTION_MARKERS):
        evidence["warnings"].append(
            f"Potential prompt-injection language found in {path}; treated as evidence only."
        )


def _merge_file_metadata(evidence: dict[str, Any], path: str, text: str) -> None:
    lower = path.lower()
    if lower.endswith("package.json"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return
        if not isinstance(data, dict):
            return
        evidence["package_managers"].append("npm")
        evidence["name"] = str(data.get("name") or evidence.get("name"))
        evidence["version"] = str(data.get("version") or evidence.get("version") or "")
        evidence["description"] = str(
            data.get("description") or evidence.get("description") or ""
        )
        evidence["keywords"].extend(_listify(data.get("keywords")))
        _record_entrypoints(evidence, data.get("bin"), "npm bin")
        _record_entrypoints(evidence, data.get("scripts"), "npm script")
        _record_dependencies(evidence, data)
    elif lower.endswith("pyproject.toml"):
        evidence["package_managers"].append("python")
        parsed = _parse_simple_pyproject(text)
        evidence["name"] = parsed.get("name") or evidence.get("name")
        evidence["version"] = parsed.get("version") or evidence.get("version")
        evidence["description"] = parsed.get("description") or evidence.get("description")
        evidence["keywords"].extend(parsed.get("keywords", []))
        for script in parsed.get("scripts", []):
            evidence["candidate_entrypoints"].append({
                "kind": "python script",
                "name": script,
            })
    elif _manifest_name(path):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return
        if isinstance(data, dict) and {"tool_id", "name", "version"}.issubset(data):
            evidence["declared_manifest"] = data


def _record_entrypoints(evidence: dict[str, Any], value: Any, kind: str) -> None:
    if isinstance(value, str):
        evidence["candidate_entrypoints"].append({"kind": kind, "name": value})
    elif isinstance(value, dict):
        for name in sorted(value)[:20]:
            evidence["candidate_entrypoints"].append({"kind": kind, "name": str(name)})


def _record_dependencies(evidence: dict[str, Any], package: dict[str, Any]) -> None:
    for key in ("dependencies", "peerDependencies", "optionalDependencies"):
        deps = package.get(key)
        if isinstance(deps, dict):
            evidence["dependencies"].setdefault(key, [])
            evidence["dependencies"][key].extend(sorted(str(name) for name in deps)[:50])


def _finalize_evidence(evidence: dict[str, Any]) -> None:
    evidence["keywords"] = _dedupe([str(item) for item in evidence.get("keywords", []) if item])
    evidence["package_managers"] = _dedupe(evidence.get("package_managers", []))
    evidence["candidate_entrypoints"] = _dedupe_dicts(
        evidence.get("candidate_entrypoints", []),
        ("kind", "name"),
    )
    evidence["warnings"] = _dedupe(evidence.get("warnings", []))


def _select_github_paths(paths: list[str], base_path: str, limit: int) -> list[str]:
    selected: list[str] = []
    base = base_path.strip("/")
    for path in sorted(paths, key=_path_priority):
        if base and not (path == base or path.startswith(base + "/")):
            continue
        if _is_relevant_package_file(path):
            selected.append(path)
        if len(selected) >= limit:
            break
    if selected:
        return selected
    return _fallback_github_paths(base_path)


def _fallback_github_paths(base_path: str) -> list[str]:
    base = base_path.strip("/")
    prefix = f"{base}/" if base else ""
    return [
        f"{prefix}skills-router.json",
        f"{prefix}skills_router.json",
        f"{prefix}manifest.json",
        f"{prefix}README.md",
        f"{prefix}SKILL.md",
        f"{prefix}package.json",
        f"{prefix}pyproject.toml",
        "README.md",
        "package.json",
        "pyproject.toml",
    ]


def _path_priority(path: str) -> tuple[int, int, str]:
    lower = path.lower()
    name = lower.rsplit("/", 1)[-1]
    if name in {"skills-router.json", "skills_router.json"}:
        group = 0
    elif name in {"manifest.json", "package.json", "pyproject.toml"}:
        group = 1
    elif name == "skill.md" or lower.endswith("/skill.md"):
        group = 2
    elif name.startswith("readme"):
        group = 3
    elif name in {"agents.md", "claude.md", "guideline.md", "copilot-instructions.md"}:
        group = 4
    else:
        group = 9
    return (group, path.count("/"), lower)


def _is_relevant_package_file(path: str) -> bool:
    lower = path.lower()
    name = lower.rsplit("/", 1)[-1]
    return (
        name in GITHUB_DOC_NAMES
        or name in GITHUB_MANIFEST_NAMES
        or lower.endswith("/skill.md")
        or lower.endswith("/skills-router.json")
        or lower.endswith("/skills_router.json")
        or lower.endswith("/.codex/skills")
        or "/.codex/skills/" in lower
        or "/.claude/commands/" in lower
        or "/prompts/" in lower and lower.endswith(".md")
    )


def _strip_tar_package_prefix(path: str) -> str:
    parts = path.replace("\\", "/").split("/")
    if parts and parts[0] == "package":
        parts = parts[1:]
    return "/".join(part for part in parts if part and part != "..")


def _is_document(path: str) -> bool:
    lower = path.lower()
    return lower.endswith((".md", ".txt", ".json", ".toml"))


def _document_kind(path: str) -> str:
    lower = path.lower()
    if lower.endswith("skill.md"):
        return "skill"
    if lower.endswith("package.json"):
        return "package-json"
    if lower.endswith("pyproject.toml"):
        return "pyproject"
    if _manifest_name(path):
        return "manifest"
    return "doc"


def _manifest_name(path: str) -> bool:
    name = path.lower().rsplit("/", 1)[-1]
    return name in {"skills-router.json", "skills_router.json", "manifest.json"}


def _declared_manifest(evidence: dict[str, Any]) -> dict[str, Any] | None:
    manifest = evidence.get("declared_manifest")
    if not isinstance(manifest, dict):
        return None
    result = dict(manifest)
    result.setdefault("layer_meta", {})
    result["layer_meta"].setdefault("inferred_from_source", False)
    return result


def _analysis_meta(evidence: dict[str, Any]) -> dict[str, Any]:
    source = evidence.get("source", {})
    return {
        "source_type": source.get("type"),
        "source_identifier": source.get("identifier"),
        "source_url": source.get("url"),
        "resolved_version": source.get("resolved_version"),
        "evidence_files": [
            item.get("path") for item in evidence.get("files_seen", [])[:20]
        ],
        "warnings": evidence.get("warnings", []),
        "confidence": _confidence(evidence),
        "inference_engine": "skills-router-deterministic-v1",
        "prompt_injection_guard": (
            "Remote docs are treated only as untrusted evidence, never instructions."
        ),
    }


def _confidence(evidence: dict[str, Any]) -> str:
    score = 0
    if evidence.get("description"):
        score += 1
    if evidence.get("keywords"):
        score += 1
    if evidence.get("documents"):
        score += 1
    if evidence.get("candidate_entrypoints"):
        score += 1
    if any(doc.get("kind") == "skill" for doc in evidence.get("documents", [])):
        score += 2
    if evidence.get("declared_manifest"):
        score += 3
    if score >= 5:
        return "high"
    if score >= 2:
        return "medium"
    return "low"


def _normalize_version(value: Any) -> tuple[str, str]:
    original = str(value or "").strip()
    if not original:
        return "0.0.0", ""
    try:
        return str(Version(original)), original
    except InvalidVersion:
        cleaned = re.sub(r"[^0-9A-Za-z.]+", ".", original).strip(".")
        try:
            return str(Version(cleaned)), original
        except InvalidVersion:
            return "0.0.0", original


def _combined_text(evidence: dict[str, Any]) -> str:
    parts = [
        str(evidence.get("name", "")),
        str(evidence.get("description", "")),
        " ".join(evidence.get("keywords", [])),
    ]
    for doc in evidence.get("documents", [])[:6]:
        parts.append(str(doc.get("text", ""))[:4000])
    return "\n".join(parts).lower()


def _description(evidence: dict[str, Any], name: str) -> str:
    description = str(evidence.get("description") or "").strip()
    if description:
        return description[:240]
    for doc in evidence.get("documents", []):
        first = _first_meaningful_line(str(doc.get("text", "")))
        if first:
            return first[:240]
    return f"Inferred capabilities for {name}."


def _infer_package_type(text: str, evidence: dict[str, Any]) -> str:
    if "plugin" in text:
        return "plugin"
    if (
        "skill" in text
        or "agent" in text
        or any(doc.get("kind") == "skill" for doc in evidence.get("documents", []))
    ):
        return "skillset"
    return "tool"


def _domain_tags(text: str, evidence: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    source_type = evidence.get("source", {}).get("type")
    if source_type == "npm" or "npm" in evidence.get("package_managers", []):
        tags.append("JavaScript")
    if "python" in evidence.get("package_managers", []):
        tags.append("Python")
    for needle, tag in (
        ("mcp", "MCP"),
        ("agent", "AI Agent"),
        ("skill", "AI Agent Skill"),
        ("prompt", "Prompting"),
        ("cli", "CLI"),
        ("command", "CLI"),
        ("browser", "Browser"),
        ("web", "Web"),
        ("api", "API"),
        ("github", "GitHub"),
    ):
        if needle in text:
            tags.append(tag)
    if not tags:
        tags.append("AI Agent")
    return _dedupe(tags)[:8]


def _permissions(text: str, evidence: dict[str, Any]) -> list[str]:
    permissions: list[str] = []
    if any(word in text for word in ("http", "api", "network", "fetch", "request")):
        permissions.append("network: outbound_https")
    if any(word in text for word in ("file", "workspace", "readme", "path")):
        permissions.append("filesystem: read_workspace")
    if evidence.get("candidate_entrypoints"):
        permissions.append("process: execute_cli")
    if "browser" in text:
        permissions.append("browser: optional")
    return _dedupe(permissions)


def _inputs(text: str, evidence: dict[str, Any]) -> list[str]:
    inputs = ["task: string"]
    if "mcp" in text:
        inputs.append("mcp request: json")
    if evidence.get("candidate_entrypoints"):
        inputs.append("command arguments: string")
    return _dedupe(inputs)


def _outputs(text: str, evidence: dict[str, Any]) -> list[str]:
    outputs = ["agent capability result: text"]
    if "json" in text or "mcp" in text:
        outputs.append("structured result: json")
    if evidence.get("candidate_entrypoints"):
        outputs.append("cli output: text")
    return _dedupe(outputs)


def _skillsets(evidence: dict[str, Any], name: str, text: str) -> list[dict[str, Any]]:
    skills: list[dict[str, Any]] = []
    permissions = _permissions(text, evidence)
    for doc in evidence.get("documents", []):
        if doc.get("kind") != "skill":
            continue
        skill_name = _title_from_path(str(doc.get("path", ""))) or name
        use_when = _first_meaningful_line(str(doc.get("text", ""))) or _description(
            evidence,
            name,
        )
        skills.append({
            "id": _slug(skill_name),
            "name": skill_name,
            "description": use_when[:240],
            "use_when": use_when[:240],
            "permissions": permissions,
        })
    if evidence.get("candidate_entrypoints"):
        skills.append({
            "id": "cli",
            "name": f"{name} CLI",
            "description": _description(evidence, name),
            "use_when": _description(evidence, name),
            "permissions": permissions,
        })
    if not skills:
        skills.append({
            "id": "default",
            "name": str(name),
            "description": _description(evidence, name),
            "use_when": _description(evidence, name),
            "permissions": permissions,
        })
    return _dedupe_dicts(skills, ("id",))[:5]


def _publisher_id(evidence: dict[str, Any]) -> str:
    source = evidence.get("source", {})
    if source.get("type") == "github":
        identifier = str(source.get("identifier", ""))
        return identifier.split("/", 1)[0]
    if source.get("type") == "npm":
        identifier = str(source.get("identifier", ""))
        if identifier.startswith("@"):
            return identifier.split("/", 1)[0].lstrip("@")
    return ""


def _stable_hash(evidence: dict[str, Any]) -> str:
    payload = json.dumps(
        {
            "source": evidence.get("source"),
            "files": evidence.get("files_seen"),
            "description": evidence.get("description"),
            "keywords": evidence.get("keywords"),
        },
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _parse_simple_pyproject(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {"keywords": [], "scripts": []}
    for field in ("name", "version", "description"):
        match = re.search(rf"(?m)^\s*{field}\s*=\s*['\"]([^'\"]+)['\"]", text)
        if match:
            result[field] = match.group(1)
    keywords = re.search(r"(?ms)^\s*keywords\s*=\s*\[(.*?)\]", text)
    if keywords:
        result["keywords"] = re.findall(r"['\"]([^'\"]+)['\"]", keywords.group(1))
    scripts = re.search(r"(?ms)^\s*\[project\.scripts\]\s*(.*?)(?:^\s*\[|\Z)", text)
    if scripts:
        result["scripts"] = re.findall(r"(?m)^\s*([A-Za-z0-9_.-]+)\s*=", scripts.group(1))
    return result


def _repository_url(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("url") or "")
    return ""


def _first_meaningful_line(text: str) -> str:
    for raw_line in text.splitlines():
        line = raw_line.strip().strip("#").strip()
        if not line or line.startswith("<!--"):
            continue
        return line
    return ""


def _title_from_path(path: str) -> str:
    parts = [part for part in path.replace("\\", "/").split("/") if part]
    if len(parts) >= 2 and parts[-1].lower() == "skill.md":
        return parts[-2].replace("-", " ").replace("_", " ").title()
    return ""


def _clean_text(text: str) -> str:
    return "".join(ch if ch == "\n" or ch == "\t" or ord(ch) >= 32 else " " for ch in text)


def _slug(value: Any) -> str:
    text = str(value or "inferred-tool").lower()
    text = text.replace("@", "").replace("/", "-")
    chars = []
    prev_dash = False
    for char in text:
        if char.isalnum():
            chars.append(char)
            prev_dash = False
        elif not prev_dash:
            chars.append("-")
            prev_dash = True
    slug = "".join(chars).strip("-")
    if len(slug) < 2:
        slug = f"{slug or 'tool'}-tool"
    return slug[:128].strip("-")


def _listify(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item)]
    return [str(value)]


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = str(value).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _dedupe_dicts(values: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    result: list[dict[str, Any]] = []
    for value in values:
        marker = tuple(value.get(key) for key in keys)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(value)
    return result
