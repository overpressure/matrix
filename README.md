# overpressure on Matrix

**TL;DR.** The settings of [overpressure](https://github.com/overpressure/overpressure)'s Matrix space
[#overpressure:matrix.org](https://matrix.to/#/#overpressure:matrix.org) and its rooms, as YAML in
[config/](config/), and the tool that makes the space match them. A change is a pull request; once it is
merged, the [Space workflow](.github/workflows/space.yml) applies it. Every day it brings the icons up to
brand's, and every Monday it checks that nobody changed the space by hand since. The
[Announce workflow](.github/workflows/announce.yml) posts each release and each new headset to the
Announcements room, once. Both run as the project's bot account.

## What it sets

For the space and every room: its name, topic, icon (from
[overpressure/brand](https://github.com/overpressure/brand)'s `matrix/`), address, join rule, history
visibility, guest access, power levels and the people's levels, its place and order in the space, and its
pinned text. It changes only what differs, so a second run changes nothing.

What it never does on its own, and only reports in the run's summary:

- **Create a room.** A room it can't find by its address, name or `also` may exist under another name, so a
  new one could be a duplicate. Only `--create` (the workflow run by hand with *create* ticked) creates it,
  after a dry run showed that nothing was found.
- **Lower someone.** People above 0 who aren't in the settings, such as a moderator raised by hand in Element,
  keep their level unless `people.yaml` sets `lower_unlisted: true`. Accounts at or above its own level it
  can't change at all.
- **Touch other pins or messages.** It never deletes or redacts anything, nor kicks or bans; pins people made
  stay, in their order.

It also leaves alone, and reports, an address another room holds, encryption once on, and rooms in the space
that the settings don't name. Moderation (bans, a moderation bot) is not part of it.

### The pinned texts

Each room's text is pinned exactly once. The tool marks its message (`org.overpressure.pin` with the room's
key) and finds it among the pins by that mark:

- a changed text is edited in place: the same message, shown as edited, still pinned where it was;
- further pinned copies of the text are unpinned (the messages stay in the timeline);
- a text pinned by another account is posted anew in its place, since Matrix only honours an edit by the
  message's own sender (the old one is unpinned, not deleted);
- an unpinned text is pinned again, at the top.

### The icons

An icon is set from its PNG's content: the room's icon carries the PNG's SHA-256, so a new drawing under the
same name is uploaded again and an unchanged one never is. They come from brand's `main` on GitHub, which
changes without this repository, so the workflow applies the icons, and only the icons, every day.

## Announcements

[announcements.yaml](config/announcements.yaml) says what the bot posts by itself, to the Announcements room:

- **Releases** of the repositories it names (overpressure) and of every headset's repository, betas too. A new
  headset's releases need no change here.
- **A new headset**: a public repository of the organization with a `headset.json` (codename and name), not a
  template. The post names the headset, links its repository and, when `rooms/` has a room by its codename,
  that room.

Nothing published or created before its `since` is announced, so the first run doesn't post the history. At most
`max_per_run` posts go out per run, oldest first; the rest follow in the next runs.

Each post carries a mark naming what it announced (`org.overpressure.announce`: `release:<owner/repo>:<tag>`
or `headset:<codename>`). The announcer reads the room's latest 1000 messages for those marks, so the room
itself remembers what was posted: nothing is posted twice, and there is no state file to keep. The posts'
words are [texts/release.md](config/texts/release.md) and [texts/headset.md](config/texts/headset.md), with
`$placeholders`; a line whose placeholder is empty is left out.

## The settings

| File | What |
| --- | --- |
| [config/space.yaml](config/space.yaml) | The server, where the icons come from, the space itself, and its rooms in their order |
| [config/defaults.yaml](config/defaults.yaml) | What every room has unless its own file says otherwise: join rule, history, guest access, power levels |
| [config/people.yaml](config/people.yaml) | Who is at which level (admin 100, moderator 50), the same in the space and every room, and whether others are lowered |
| [config/rooms/](config/rooms/) | One file per room: name, address, topic, icon, and what it does differently |
| [config/announcements.yaml](config/announcements.yaml) | What the bot announces: which releases, new headsets, from when, how many per run |
| [config/texts/](config/texts/) | The pinned texts and the announcements' words, in the small Markdown the tool renders (paragraphs, lists, bold, links) |

A room's file:

```yaml
name: Meta Quest 2                          # the room's name
alias: "#overpressure-hollywood:matrix.org" # its address
topic: >-
  The Meta Quest 2 on overpressure, ...
icon: hollywood                             # brand's matrix/hollywood.png
also: [quest2, quest, metaquest2]           # names it may have had, to find it before it has its address
pin: texts/welcome.md                       # optional: the pinned text
power_levels:                               # optional: merged over defaults.yaml's
  events_default: 25
people:                                     # optional: added to people.yaml's, in this room only
  "@someone:matrix.org": 25
```

Every file is checked strictly: an unknown key, a missing file, a room the space doesn't list or a level that
isn't one is an error naming the file (`python -m matrix_space --check`, which CI runs).

### A new headset's room

1. Its icon in overpressure/brand first: `matrix/<codename>.svg` and `.png`.
2. Here, `config/rooms/<codename>.yaml` (copy a headset's room), and its key in `config/space.yaml`'s `rooms`
   where it belongs in the order.
3. The pull request's CI checks the settings and that the icon exists. Merged, the workflow sets up a room
   made by hand, found by its address, else its name or `also`. A room it can't find is reported: run the
   workflow by hand with *create* ticked and *apply* unticked, check that the summary creates only that room,
   then again with both ticked. It creates the room and sets it up in the same run.

The headset's team members go into its room's `people:` at 25. Its repository is announced by itself once it is
public (its `headset.json` makes it a headset), and so are its releases.

## The workflows

[space.yml](.github/workflows/space.yml) runs the tool as the bot:

| When | What |
| --- | --- |
| A change to `config/` or the tool lands on `main` | Applies it; a room it can't find is only reported |
| Run by hand (Actions, Space, Run workflow) | A dry run; *apply* changes it, *create* also creates rooms it can't find |
| Every day | Applies the icons alone (`--only-icons`): nothing else is listed or changed |
| Every Monday | A dry run that fails when the space differs from the settings; it changes nothing |

[announce.yml](.github/workflows/announce.yml) posts the announcements:

| When | What |
| --- | --- |
| Every half hour | Posts what is new |
| A release workflow sends the event `announce` (`repository_dispatch`) | Posts it right away instead of within half an hour |
| Run by hand (Actions, Announce, Run workflow) | A dry run; *apply* posts |

Each run's summary lists what differed, changed or was posted. Runs of one workflow never overlap.

**Setting it up**, once:

1. **The bot account**, a Matrix account of the project's own (such as `@overpressure-bot:matrix.org`), not a
   person's: its token can be replaced without anyone losing their login, and the space's history shows what
   the bot did. An owner invites it into the space and every room and raises it to 100 there (Element: Room
   settings, Roles & Permissions). The owners stay at 100 themselves and list themselves in `people.yaml`.
2. **Its access token** (Element, signed in as the bot: Settings, Help & About, Access Token; then close that
   session without signing out, which would end the token) as the secret `MATRIX_ACCESS_TOKEN` of an
   environment `matrix` in this repository's settings, with deployments limited to `main`. A pull request's
   code never sees it.
3. **A first run of each workflow by hand** without *apply*: the summaries show what the settings would change
   and what would be announced.

## Running it locally

```sh
uv run python -m matrix_space --check            # the settings and the icons; no token
MATRIX_ACCESS_TOKEN=... uv run python -m matrix_space            # what differs, nothing changed
MATRIX_ACCESS_TOKEN=... uv run python -m matrix_space --apply    # change it
MATRIX_ACCESS_TOKEN=... uv run python -m matrix_space.announce   # what would be announced
uv run python -m unittest                        # the tests, against a fake server
```

`--create` creates the rooms it can't find, `--only-icons` lists and changes the icons alone, `--icons DIR`
takes the icons from a brand checkout's `matrix/`, `--room KEY=!id:server` says which existing room a room of
the settings is, `--keep-names` and `--skip-icons` leave names and icons alone, `--directory` also sets the
server's public room directory (matrix.org refuses most accounts), `--fail-on-drift` makes a dry run that
finds differences exit with 3. The token is never printed.

## Changes

By pull request, like every repository of the organization
([GOVERNANCE.md](https://github.com/overpressure/.github/blob/main/GOVERNANCE.md)); `core` reviews them.
