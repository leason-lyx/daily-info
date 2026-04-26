# AGENTS.md

Development rules for agents working in this repository.

## Development Rules

- After every functional code change, agents must actually run the frontend and test the affected behavior in the browser before handing off. Static checks or builds alone are not sufficient.
- Acceptance testing must use Docker Compose to build/start the app services, so validation covers the same runtime path used by the local deployment.
- The user is not deeply familiar with this project's tech stack. If agents see a better implementation path, they should propose it and explain the reasoning instead of silently following a weaker approach.
