# NAD C 338 Home Assistant integration requirements

## Scope

Create a Home Assistant integration for the NAD C 338 amplifier. Configuration
must use the Home Assistant user interface. Control must use the existing
network connection; additional hardware is out of scope.

Implementation locations identify the current code or packaging
configuration. `Open` and `In progress` mean the acceptance criteria are not
yet fully met.

## Functional requirements

| ID | Requirement | Acceptance criteria | Status | Implementation |
| --- | --- | --- | --- | --- |
| FR-01 | Configure the integration through the Home Assistant UI. | A user can add and configure the amplifier without editing configuration files. | Done | `config_flow.py` |
| FR-02 | Wake the amplifier from network standby. | A Home Assistant control wakes the amplifier over the network and its reported state changes to on. If its restored volume exceeds the configured maximum, the integration reduces it to that maximum after wake-up. The UI and documentation warn that audio may briefly exceed the limit before reduction. | Done | `client.py`, `media_player.py`, UI strings |
| FR-03 | Put the amplifier in standby. | A Home Assistant control puts the amplifier in standby and its reported state changes accordingly. | Done | `client.py`, `media_player.py` |
| FR-04 | Report the selected source. | Home Assistant displays the amplifier's current source, including changes made outside Home Assistant. | Done | `client.py`, `media_player.py` |
| FR-05 | Report the volume. | Home Assistant displays the current amplifier volume, including changes made outside Home Assistant. If the volume exceeds the configured maximum, the slider is capped but the actual dB value remains available. | Done | `client.py`, `media_player.py` |
| FR-06 | Report the power state. | Home Assistant distinguishes on from standby based on the amplifier's reported state. | Done | `client.py`, `media_player.py` |
| FR-07 | Report extended device information when available. | The firmware version and other available information are exposed; unavailable information is not fabricated. | Done | `client.py`, `media_player.py` |
| FR-08 | Set the volume. | A volume change in Home Assistant changes the amplifier volume and the reported value reflects the result. Volume commands received in standby are ignored and do not wake the amplifier. | Done | `client.py`, `media_player.py` |
| FR-09 | Select any amplifier source. | Every source offered by the amplifier can be selected in Home Assistant and the reported source reflects the result. Selecting a source in standby wakes the amplifier before applying the source. | In progress | `const.py`, `client.py`, `media_player.py` (eight tested sources supported; a complete discovery mechanism is unverified) |
| FR-10 | Configure an optional maximum volume in dB through the UI. | The default has no integration-imposed maximum. The slider scales to the effective maximum, and commands exceeding a configured limit are clamped to it. Lowering the limit does not immediately change the current volume; the limit applies to subsequent commands and after wake-up. | Done | `config_flow.py`, `client.py`, `media_player.py` |
| FR-11 | Configure the volume increment in dB through the UI. | Volume up and down use the configured increment. The default increment is 2 dB. | Done | `config_flow.py`, `client.py`, `media_player.py` |
| FR-12 | Distribute the integration through HACS. | Users can install and update the integration through HACS. | Open | `hacs.json`, `scripts/package_release.py` (implemented locally; HACS install/update unverified) |
| FR-13 | Support current Home Assistant releases. | The integration is tested against Home Assistant Core 2026.11.0 and maintained and tested against current releases. Compatibility with future breaking changes cannot be guaranteed in advance. | In progress | `pyproject.toml`, `tests/` (Core 2026.11.0 tested; ongoing maintenance remains) |
| FR-14 | Expose mute and unmute controls. | Home Assistant can mute and unmute the amplifier, and the reported mute state reflects the device. | Done | `client.py`, `media_player.py` |
| FR-15 | Avoid log spam when the amplifier is unreachable. | After an established connection is lost, log one warning per outage, not one per retry; do not report recovery until a full status poll succeeds. Home Assistant manages initial setup retry logging separately. | Done | `client.py` |
| FR-16 | Make integration log entries identifiable. | Every integration-authored log message includes `[NAD-C338]`; errors presented to Home Assistant also identify the integration. | Done | `client.py`, `__init__.py`, `media_player.py` |
| FR-17 | Set the amplifier display brightness. | Home Assistant can select the amplifier's available display brightness levels, including display off. | Open | TBD; protocol and supported levels require verification |

## Project requirements

| ID | Requirement | Acceptance criteria | Status | Implementation |
| --- | --- | --- | --- | --- |
| PR-01 | Use the project directory as the development repository and Git for source control. | The project is a Git repository with `origin` set to the [project remote](https://github.com/DBlade82/NAD-C338-Ctrl.git). | Done | Git configuration |
| PR-02 | Maintain a `.gitignore`. | Local environments, generated artifacts, and known local sensitive files are excluded without hiding project source. | Done | `.gitignore` |
| PR-03 | Keep secrets and local paths out of commits, including documentation. | Ignore rules and automated pre-commit checks are in place, and staged changes are reviewed for secrets and local paths before each commit. | Open | `.gitignore`, `.pre-commit-config.yaml` |
| PR-04 | Use US English throughout the repository, integration, source code, and documentation. | Project-authored text and identifiers use US English. | Open | TBD |
| PR-05 | Use only the existing network connection for amplifier control. | All required controls and status reporting work without additional hardware when the amplifier is network reachable. Network standby is required for network wake-up; ECO standby and the rear power switch can prevent it. | Done | `client.py`, `media_player.py`, supervised hardware acceptance |
| PR-06 | Follow Home Assistant integration repository best practices. | The repository and integration structure follow applicable Home Assistant conventions. | Open | TBD |
| PR-07 | An existing integration may be forked. | Existing implementations can be evaluated and reused in compliance with their licenses. | Open | TBD |
| PR-08 | Version and publish installable builds. | Starting with the first installable version, each installable version has a version number in the integration manifest, a corresponding Git tag, and a GitHub Release referencing that tag. | In progress | `manifest.json`, `pyproject.toml`, Git tags, GitHub Releases |

## Design traceability

The following links identify implementation and planned responsibilities.

| Requirements | Design and planned implementation |
| --- | --- |
| FR-01, FR-10, FR-11 | [Setup and identity](systemoverview.md#setup-and-identity); `config_flow.py` and UI strings. |
| FR-02, FR-03, FR-08, FR-09, FR-14 | [Volume and control behavior](systemoverview.md#volume-and-control-behavior); `client.py` and `media_player.py`. |
| FR-04 through FR-07 | [Protocol and state](systemoverview.md#protocol-and-state); `client.py` and `media_player.py`. |
| FR-12, PR-06 | [Validation and release](systemoverview.md#validation-and-release); `hacs.json`, `scripts/package_release.py`, and HACS installation/update validation. |
| FR-13 | [Validation and release](systemoverview.md#validation-and-release); compatibility testing and maintenance. |
| FR-15, FR-16 | [Logging and availability](systemoverview.md#logging-and-availability); `client.py` and Home Assistant error messages. |
| FR-17 | [Display brightness](systemoverview.md#display-brightness); protocol and Home Assistant control to be determined. |
| PR-01 through PR-04 | Git configuration, `.gitignore`, `.pre-commit-config.yaml`, and project documentation. |
| PR-05, PR-07 | [Implementation choice](systemoverview.md#implementation-choice) and [protocol research](protocol-research.md). |
| PR-08 | [Validation and release](systemoverview.md#validation-and-release); integration manifest, Git tags, and GitHub Releases. |

## Research and open questions

- The existing Google Cast integration controls volume. The user reports that
  the amplifier is reachable or wakeable through Google Cast while in standby.
  Whether Cast alone can provide every required amplifier function is unverified.
- A community-developed
  [C 338 control library](https://github.com/KiboOst/php-NADC338) reports a
  network protocol, firmware queries, power and volume control, and these
  source names: Stream, Wireless, TV, Phono, Coax1, Coax2, Opt1, and Opt2.
  This is a research lead, not a verified list of all sources or a guarantee
  that any particular firmware supports the protocol.
- The [ha-nadtcp integration](https://github.com/martonperei/ha-nadtcp) provides
  power, volume, and source controls, but uses YAML configuration and is marked
  unmaintained. Compatibility with current Home Assistant releases and the
  remaining requirements has not been verified.
- Initial tests confirmed C 338 control on TCP port 30001, including wake-up,
  standby, volume, and eight source names. Additional verification is tracked
  in [protocol research](protocol-research.md).
- Determine how to represent unavailable or temporarily unreachable devices
  without confusing an unknown state with standby.
- The tested device reported -80 dB and +12 dB at its manually selected minimum
  and maximum. An unset maximum means the device maximum, not an infinite output
  level. Confirm rounding behavior for arbitrary requested values during
  implementation.
- A supervised wake test confirmed post-wake clamping to a configured maximum.
  The maximum limits commands sent through this integration, not changes made
  by Cast, the remote, or other controllers. The device may briefly output
  above the limit before reduction. If communication is lost or an operation is
  cancelled after wake, the amplifier may remain on and the limit may be
  unverified.
