# NAD C 338 protocol research

## Status

Initial investigation and supervised M4 control acceptance are complete, with
limitations and follow-ups listed below. Findings describe one amplifier and
firmware version and must not be assumed to apply to every C 338 or firmware
release. No device address, network identifier, or local path is recorded here.

## Observed network behavior

- The device's web interface identified it as a NAD C 338. TCP port `30001`
  accepted connections and answered NAD-style queries. Port `50001` was refused
  during the earlier Home Assistant investigation.
- ASCII queries were sent without a terminator. Responses were observed as
  `Main.<field>=<value>` lines terminated by CRLF. One source query produced a
  line with a leading NUL byte; its cause and frequency remain unconfirmed.
- Queries for `Main.Power`, `Main.Volume`, `Main.Source`, and `Main.Version`
  returned values in standby. `Main.Power` returned `Off`, and the reported
  firmware version was `1.76`.
- An aggregate `Main?` query in standby included power, volume, source, model,
  firmware, and `Main.ControlStandby=On`. The meaning and effect of that setting
  have not been verified; it was not changed.
- Setting `Main.Power=On` woke the amplifier and a later power query returned
  `On`. Setting `Main.Power=Off` returned it to standby. During supervised M4
  testing, the quiet wake interval was about seven seconds; bounded readiness
  checks were added before source selection.
- With the amplifier on, setting and querying the volume succeeded at two
  nearby low-volume values differing by 0.5 dB. The user manually selected
  minimum and maximum, and network queries reported -80 dB and +12 dB,
  respectively. A front-panel or remote-control adjustment produced a sequence of
  0.5 dB updates within the low-volume range. Arbitrary rounding and all
  intermediate values were not tested.
- Source setting and subsequent queries succeeded for `Stream`, `Wireless`,
  `TV`, `Phono`, `Coax1`, `Coax2`, `Opt1`, and `Opt2`. This confirms these
  names, not that no other protocol values exist. The
  [owner's manual](https://www.manualslib.com/manual/1248412/Nad-C-338.html?page=6)
  documents the seven wired inputs; Wi-Fi and Bluetooth share a wireless
  indicator according to the
  [streaming section](https://www.manualslib.com/manual/1248412/Nad-C-338.html?page=13).
  The aggregate status query returned the current source, not a list of sources.
- Two control connections were open concurrently. A volume change sent on one
  connection produced a volume update on the other. A user-initiated Google
  Cast volume reduction was also observed on an open TCP connection. The user
  confirmed that Cast could adjust volume during the test.
- `Main.Mute?`, `Main.Mute=On`, and `Main.Mute=Off` responded. While on, four
  alternating mute and unmute commands were verified by later queries. One
  earlier mute query unexpectedly returned `Off` after an `On` response;
  its cause is unknown. A control connection observed multiple unsolicited and
  duplicate mute and volume updates during testing.
- Setting mute in standby returned `On` without waking the device, but mute
  was `Off` after the following wake-up. Pre-setting mute in standby is
  therefore not a verified way to guarantee silence during wake-up.
- A low-volume `Main.Volume` set in standby returned the requested value, but
  woke the device and a subsequent query reported the previous volume instead.
  A command response alone must not be treated as confirmation of the eventual
  device state.
- One persistent connection stopped responding to source commands after a
  wake-up. Reconnecting allowed status queries and subsequent control. The
  cause and reproducibility remain unknown. In a separate test a control
  connection remained open and received updates across a standby-to-on-to-
  standby transition while Cast was used.
- Another open control connection received power and 0.5 dB volume updates
  during user-operated front-panel or remote-control changes.
- After testing, queries confirmed standby, the original source, and the
  low volume used for the tests.

The standby volume initially reported a value above the requested test ceiling
despite a manual adjustment. After another manual adjustment while powered on,
the low value remained visible in standby. The reason for the earlier mismatch
is unknown; a standby reading alone must not be treated as proof that wake-up
will be silent.

## M4 supervised control validation

The M4 implementation passed 99 automated tests, lint, and mypy. Supervised
hardware acceptance covered wake and standby, all eight tested sources, mute
and unmute, absolute and step volume at or below -60 dB, volume commands
ignored in standby, source selection from standby, restoration of the original
state, and leaving the amplifier on.

In a separate supervised test, the configured maximum was lowered from -64 dB
to -70 dB. After wake, the device volume was confirmed clamped to -70 dB. The
test then restored -64 dB, source `Wireless`, mute off, and power on. The
observed quiet interval during wake was about seven seconds; bounded readiness
handling was added to account for it before source selection.

These results do not guarantee a safe or confirmed outcome if the connection is
lost or an operation is cancelled after wake: the amplifier may remain on and
the volume limit may be unverified. Replies are broadcast and have no
correlation IDs; delayed, duplicate, or unsolicited messages can make a
post-write observation ambiguous. The client does not blindly retry setters.
The accepted warning remains that audio may initially exceed the configured
limit before post-wake reduction takes effect.

## Existing implementations

- [ha-nadtcp](https://github.com/martonperei/ha-nadtcp) is a Home Assistant
  custom integration, marked unmaintained by its author and distributed under
  the MIT license. It uses YAML setup and depends on `nadtcp==0.2.0.dev2`.
  The integration was not run on the current Home Assistant release; its
  dependency has the Python 3.14 compatibility issue described below.
- [nadtcp](https://pypi.org/project/nadtcp/) is a Python library for the C 338.
  The published metadata identifies an MIT license. Inspection of the exact
  `0.2.0.dev2` source used by ha-nadtcp found NUL-byte removal and legacy
  asyncio `loop` arguments. The Python 3.14 runtime used by Home Assistant does
  not accept those arguments in `asyncio.wait_for` or `asyncio.sleep`; the
  dependency needs changes before it can be used there.
- [php-NADC338](https://github.com/KiboOst/php-NADC338) is an MIT-licensed
  PHP control library, not a Home Assistant integration. It was a source for
  the command and source names tested above.

No third-party source code has been copied into this repository.
The two libraries specify different upper volume ranges. The manually observed
device endpoints above provide stronger evidence for this tested unit.

## Constraints and implementation follow-ups

- Per the owner's manual, network standby maintains network connectivity, but
  [ECO standby](https://www.manualslib.com/manual/1248412/Nad-C-338.html?page=13)
  requires local or remote-control wake-up. The user accepts network standby as
  a prerequisite.
- The user accepts waking and then reducing volume with a warning about
  possible brief audio above the limit. Standby mute did not survive wake-up,
  and setting volume in standby woke the device without retaining the requested
  value. These tests do not establish a safe pre-wake volume mechanism.
- Handle unsolicited, duplicate, and NUL-containing messages; re-query state
  after commands and reconnect after failure. Since replies have no IDs, a
  query cannot prove that a reply is fresh or belongs to the preceding setter.
  Do not blindly retry setters after an ambiguous failure. Communication loss
  or cancellation after wake can leave power on and the volume limit
  unverified.
- No complete source list was returned by the aggregate query. The tested
  source names can be used as a model-specific list; whether another discovery
  mechanism exists remains unknown.
- Longer-term coexistence with Google Cast and exact command rounding remain
  release-test concerns.
