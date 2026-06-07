# Claude Code Bot Integration

This repository is configured to run the Claude Code bot via GitHub Actions.

## Usage

You can trigger the Claude Code bot by mentioning `@claude` in:

- Issue descriptions or titles
- Issue comments
- Pull Request reviews
- Pull Request review comments

## Security Constraints

For security reasons, the bot will only execute if the user who triggered it has one of the following author associations with the repository:

- `OWNER`
- `MEMBER`
- `COLLABORATOR`

This prevents unauthorized execution and protects repository secrets.

## Configuration

To make the workflow function correctly, you must set the following repository secret:

- `CLAUDE_CODE_OAUTH_TOKEN`: The OAuth token required to authenticate and run the Claude Code action.
