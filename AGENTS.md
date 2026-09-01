# Guidelines for AI Coding Agents

When making changes to this codebase, always follow these rules and quality checks before concluding your turn:

## 1. Code Formatting & Linting
Run `hatch fmt` (or `hatch check code --fix` and `hatch check fmt --fix`) to ensure all code complies with Ruff formatting and lint rules:
```bash
hatch fmt
```
Fix any reported lint errors or warnings.

## 2. Type Checking
Run type checks using Hatch and ensure there are no errors:
```bash
hatch run types:check
```
- Ensure type annotations are used for all public functions and signatures.
- Type-only imports (e.g. from `collections.abc` or `typing`) should be placed inside `if TYPE_CHECKING:` blocks when flagged by linter/type-checker.

## 3. Unit Tests
Run the test suite and verify that all tests pass:
```bash
hatch test
```
- Write tests using `pytest` style functions or `unittest.TestCase`, standard `assert` statements, and fixtures (such as `tmp_path` / `tempfile`).
- Always add or update tests when adding features or fixing bugs.
- Ensure everything is fixed and passing before suggesting that your task is completed.
