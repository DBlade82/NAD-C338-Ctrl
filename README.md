# NAD C 338 Home Assistant integration

Network-only Home Assistant integration for the NAD C 338 amplifier. Google
Cast remains independent. This project is not affiliated with or endorsed by
NAD; its icon is an original generic cyberpunk op-amp illustration.

> **Release status:** M4 controls passed 99 automated tests and supervised
> hardware acceptance. Version `0.1.0-beta.2` is an experimental prerelease.
> HACS installation and update have not been verified.

## Features

- UI setup and host reconfiguration; automatic reconnect and unavailable state.
- Power on and standby, source selection, mute, and volume set and step.
- Status reporting for power, source, volume, mute, and available firmware.
- Optional software maximum volume and configurable volume increment (default
  2 dB). The actual dB value remains available even above the maximum.
- Eight tested sources: Stream, Wireless, TV, Phono, Coax1, Coax2, Opt1, Opt2.
  Selecting a source from standby wakes the amplifier. Volume commands in
  standby are ignored.

**Volume safety:** The maximum applies to integration commands and is checked
after wake, not to Cast, the remote, or other controllers. Audio may briefly
exceed it on wake. If communication fails or an operation is canceled after
wake, the amplifier may remain on and the limit may remain unverified. Lowering
the maximum alone does not immediately change the current volume.

## Installation and setup

The intended HACS ZIP is the attached `nad-c338.zip` release asset, not a
GitHub-generated source archive. HACS install and update using that asset
remain unverified.

For manual installation from a locally built ZIP or release asset:

1. Extract the ZIP contents into Home Assistant's
   `custom_components/nad_c338/` directory. The ZIP root contains
   `manifest.json`, Python modules, `strings.json`, `translations/`, `brand/`,
   `LICENSE`, and `README.md`; it does not contain a
   `custom_components/nad_c338/` parent directory.
2. Restart Home Assistant. Under **Settings > Devices & services**, add
   **NAD C 338** and enter the amplifier's hostname or IP address.
3. Use the integration options to configure the optional maximum and volume
   increment. Leave the maximum unset to use the device maximum.

The amplifier must be network reachable. Network standby must be enabled for
wake; ECO standby and the rear power switch can prevent network wake. Use
**Reconfigure** if the address changes. Host aliases can bypass duplicate
detection, so a stable DHCP reservation is recommended.

## Development packaging

After installing development dependencies, build the release asset locally:

```shell
uv run python scripts/package_release.py
```

The builder writes `dist/nad-c338.zip`, with integration files at the ZIP root,
and rejects a missing or unhydrated Git LFS icon. A locally built ZIP is not a
published release or evidence that HACS install or update works. GitHub source
archives can contain an LFS pointer instead of the icon.

## Development

Install Python 3.14.2 or newer, `uv`, Git LFS, and pre-commit. Git LFS tracks
the integration's PNG brand assets:

```shell
git lfs install
git lfs pull
pre-commit install
uv sync
uv run pytest
uv run ruff check custom_components tests
uv run ruff format --check custom_components tests
uv run mypy --explicit-package-bases custom_components/nad_c338 tests --ignore-missing-imports
```

See the [requirements](docs/requirements.md),
[system overview](docs/systemoverview.md),
[protocol research](docs/protocol-research.md), and
[master plan](docs/masterplan.md). Integration log messages use the
`[NAD-C338]` prefix; Home Assistant manages initial setup retry logging.

## License and trademarks

Copyright (C) NAD C 338 Home Assistant integration contributors.

The project code is licensed under the GNU General Public License, version 3
or (at your option) any later version (GPL-3.0-or-later). See [LICENSE](LICENSE)
for the version 3 license text. This license grants no trademark rights; NAD
and other product names are used only to identify the supported device.
