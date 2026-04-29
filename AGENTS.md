# AGENTS.md

Development rules for agents working in this repository.

## Development Rules

- After every functional code change, agents must actually run the frontend and test the affected behavior in the browser before handing off. Static checks or builds alone are not sufficient.
- Acceptance testing must use Docker Compose to build/start the app services, so validation covers the same runtime path used by the local deployment.
- When submitting code changes, agents should default to creating a branch, pushing it, and opening a pull request instead of pushing directly to the default branch, unless the user explicitly asks for a direct push.
- The user is not deeply familiar with this project's tech stack. If agents see a better implementation path, they should propose it and explain the reasoning instead of silently following a weaker approach.
