# Python-Headless-coder

## Product Vision
Headless-coder (the python version)

## Suggested Tech Stack
python

## UI & UX Guidance
Not provided.

## Development and Coding Rules

### Error Handling

- [x] Never use fallback content (dummy code, fabricated data, mock features, or placeholder artifacts).
- [x] Always surface real errors: when a dependency, upstream generation, or test fails, report the actual error message to the PM or developer.
- [x] Provide meaningful error messages that include context about what failed, where, why (if known), and possible next steps.

### Code Quality and Structure

- [x] Keep files small and modular—respect the existing file structure and avoid large monolithic additions.
- [x] Respect styling conventions: follow the repository’s linting, formatting, and naming guidelines consistently.
- [x] Maintain readability: favor clear naming and explicit logic over clever but opaque code.
- [x] Before writing new code, first search for, evaluate, and reuse existing functions, classes, or modules that already fulfill the intended purpose. When necessary, refactor existing components for clarity or modularity instead of starting from scratch.
- [x] Document every module, class, and function with a Google-style docstring placed immediately after the definition. Use triple quotes to cover purpose, Args, Returns, Raises, Examples (when helpful), note side effects, and keep the docstring updated with any code changes.

### Dependencies and Documentation

- [x] No silent imports: when adding a new library, fetch and review its documentation before use.
- [x] Justify each new dependency—prefer standard libraries or existing project dependencies unless there is a strong reason.

### Testing and Logging

- [x] Add unit tests for each component or feature, covering positive, negative, and edge cases when possible.
- [x] Add meaningful logs to aid debugging without creating noise.
- [x] Ensure any command that runs automated tests enforces a maximum execution time of five minutes (e.g., via shell timeout flags).
- [x] Design every change for testability: UI flows should be traversable programmatically, APIs must expose HATEOAS links, and back-end logic should follow isolation, determinism, seam, and small-surface principles.
- [x] Run the linter early and often with strict rules (e.g. max file size 500 LOC); fix all warnings introduced and never rely on `--max-warnings=0` bypasses.

### Data Integrity

- [x] Never fabricate data: do not create fake features, UI/UX mockups, or test data if the real source is missing.
- [x] Fail gracefully: stop and report when inputs are incomplete or generations fail instead of patching with assumptions.

### Version Control

- [x] Do not amend to commits; always create a new commit instead.
- [x] DO NOT PUSH to git without getting permission. When a larger feature is complete, suggest squashing the relevant commits with a clear message before pushing.
