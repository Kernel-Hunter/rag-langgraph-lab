# Security policy

Vesper reads and edits files and can run commands, so security reports matter a lot here.

## Reporting a problem

Please report vulnerabilities privately through GitHub:
[Report a vulnerability](https://github.com/Kernel-Hunter/vesper/security/advisories/new).
Do not open a public issue.

Useful details: the version (`vesper --version`), your operating system, the steps to
reproduce, and what an attacker could do with it. You should get a reply within a week.

## What counts

- A way for the agent to read, write or run anything outside the folder it was opened in.
- A file edit or command that runs without approval in `ask` mode, or a command that runs
  without approval in `auto-edit` mode.
- Another program or web page reaching the desktop engine on 127.0.0.1.
- API keys leaking into the interface, logs or network traffic other than the provider's.
- Tampering with the install scripts or release downloads that the checksum check misses.

Prompt injection on its own (a file that talks the model into proposing a bad change) is a
known limit of every LLM agent. It is in scope when it gets past the approval prompt.

## Supported versions

Only the latest release gets fixes.
