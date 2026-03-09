# Copilot Instructions

## Project goal
Build and maintain a small local API that exposes public social profile stats for home automation devices.

## Constraints
- No paid API and no API key required.
- Only public data is allowed.
- If a profile/repository is private or unavailable, return a clear error payload.
- Keep dependencies lightweight and startup fast.

## Coding style
- Prefer clear, explicit Python code over abstractions.
- Keep scraping logic isolated per platform client.
- Include source metadata in responses to show where values came from.
- Keep backwards compatibility for existing endpoint paths.

## Configuration
- Runtime config comes from `config.yaml` at repository root.
- `config-example.yaml` must stay synchronized when config shape changes.
- Never commit real `config.yaml`.
