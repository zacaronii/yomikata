# The regression check

`yomikata regress` reads every merged entry's bar screens again with the catalog's current scripts, the reader
runtime and `lib/`, and says what changed since the last night. It runs on the server that holds the games and the
harness's snaps, never in CI (which has neither), and it writes only names, numbers and hashes: never the game's
text.

## What it reads

A **bar screen** is a harness snap whose `.json` has rows read off its picture (`ocr`, written by `snap_lines`):
what a bar was measured on. Each night:

1. **Keep.** The bar screens of every game the catalog has a script for (the game named in the snap's `.json`,
   identified as `identify` does) are copied, whole, into scratch's `regress/keep/<id>/`, with their `.json`,
   regions and picture. One already kept is never written over. The harness never writes there (a snap's name
   is a plain name in `snaps/`), so a snap written later under the same name, or `snaps/` cleared, can't take a
   bar away: **the kept folder is what the check reads, and what must be kept** (about 1 GB for 27 games).
2. **Read again.** Each kept snap is read as `compare` with `reread` reads one: the script ticked 40 frames over
   the still snap, its regions beside it, the cartridge from the game's file where the platform has one. Its
   agreement with the rows read off the picture, and the hash and length of what it said, are its reading. A bar
   snap kept with a **sequence** is read as in play instead (below).
3. **Judge.** Against `regress/baseline.json` (the first reading of each snap):
   - a screen that agreed (80% or more) and no longer does, or over which the script no longer loads, has
     **fallen**: it stays out of the baseline and is reported every night until fixed or accepted;
   - a reading that changed but agrees as well as before is **changed**, noted, and becomes the baseline;
   - a snap kept for the first time is **new**.

`--accept <id>` takes an entry's fallen readings as its baseline, once they have been looked at.

## Sequences: a script that says a page as it types

Some scripts say a page only once they have watched it type: its typing position moving, then standing (Kaeru
no Tame ni Kane wa Naru), each character drawn in turn (a Super Famicom game that draws its window a character
at a time), a pointer set while it types and cleared at its end (a Dreamcast game). Over a still snap nothing
moves, and they say nothing. Their entries say `"regress": "sequence"`, and each bar snap is taken with the
snaps across its page's typing, named `<bar>-seq-00`, `-01`, …. The harness takes them with the bar snaps:
`said: {"after": 2, "before": [12, 8, 4, 2]}` on `steps` or `replay` keeps memory those frames before each
saying and writes it as the saying's snap's sequence; or a `steps` wait with `every` and `snap: "<bar>-seq"`
takes one every frame or few (as many as come to 512 MB of memory in a call: frame by frame on a console with
little memory); `replay`'s `marks` place snaps at given frames of a recorded route. The check
keeps the group with its bar, and reads it as a run: one reader, the group's snaps one tick each, then the bar
snap held 40 ticks; what is judged is the last page it says. Still offline, with no core: only memory, a frame
at a time. A sequence entry's bar kept still is named in the report, to be taken again with its sequence.

How many snaps a page needs is the script's: every frame where it follows each character drawn, a couple (one
while it types, one after) where it follows a pointer. A snap kept as a difference from the group's first is
small: memory changes little across a page's typing.

## What it reports

`regress/<date>.md` (and the same on stdout): per game, its screens, how many agree, how many changed or fell;
then each fallen and changed snap by name with its agreement before and after; the games whose scripts read
nothing over a still snap; the screens read as sequences, and a sequence entry's screens still kept still; bar
snaps that can't be read (kept as a difference from a snap that is gone); and games with bar snaps but no
merged script. The exit status is 1 when anything
fell, 0 otherwise; with `--summary <file>` a fall also writes one line of counts and snap names there.

## Limits

A script reads a still snap only as well as the snap lets it. Some read nothing at all over one, and the
report lists them: a Game Boy or LoROM Super Famicom script that checks its build through the core's map of the
cartridge (a snap keeps memory, and gives the cartridge only where docs/script-api.md places it), and snaps
taken before the regions their script reads were kept. Scripts that say a page as it types read nothing over
a still snap: their bars are kept with sequences (above). Over a screen that reads nothing a fall can't show,
but a change still does. The check holds the catalog to what it read before, not to what was said
live at the bar.

## The nightly job

`tools/scripts/regress-job.sh` is the Unraid User Scripts job: it brings a clone of the catalog at main up to
date (the one step with the network), runs `regress` in the tools image with `--network none`, and on a fall
sends one Unraid notification with the summary line. A clean night is silent. To install it:

```sh
mkdir -p /boot/config/plugins/user.scripts/scripts/yomikata-regress && \
curl -fsSL https://raw.githubusercontent.com/zacaronii/yomikata/main/tools/scripts/regress-job.sh \
  -o /boot/config/plugins/user.scripts/scripts/yomikata-regress/script && \
echo "Yomikata regression check" > /boot/config/plugins/user.scripts/scripts/yomikata-regress/description
```

then, in Settings → User Scripts, set its schedule to Custom `30 3 * * *` (or another hour). Its paths are at the
top of the script, overridable in the environment.
