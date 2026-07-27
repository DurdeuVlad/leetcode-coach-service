---
name: Bug report
about: Something is broken or behaves wrong
title: "[bug] "
labels: bug
body:
  - type: markdown
    attributes:
      value: |
        Thanks for taking the time to file a bug. Before you continue:

        - **Security issue?** Stop. Do NOT use this template. See
          [SECURITY.md](../SECURITY.md) and use GitHub's private vulnerability
          reporting instead.
        - **A LeetCode/Telegram/Google API outage or rate-limit?** That's
          upstream, not this repo. Check the provider's status page first.
  - type: textarea
    id: what-happened
    attributes:
      label: What happened?
      description: Describe the unexpected behavior. Include the exact error message or log line if you have it.
      placeholder: "The /health endpoint returned 500 when..."
    validations:
      required: true
  - type: textarea
    id: expected
    attributes:
      label: What did you expect?
      description: What should have happened instead?
    validations:
      required: true
  - type: textarea
    id: repro
    attributes:
      label: Reproduction steps
      description: Minimal steps to trigger the bug. If you can't reproduce reliably, say so.
      placeholder: |
        1. `docker compose up`
        2. `curl localhost:8000/health`
        3. ...
    validations:
      required: true
  - type: input
    id: version
    attributes:
      label: Commit / version
      description: Git SHA, tag, or branch you're running on.
      placeholder: main @ a1b2c3d
    validations:
      required: true
  - type: textarea
    id: env
    attributes:
      label: Environment
      description: OS, Docker version, Python version, anything unusual. **Do NOT paste real secret values.**
      placeholder: |
        OS: Ubuntu 24.04
        Docker: 27.0
        Python: 3.12.4
        Deploy: homelab Coolify
  - type: dropdown
    id: scope
    attributes:
      label: Is this in scope for v1?
      options:
        - "Yes — core flow or integration"
        - "No — out-of-scope feature (see docs/architecture.md §12)"
        - "Not sure"
    validations:
      required: true
  - type: checkboxes
    id: checks
    attributes:
      label: Checklist
      options:
        - label: I checked existing issues and didn't find a duplicate
          required: true
        - label: This is not a security issue (those go through SECURITY.md)
          required: true
        - label: I'm not asking for an out-of-scope v1 feature (Celery, multi-user, web UI, Browserless)
          required: true
