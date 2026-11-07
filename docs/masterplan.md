# NAD C 338 integration master plan

## Goal and scope

Build a network-only Home Assistant custom integration for the NAD C 338,
installed through HACS and configured through the UI. The integration must
provide power, source, volume, and mute controls and status as specified in
[requirements](requirements.md). Google Cast must remain usable independently.

The current target is Home Assistant Core 2026.11.0 and later current releases.
Ongoing maintenance and compatibility testing are required; future compatibility
cannot be guaranteed.

Starting with the first installable version, assign a version in the manifest,
create a corresponding Git tag, and publish a GitHub Release for that tag.
This applies as soon as an installable version exists, even before M5; releases
not ready for general use can be marked as prereleases.

## Current status

M1 through M4 are complete. M4 controls passed supervised hardware acceptance.
Version `1.1.0` adds optional Chromecast cover art. Marketplace installation of beta.2
succeeded; update and installation through older HACS versions remain unverified.
Brightness tests pass against a simulator and through the integration client
on the amplifier; Home Assistant UI testing on the device remains. See
[protocol research](protocol-research.md) and
[system overview](systemoverview.md) for evidence and limitations.

## Milestones

### M1: Verify device and protocol

**Status:** Done

- Evaluate the existing
  [ha-nadtcp integration](https://github.com/martonperei/ha-nadtcp) and
  [C 338 control library](https://github.com/KiboOst/php-NADC338), including
  maintenance, dependencies, compatibility, and licenses.
- Verify network communication on the target device, including standby and
  recovery. TCP port 30001 responded, including after reconnecting.
- Verify source names, power and source changes, mute control and status,
  volume bounds and resolution, firmware reporting, unsolicited updates, and
  simultaneous use with Cast.
- Determine whether wake-up can apply a volume limit before audible output;
  document the unverified guarantee and the user's accepted risk.
- Sanitize captured responses and examples before saving them; never commit
  device addresses, local paths, credentials, or other private data.

**Exit criteria:** A documented feasibility result for every required operation
and a tested protocol approach. If a requirement cannot be met over the existing
network connection, stop and agree on a change before implementation.

**Requirements:** FR-02 through FR-11, FR-14; PR-03, PR-05, PR-07.

### M2: Design and repository structure

**Status:** Done

- Decide whether to adapt an existing implementation or build on a compatible
  library, preserving license notices where required.
- Define integration domain, transport ownership, status synchronization,
  reconnect behavior, availability, error reporting, and command sequencing.
- Define the volume mapping between dB and Home Assistant's normalized volume,
  supported rounding, clamping, and the 2 dB default step.
- Establish the HACS custom integration structure, UI configuration and options,
  English translations, and architecture documentation.
- Specify safe handling of source selection while waking, failed wake-up, and
  changes made by external controllers.

**Exit criteria:** Design is traceable to requirements, unresolved protocol
constraints are documented, and repository layout matches
[HACS integration requirements](https://www.hacs.xyz/docs/publish/integration/).

**Requirements:** FR-01 through FR-14; PR-04 through PR-07.

### M3: Implement UI setup and device status

**Status:** Done

- Implement UI setup and settings, including connection validation and
  duplicate-device handling.
- Implement network communication and report power, source, volume, mute, and
  available firmware information. Reflect changes from other controllers.
- Report unavailable or unknown states honestly and recover after disconnects.

**Exit criteria:** Focused automated tests pass for configuration, status,
disconnects, and recovery; status is verified on the target amplifier.

**Requirements:** FR-01, FR-04 through FR-07, FR-10 through FR-14; PR-05, PR-06.

### M4: Implement controls and volume safeguards

**Status:** Done.

- Implement wake, standby, source selection, mute and unmute, and volume set and
  step commands.
- Ignore volume commands in standby. Source selection from standby wakes the
  amplifier, waits until ready, applies any required volume reduction, and then
  selects the source. Report failures rather than claiming success.
- Apply the configured software maximum to commands; lowering the setting alone
  does not alter current volume. On wake, reduce restored volume when above the
  configured maximum.
- Implement the default 2 dB step and configurable increments. Test rounding,
  boundaries, and step changes.
- Keep integration messages identifiable and suppress repeated warnings during
  an outage, including repeated failed or unstable reconnects.

**Exit criteria:** Automated and device tests cover the eight tested sources, mute and unmute,
standby transitions, wake failures, external changes, volume limits, and
disconnections. Any unavoidable wake-up volume risk is documented before use.

**Validation evidence:** 99 automated tests, lint, and mypy passed. Supervised
hardware acceptance covered wake, standby, all eight tested sources,
mute/unmute, absolute and step volume at or below -60 dB, ignored standby
volume commands, source selection from standby, restoration of the original
state, and leaving the amplifier on. A separate supervised test lowered the
limit from -64 dB to -70 dB, woke the amplifier and confirmed the post-wake
volume was clamped to -70 dB, then restored -64 dB, source `Wireless`, mute
off, and power on. The source-list scope and wake limitations remain as stated
in protocol research.

**Requirements:** FR-02, FR-03, FR-08 through FR-11, FR-14 through FR-16; PR-05.

### M5: Package, validate, and release

**Status:** In progress. Marketplace installation of beta.2 succeeded.
Update and HACS-specific installation remain unverified.

- Verify the local HACS metadata and ZIP builder; complete installation and
  setup instructions, troubleshooting, architecture overview, and
  requirement-to-implementation traceability.
- Run Home Assistant and HACS validation, focused tests, linting, and relevant
  compatibility checks against current Home Assistant releases. Verify HACS
  delivery of the attached ZIP and its Git LFS brand icon.
- Use sanitized protocol fixtures or a simulated device for automated tests;
  perform a separate real-device acceptance test before release.
- Review staged changes for secrets and local paths, run pre-commit checks,
  and publish a versioned GitHub Release with a corresponding Git tag after
  verification.

**Exit criteria:** HACS installation and update work, all mandatory acceptance
criteria pass on the target device, and release documentation records tested
versions and known limitations.

**Requirements:** FR-01 through FR-14; PR-01 through PR-08.

### M6: Display brightness

**Status:** In progress. All four levels were verified by integration client
tests on the target amplifier, both on and in standby, and by simulated Home
Assistant tests. The Home Assistant UI on the device remains unverified.

- Establish whether network control supports display brightness, its available
  levels including off, and whether the current level can be queried.
- Choose an appropriate Home Assistant control and implement supported levels
  without inventing state when the device cannot report it.
- Test supported levels and display-off behavior with a simulator and, with
  authorization, the target amplifier.

**Exit criteria:** Supported display brightness levels, including off, can be
selected through Home Assistant and verified on the target device.

**Requirement:** FR-17.

### M7: Chromecast cover art

**Status:** In progress. Optional Cast entity selection and cover updates are
implemented and tested with simulated Home Assistant states; actual Cast and
Home Assistant UI validation remain.

**Requirement:** FR-18.

## Risks and decisions

- Network control and eight source names have been observed on the target
  device. A source-list discovery mechanism and some protocol details remain
  unverified; see the implementation follow-ups in the protocol research.
- The target amplifier accepted all four brightness values while on and in
  standby. Other firmware may behave differently, including unexpected wake.
- The software volume limit does not constrain Cast, the remote, or other
  controllers. Wake-up may briefly restore a loud volume before reduction.
  Tested standby mute did not persist across wake-up, and a standby volume
  command woke the amplifier without retaining the requested value.
- Reusing old integrations may require significant Home Assistant API updates.
  The legacy dependency uses asyncio arguments unavailable in Python 3.14.
  Review third-party licenses before copying or adapting code.
- GitHub source archives may contain a Git LFS pointer instead of the icon.
  The ZIP builder rejects an unhydrated icon; the intended HACS asset is the
  attached release ZIP, but its delivery and install/update remain to be tested.
- The project uses an original generic cyberpunk op-amp icon and is not
  affiliated with NAD. GPL-3.0-or-later does not grant trademark rights.
- Marketplace prerelease selection and beta.2 installation succeeded; update
  and HACS-specific installation remain unverified.
- Automated secret scanning and ignore rules reduce risk but do not guarantee
  that secrets or local paths are absent; staged changes require review.

## References

- [Home Assistant integration development](https://developers.home-assistant.io/docs/creating_component_index/)
- [Home Assistant UI config flows](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/config-flow/)
- [HACS integration requirements](https://www.hacs.xyz/docs/publish/integration/)
