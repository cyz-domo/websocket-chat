# AGENTS.md

## How to investigate
- Read highest-value sources first:
    - `README*`, root manifests, workspace config, lockfiles
    - Build/test/lint/formatter/typecheck/codegen configs
    - CI workflows and pre-commit/task runner config
    - Existing instruction files (`AGENTS.md`, `CLAUDE.md`, `.cursor/rules/`, `.cursorrules`, `.github/copilot-instructions.md`)
    - Repo-local OpenCode config (`opencode.json`)
- If architecture is unclear, inspect representative code files for entrypoints and execution flow.
- Trust executable sources of truth over prose when conflicts exist.

## What to extract (High Signal Facts)
- Exact developer commands, especially non-obvious ones.
- How to run a single test, package, or focused verification step.
- Required command order (e.g., `lint -> typecheck -> test`).
- Monorepo/multi-package boundaries and ownership of major directories.
- Framework/toolchain quirks (generated code, migrations, build artifacts, special env loading).
- Repo-specific style/workflow conventions deviating from defaults.
- Testing quirks (fixtures, integration prerequisites, snapshot workflows).
- Important constraints from existing instructions.

## Questions
- Only ask if the repo cannot answer something important. Use `question` tool sparingly.
- Good topics: Undocumented team conventions, branch/PR expectations, missing setup prerequisites.

## Writing rules
- Include only high-signal, repo-specific guidance:
    - Exact commands and shortcuts the agent would guess wrong.
    - Architecture notes not obvious from filenames.
    - Conventions differing from language/framework defaults.
    - Setup requirements, environment quirks, and operational gotchas.
    - References to relevant existing instructions.
- Exclude:
    - Generic software advice.
    - Long tutorials or exhaustive file trees.
    - Obvious language conventions.
    - Speculative claims.
    - Content better stored in another file referenced via `opencode.json` `instructions`.

- Prefer short sections and bullets. Summarize structural facts for large repos.
