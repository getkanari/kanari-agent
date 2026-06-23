# Kanari Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename every `doorman*` reference to `kanari*` across the kanari-agent Python package — module name, CLI command, PyPI package, env vars, config path, public symbols, and documentation.

**Architecture:** Four ordered tasks. Task 1 renames the module directory and fixes all imports so the package installs and tests pass. Task 2 renames symbols, constants, env vars, and user-visible strings. Task 3 updates docs. Task 4 renames the local project directory (last, after all commits). Each task ends with a passing test suite.

**Tech Stack:** Python 3.9+, Poetry, pytest, BSD sed (macOS `sed -i ''`), git mv.

## Global Constraints

- Python ≥ 3.9; all type hints preserved
- All 276 tests must pass after each task
- `pre-commit run --all-files` must pass before each commit
- No backward compatibility layer — clean rename
- Use `sed -i ''` (macOS BSD sed), not `sed -i`
- Working directory for all commands: `/Users/herchila/Projects/kanari-agent`
- Branch: `feat/kanari-rename`

---

## Task 1: Module directory rename + import paths

**Files:**
- Rename: `src/kanari_agent/` → `src/kanari_agent/` (entire directory via `git mv`)
- Modify: `pyproject.toml` — package name, entry points, packages list, bandit target, repository URL
- Modify: all `*.py` files — replace `kanari_agent` module paths in imports and `patch()` calls

**Interfaces:**
- Produces: installable `kanari-agent` package with `kanari` and `kanari-agent` CLI entry points; all `from kanari_agent.X import Y` imports work; 276 tests pass

- [ ] **Step 1: Rename the module directory**

```bash
git mv src/kanari_agent src/kanari_agent
```

Verify it moved:
```bash
ls src/kanari_agent/
```
Expected: `__init__.py agent.py api_client.py audit.py cli.py collector.py config.py findings.py logger.py login.py models.py stamps.py`

- [ ] **Step 2: Update pyproject.toml**

Apply all these changes manually (not with sed — the file has complex structure):

```toml
[tool.poetry]
name = "kanari-agent"                           # was: kanari-agent
repository = "https://github.com/getkanari/kanari-agent"  # was: herchila/kanari-agent
packages = [{include = "kanari_agent", from = "src"}]     # was: kanari_agent

[tool.poetry.scripts]
kanari-agent = "kanari_agent.cli:main"          # was: kanari-agent = "kanari_agent.cli:main"
kanari = "kanari_agent.cli:main"                # was: doorman = "kanari_agent.cli:main"

[tool.bandit]
targets = ["src/kanari_agent"]                  # was: src/kanari_agent
```

- [ ] **Step 3: Update all import statements in source and test files**

```bash
find src tests -name "*.py" -exec sed -i '' \
  -e 's/from kanari_agent\./from kanari_agent\./g' \
  -e 's/import kanari_agent\b/import kanari_agent/g' \
  {} \;
```

- [ ] **Step 4: Update `patch()` paths in test files**

```bash
find tests -name "*.py" -exec sed -i '' \
  -e 's/patch("kanari_agent\./patch("kanari_agent\./g' \
  -e "s/patch('kanari_agent\./patch('kanari_agent\./g" \
  {} \;
```

- [ ] **Step 5: Update `version()` call in `__init__.py` and `config.py`**

```bash
sed -i '' 's/version("kanari-agent")/version("kanari-agent")/g' \
  src/kanari_agent/__init__.py \
  src/kanari_agent/config.py
```

- [ ] **Step 6: Install the renamed package**

```bash
poetry install
```

Expected: installs without error; `kanari --help` and `kanari-agent --help` show the CLI.

- [ ] **Step 7: Run tests**

```bash
poetry run pytest -v
```

Expected: 276 passed. If any fail they will be import errors — fix the remaining `kanari_agent` reference that the sed missed, then re-run.

- [ ] **Step 8: Run pre-commit**

```bash
pre-commit run --all-files
```

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat: rename module from kanari_agent to kanari_agent"
```

---

## Task 2: Symbol, constant, env var, path, and string renames

**Files:**
- Modify: all files in `src/kanari_agent/` — class names, constant names, string values, env var names, config paths, user-visible CLI strings
- Modify: all files in `tests/` — update references to renamed symbols

**Interfaces:**
- Consumes: `src/kanari_agent/` from Task 1 (module already installed as `kanari_agent`)
- Produces: `KanariAgent`, `KanariStampPlugin`, `KANARI_*` env vars, `~/.kanari/config`, `kanari login` user-visible strings; 276 tests pass

- [ ] **Step 1: Rename classes and internal functions in source files**

```bash
find src/kanari_agent -name "*.py" -exec sed -i '' \
  -e 's/KanariStampPlugin/KanariStampPlugin/g' \
  -e 's/KanariAgent/KanariAgent/g' \
  -e 's/add_doorman_stamp/add_kanari_stamp/g' \
  {} \;
```

- [ ] **Step 2: Rename constants and string values in source files**

```bash
find src/kanari_agent -name "*.py" -exec sed -i '' \
  -e 's/KANARI_TS_HEADER/KANARI_TS_HEADER/g' \
  -e 's/doorman_sent_ts/kanari_sent_ts/g' \
  -e 's/KANARI_CONFIG_PATH/KANARI_CONFIG_PATH/g' \
  -e 's/load_doorman_config/load_kanari_config/g' \
  -e 's/save_doorman_config/save_kanari_config/g' \
  -e 's/doorman_cfg/kanari_cfg/g' \
  {} \;
```

- [ ] **Step 3: Rename env vars in source files**

```bash
find src/kanari_agent -name "*.py" -exec sed -i '' \
  -e 's/KANARI_SANITIZE_TASK_SIGNATURES/KANARI_SANITIZE_TASK_SIGNATURES/g' \
  -e 's/KANARI_LOCAL_MODE/KANARI_LOCAL_MODE/g' \
  -e 's/KANARI_API_KEY/KANARI_API_KEY/g' \
  -e 's/KANARI_API_URL/KANARI_API_URL/g' \
  {} \;
```

- [ ] **Step 4: Update config path and API URL in source files**

```bash
find src/kanari_agent -name "*.py" -exec sed -i '' \
  -e 's|\.doorman.*/ "config"|.kanari" / "config"|g' \
  {} \;
```

Then open `src/kanari_agent/login.py` and manually verify line 16 reads:
```python
KANARI_CONFIG_PATH = Path.home() / ".kanari" / "config"
```

Also update the default API URL in `src/kanari_agent/config.py`. Find the line with `api.getkanari.com` and change it to:
```python
"api_url": yaml_config.get("api_url", "https://api.getkanari.com"),
```

- [ ] **Step 5: Rename user-visible CLI strings in source files**

```bash
find src/kanari_agent -name "*.py" -exec sed -i '' \
  -e 's/kanari upgrade/kanari upgrade/g' \
  -e 's/kanari alerts/kanari alerts/g' \
  -e 's/kanari login/kanari login/g' \
  -e 's/kanari agent/kanari agent/g' \
  -e 's/kanari audit/kanari audit/g' \
  -e 's/kanari watch/kanari watch/g' \
  {} \;
```

- [ ] **Step 6: Rename remaining `doorman` strings in source files**

```bash
find src/kanari_agent -name "*.py" -exec sed -i '' \
  -e 's/kanari-agent\//kanari-agent\//g' \
  -e 's/kanari-agent {/kanari-agent {/g' \
  -e 's/"kanari-agent"/"kanari-agent"/g' \
  -e "s/'kanari-agent'/'kanari-agent'/g" \
  -e 's/Kanari Agent/Kanari Agent/g' \
  -e 's/Kanari Stamps/Kanari Stamps/g' \
  -e 's/Kanari timestamp/Kanari timestamp/g' \
  -e 's/doorman_sent_ts/kanari_sent_ts/g' \
  {} \;
```

- [ ] **Step 7: Update test files to match renamed symbols**

```bash
find tests -name "*.py" -exec sed -i '' \
  -e 's/KanariAgent/KanariAgent/g' \
  -e 's/KANARI_TS_HEADER/KANARI_TS_HEADER/g' \
  -e 's/KANARI_API_KEY/KANARI_API_KEY/g' \
  -e 's/KANARI_API_URL/KANARI_API_URL/g' \
  -e 's/KANARI_LOCAL_MODE/KANARI_LOCAL_MODE/g' \
  -e 's/KANARI_SANITIZE_TASK_SIGNATURES/KANARI_SANITIZE_TASK_SIGNATURES/g' \
  -e 's/load_doorman_config/load_kanari_config/g' \
  -e 's/doorman_cfg/kanari_cfg/g' \
  -e 's/api\.doorman\.com/api.getkanari.com/g' \
  -e 's/doorman\.com/getkanari.com/g' \
  {} \;
```

- [ ] **Step 8: Check for any remaining doorman references in source and tests**

```bash
grep -rn "doorman" src/ tests/ --include="*.py" | grep -v "\.pyc"
```

For any remaining hits: fix them manually. Common survivors:
- Prose in docstrings: `"""The Kanari Agent..."""` → `"""The Kanari Agent..."""`
- Any comment mentioning `doorman` not caught by the sed patterns above

- [ ] **Step 9: Run tests**

```bash
poetry run pytest -v
```

Expected: 276 passed.

- [ ] **Step 10: Run pre-commit**

```bash
pre-commit run --all-files
```

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "feat: rename all doorman symbols, env vars, and strings to kanari"
```

---

## Task 3: Documentation

**Files:**
- Modify: `README.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `CLAUDE.md`, `ASSISTANT.md`
- Modify: `config.example.yaml`

**Interfaces:**
- Produces: all documentation refers to `kanari`, `kanari-agent`, `KanariAgent`, `KANARI_*`, `~/.kanari/config`

- [ ] **Step 1: Batch rename in Markdown and YAML files**

```bash
find . -name "*.md" -o -name "*.yaml" -o -name "*.yml" | \
  grep -v ".git/" | grep -v "__pycache__" | \
  xargs sed -i '' \
  -e 's/kanari-agent/kanari-agent/g' \
  -e 's/kanari_agent/kanari_agent/g' \
  -e 's/KanariAgent/KanariAgent/g' \
  -e 's/KanariStampPlugin/KanariStampPlugin/g' \
  -e 's/KANARI_API_KEY/KANARI_API_KEY/g' \
  -e 's/KANARI_API_URL/KANARI_API_URL/g' \
  -e 's/KANARI_LOCAL_MODE/KANARI_LOCAL_MODE/g' \
  -e 's/KANARI_SANITIZE_TASK_SIGNATURES/KANARI_SANITIZE_TASK_SIGNATURES/g' \
  -e 's/KANARI_/KANARI_/g' \
  -e 's|\.kanari/|.kanari/|g' \
  -e 's/kanari login/kanari login/g' \
  -e 's/kanari agent/kanari agent/g' \
  -e 's/kanari audit/kanari audit/g' \
  -e 's/kanari watch/kanari watch/g' \
  -e 's/kanari upgrade/kanari upgrade/g' \
  -e 's/kanari alerts/kanari alerts/g' \
  -e 's/Kanari Agent/Kanari Agent/g' \
  -e 's/Kanari /Kanari /g' \
  -e 's/api\.doorman\.com/api.getkanari.com/g'
```

- [ ] **Step 2: Check for remaining doorman references**

```bash
grep -rn "doorman" . --include="*.md" --include="*.yaml" --include="*.yml" | grep -v ".git/"
```

Fix any remaining hits manually — they'll be edge cases the sed didn't catch (e.g., `doorman` mid-word, brand mentions).

- [ ] **Step 3: Run pre-commit**

```bash
pre-commit run --all-files
```

- [ ] **Step 4: Run tests one final time**

```bash
poetry run pytest -v
```

Expected: 276 passed.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "docs: rename all doorman references to kanari"
```

---

## Task 4: Local directory rename

**This is a filesystem operation, not a git operation. Do it after all commits are made.**

**Interfaces:**
- Consumes: clean git state on `feat/kanari-rename` (no uncommitted changes)
- Produces: project directory at `/Users/herchila/Projects/kanari-agent`

⚠️ **Warning:** After this step, any open terminal sessions or IDE windows pointing to the old path will break. Claude Code sessions also lose their working directory. This is expected.

- [ ] **Step 1: Verify clean git state**

```bash
git status
```

Expected: `nothing to commit, working tree clean`

- [ ] **Step 2: Rename the directory**

```bash
mv /Users/herchila/Projects/kanari-agent /Users/herchila/Projects/kanari-agent
```

- [ ] **Step 3: Verify git still works from the new path**

```bash
cd /Users/herchila/Projects/kanari-agent && git status && git log --oneline -3
```

Expected: git works normally; the new path has the full history.

- [ ] **Step 4: Update the remote URL if needed**

```bash
git remote get-url origin
```

If it still shows `kanari-agent`, update:
```bash
git remote set-url origin git@github.com:getkanari/kanari-agent.git
```

(The remote repo on GitHub is already named `kanari-agent`.)

---

## Self-Review

**Spec coverage:**
- ✅ Module directory `src/kanari_agent/` → `src/kanari_agent/` (Task 1)
- ✅ PyPI package name `kanari-agent` → `kanari-agent` (Task 1)
- ✅ Entry points `doorman` → `kanari`, `kanari-agent` → `kanari-agent` (Task 1)
- ✅ `KanariAgent` → `KanariAgent` (Task 2)
- ✅ `KanariStampPlugin` → `KanariStampPlugin` (Task 2)
- ✅ `KANARI_TS_HEADER` → `KANARI_TS_HEADER`, `"doorman_sent_ts"` → `"kanari_sent_ts"` (Task 2)
- ✅ `KANARI_CONFIG_PATH` → `KANARI_CONFIG_PATH`, `~/.kanari/config` → `~/.kanari/config` (Task 2)
- ✅ `load_doorman_config` → `load_kanari_config`, `save_doorman_config` → `save_kanari_config` (Task 2)
- ✅ `KANARI_API_KEY`, `KANARI_API_URL`, `KANARI_LOCAL_MODE`, `KANARI_SANITIZE_TASK_SIGNATURES` → `KANARI_*` (Task 2)
- ✅ User-visible strings `"kanari login"` etc. → `"kanari login"` etc. (Task 2)
- ✅ User-Agent and logger name `kanari-agent` → `kanari-agent` (Task 2)
- ✅ Test patch paths (Task 1 + Task 2)
- ✅ Documentation (Task 3)
- ✅ Local directory rename (Task 4)
- ✅ No backward compatibility (constraint met — clean rename throughout)
