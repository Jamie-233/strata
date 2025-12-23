# Changelog

Notable changes to this project. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-11-12

### Fixed

- The duplicate-address rule skipped every address claimed twice by one device,
  which also hid a switch with two SVIs on the same address. It now allows
  exactly the self-reference that is normal — one management address plus one
  of the device's own interfaces, SVI or routed port — and reports every other
  repetition.

## [0.1.0] - 2026-09-07

- Initial release: YAML inventory with groups and defaults, Cisco IOS and
  Huawei VRP templates, semantic validation, and SSH push with backup, diff
  and rollback.
