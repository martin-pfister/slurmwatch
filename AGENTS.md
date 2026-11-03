# Intended use

- The maintainer uses `slurmwatch` interactively to view job output in a terminal,
  not to pipe or redirect it elsewhere.
- Prioritize reliable job following and readable terminal behavior. Hardening
  redirected-output failures (for example, a full destination disk) is not a
  current requirement.
